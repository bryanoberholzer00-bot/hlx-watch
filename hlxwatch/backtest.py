"""Prova sul passato: HLX Watch avrebbe visto prima dell'annuncio i progetti Valve già usciti?

`progetti-storia` sa *quando* un nome in codice (citadel, deckard, neptune) è comparso nei file pubblici, perché
lo cerca sapendo già il nome. Qui invece si chiede se i rilevatori di oggi, che quel nome non lo conoscono,
avrebbero dato un avviso. Per ogni prova:
  1. si scarica il repository SteamTracking com'era il giorno prima della finestra (un commit solo, con i
     contenuti) e la scansione completa impara in silenzio quello che c'era, come al primo giro vero;
  2. si scaricano i commit della finestra (solo gli alberi; `git diff` prende i contenuti che servono) e ogni
     commit passa dagli stessi controlli dei commit veri (parole chiave, nomi di progetto, localizzazione,
     file sorvegliati, nodi Pulse), in un archivio temporaneo separato da quello vero;
  3. un avviso è una voce nuova con punteggio almeno `notify_min_score`, come per le notifiche vere.
Per non barare, i nomi del progetto (`codes`) si tolgono dai nomi noti del config e dalle regole dei file
sorvegliati: sono stati aggiunti dopo, sapendo la risposta. Il risultato dice per ogni prova se e quando è
arrivato un avviso sul progetto, con quanto anticipo sull'annuncio, e quanti altri avvisi sono arrivati nello
stesso periodo (il "rumore" che avresti ricevuto). Non si invia niente e l'archivio vero non si tocca.
Restano fuori le fonti che non hanno storia (pagine Steam, certificati, cartelle lette con l'API).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.class_families import GREP as CLASS_GREP, SCHEMA_FILE, family
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.bigcommit import _remove
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.sources.github_tracking import GitHubClient, GitHubTrackingSource
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso

log = logging.getLogger(__name__)

SEED = Path(__file__).with_name("backtest_seed.json")
CLASS_TOKEN = re.compile(CLASS_GREP)                # come il git grep del rilevatore

# ogni prova: una finestra di commit di un repository, attorno alla prima comparsa nota del nome in codice
# (date da progetti_seed.json). `codes`: espressione che riconosce le voci del progetto.
TESTS: dict[str, dict[str, Any]] = {
    "frame_2022": {"title": "Steam Frame: Deckard nei file di SteamVR", "project": "Steam Frame",
                   "announced": "2025-11-12", "repo": "steamvr", "start": "2022-03-01", "end": "2022-07-01",
                   "codes": r"deckard",
                   "nota": "Il 31 marzo 2022 entra nel driver dei sensori di SteamVR l'elenco dei dispositivi Valve, con "
                           "'Valve Deckard'. Fino al 4 novembre 2026 nessun rilevatore guardava lì; ora è un posto dei nomi "
                           "di progetto. Lo stesso giorno arrivano anche Index, VMinus1 e Unidentified: l'elenco compariva "
                           "per la prima volta, e oggi sono nomi già noti."},
    "frame_2025": {"title": "Steam Frame: i mesi prima dell'annuncio", "project": "Steam Frame",
                   "announced": "2025-11-12", "repo": "steamvr", "start": "2025-06-01", "end": "2025-11-12",
                   "codes": r"deckard|frame_(?:hmd|controller)|fremont|ibex|steam ?frame",
                   "nota": "In cinque mesi SteamTracking ha aggiornato SteamVR solo 10 volte. I file dei driver del Frame "
                           "li segue oggi una regola dei file sorvegliati, scritta dopo e quindi tolta dalla prova."},
    "deadlock_2021": {"title": "Deadlock: le prime classi Citadel in Dota 2", "project": "Deadlock",
                      "announced": "2024-08-23", "repo": "dota2", "start": "2021-03-01", "end": "2021-05-01",
                      "codes": r"citadel",
                      "nota": "Il 25 marzo 2021 compaiono in Dota 2 CCitadelModifierHandleTyped e CitadelHudEvent: una "
                              "classe e un evento con la stessa parola, abbastanza per un avviso medio sulla famiglia nuova "
                              "CCitadel. Fino al 4 novembre 2026 contavano solo i nomi con la C e l'avviso non arrivava: "
                              "la correzione è venuta da questa prova."},
    "deadlock_2022": {"title": "Deadlock: CCitadelUserCmdPB in Dota 2", "project": "Deadlock",
                      "announced": "2024-08-23", "repo": "dota2", "start": "2022-05-15", "end": "2022-07-01",
                      "codes": r"citadel",
                      "nota": "Il 9 giugno 2022 il file usercmd.proto di Dota 2 riceve CCitadelUserCmdPB insieme a "
                              "CHLXUserCmdPB: il messaggio di rete di un gioco nuovo, che il rilevatore dei nomi di progetto "
                              "segnala subito come nome mai visto."},
    "deck_2019": {"title": "Steam Deck: Neptune nel client di Steam", "project": "Steam Deck",
                  "announced": "2021-07-15", "repo": "steamclient", "start": "2019-11-01", "end": "2020-01-01",
                  "codes": r"neptune|steampal",
                  "nota": "Neptune compare il 19 novembre 2019 dentro un nome di variabile del client di Steam "
                          "(s_rgPropValuesGyroButtonNeptune): non è uno dei posti in cui si cercano i nomi di progetto, e "
                          "nel client di Steam non c'è niente che faccia scattare un avviso. Il segnale utile arriva "
                          "dopo: vedi la prova di maggio 2021."},
    "deck_2021": {"title": "Steam Deck: il tipo di controller Neptune", "project": "Steam Deck",
                  "announced": "2021-07-15", "repo": "steamclient", "start": "2021-05-01", "end": "2021-06-01",
                  "codes": r"neptune|steampal",
                  "nota": "L'11 maggio 2021 compare nel client di Steam l'elenco dei tipi di controller, con "
                          "k_eControllerType_SteamControllerNeptune: il controller dello Steam Deck, due mesi prima "
                          "dell'annuncio. Lo stesso giorno arrivano anche gli altri tipi (Xbox, PlayStation...), perché "
                          "l'elenco compariva per la prima volta; oggi sono già noti."},
}


def _day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def blind_config(cfg: Config, workdir: Path, codes: str) -> Config:
    """Copia del config con l'archivio nella cartella della prova e senza i nomi del progetto."""
    raw = copy.deepcopy(cfg.raw)
    code_re = re.compile(codes, re.IGNORECASE)
    general = raw.setdefault("general", {})
    general["data_dir"] = str(workdir)
    general["db_path"] = str(workdir / "backtest.sqlite")
    cn = raw.setdefault("codenames", {})
    cn["known"] = [n for n in (cn.get("known") or []) if not code_re.search(str(n))]
    raw["watched_files"] = [w for w in (raw.get("watched_files") or []) if not code_re.search(str(w.get("pattern", "")))]
    kw = raw.setdefault("keywords", {})
    # anche le parole chiave e le esclusioni scritte sapendo il nome (valve_hardware con deckard e neptune,
    # l'esclusione di "citadel"): nel 2021 nessuno le avrebbe avute
    for field in ("codenames", "global_excludes"):
        if kw.get(field):
            kw[field] = [c for c in kw[field] if not code_re.search(str(c))]
    kw["items"] = [i for i in (kw.get("items") or []) if not code_re.search(str(i.get("pattern", "")))]
    return Config(raw, cfg.path)


class _BaselineScan(FullScanSource):
    """La scansione completa vera, su una copia del repository ferma al commit di partenza."""

    def __init__(self, cfg: Config, db: Database, gitdir: Path, sha: str):
        super().__init__(cfg, db)
        self._gitdir, self._sha = gitdir, sha

    def update_mirror(self, repo_cfg: dict[str, Any]) -> tuple[Path, str]:
        return self._gitdir, self._sha


def _git(*args: str, timeout: int = 7200) -> str:
    proc = subprocess.run(["git", "-c", "core.quotePath=false", *args], capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[2] if len(args) > 2 else args[0]} fallito: "
                           f"{proc.stderr.decode('utf-8', 'replace')[-400:]}")
    return proc.stdout.decode("utf-8", "replace")


def commit_before(client: GitHubClient, repo: str, branch: str, when: datetime) -> tuple[str, datetime] | None:
    data = client.get_json(f"/repos/{repo}/commits", {"sha": branch, "until": iso(when), "per_page": 1})
    if not data:
        return None
    c = data[0]
    return c["sha"], parse_iso(c["commit"]["committer"]["date"])


def note_families(entry: dict[str, Any], day: str, first: dict[str, str]) -> None:
    """Il giorno in cui compare il primo nome di ogni famiglia di classi, negli stessi file che legge il rilevatore."""
    path = entry.get("filename") or ""
    names: list[str] = []
    m = SCHEMA_FILE.match(path)
    if m and entry.get("status") == "added":
        names.append(m.group("name"))
    elif path.endswith("_strings.txt") and entry.get("patch"):
        for line in entry["patch"].splitlines():
            if line.startswith("+"):
                names += CLASS_TOKEN.findall(line)
    for name in names:
        fam = family(name)
        if fam and fam not in first:
            first[fam] = day


def is_related(f: dict[str, Any], code_re: re.Pattern[str]) -> bool:
    return bool(code_re.search(" ".join(str(f.get(k) or "") for k in ("text", "file_path", "token", "context"))))


def run_test(cfg: Config, key: str, test: dict[str, Any], workdir: Path) -> dict[str, Any]:
    repo_cfg = next((r for r in cfg.github_repos if r["name"] == test["repo"]), None)
    if repo_cfg is None:
        raise ValueError(f"repository {test['repo']} non nel config")
    repo, branch = repo_cfg["repo"], repo_cfg.get("branch", "master")
    url = repo_cfg.get("clone_url") or f"https://github.com/{repo}.git"
    code_re = re.compile(test["codes"], re.IGNORECASE)
    root = workdir / key
    if root.exists():
        _remove(root)
    root.mkdir(parents=True)
    tcfg = blind_config(cfg, root, test["codes"])
    db = Database(tcfg.db_path)
    started = time.time()
    try:
        client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                              cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
        first = commit_before(client, repo, branch, _day(test["start"]))
        last = commit_before(client, repo, branch, _day(test["end"]))
        if not first or not last:
            raise RuntimeError("commit di partenza o di arrivo non trovato")
        start_sha, start_date = first
        end_sha, _ = last

        # 1) com'era il repository all'inizio: la scansione completa impara in silenzio
        base = root / "base.git"
        _git("init", "-q", "--bare", str(base))
        _git("-C", str(base), "fetch", "-q", "--depth", "1", url, start_sha)
        _git("-C", str(base), "update-ref", "HEAD", start_sha)
        scan = _BaselineScan(tcfg, db, base, start_sha)
        bid = db.upsert_build(BuildInfo(game=repo_cfg["name"], source="fullscan", ref=f"tree:{start_sha[:12]}",
                                        date=start_date, url=f"https://github.com/{repo}/tree/{start_sha}"))
        for f in scan.scan_repo(repo_cfg):
            db.add_finding(f, bid, created_at=start_date)
        scan.codenames.finish_learning()
        scan.pulse.finish()
        scan.families.finish()
        seen = json.loads(db.get_state("codenames:seen") or "{}")
        learned = sorted(n for n in seen if code_re.search(n))
        _remove(base)

        # 2) i commit della finestra, senza contenuti: git diff scarica quelli che servono
        hist = root / "hist.git"
        _git("init", "-q", "--bare", str(hist))
        since = (start_date - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _git("-C", str(hist), "fetch", "-q", "--filter=blob:none", f"--shallow-since={since}", url, end_sha)
        log_out = _git("-C", str(hist), "log", "--reverse", "--first-parent", "--format=%H%x1f%P%x1f%cI%x1f%s",
                       f"{start_sha}..{end_sha}")
        commits = [line.split("\x1f") for line in log_out.splitlines() if line.count("\x1f") == 3]
        src = GitHubTrackingSource(tcfg, db)
        matcher = KeywordMatcher(tcfg.keywords, ignore_ids=repo_cfg.get("ignore_keywords", []) or [])
        notify_min = int(cfg.get("general.notify_min_score", 50))
        alerts: list[dict[str, Any]] = []
        first_family: dict[str, str] = {}         # famiglia di classi -> giorno del primo nome (per datare il punto 3)

        def alert(f: Finding, day: str, sha: str) -> None:
            if f.is_new and f.score >= notify_min:
                alerts.append({"day": day, "sha": sha[:10], "score": f.score, "type": f.change_type,
                               "file_path": f.file_path, "text": (f.text or "")[:300], "token": f.token,
                               "keywords": list(f.keywords or []), "context": (f.context or "")[:200]})

        for i, (sha, parents, when, subject) in enumerate(commits, 1):
            parent = parents.split()[0] if parents else ""
            if not parent:
                continue
            date_c = parse_iso(when)
            found: list[Finding] = []
            for entry in src.local_diff.diff_entries(hist, parent, sha):
                found += src._entry_findings(repo_cfg, entry, matcher, None)
                note_families(entry, date_c.date().isoformat(), first_family)
            bid = db.upsert_build(BuildInfo(game=repo_cfg["name"], source="github", ref=sha, date=date_c,
                                            url=f"https://github.com/{repo}/commit/{sha}", message=subject[:500],
                                            files_changed=src.local_diff.files_total))
            for f in found:
                db.add_finding(f, bid, created_at=date_c)
                alert(f, date_c.date().isoformat(), sha)
            log.info("[prova %s] %d/%d %s %s: %d voci", key, i, len(commits), sha[:10], date_c.date(), len(found))
        _remove(hist)

        # 3) la scansione completa giornaliera, fatta una volta alla fine della finestra: famiglie di classi nuove
        # (CCitadel...) e righe che i commit non mostrano. Una famiglia prende il giorno del suo primo nome
        end = root / "end.git"
        _git("init", "-q", "--bare", str(end))
        _git("-C", str(end), "fetch", "-q", "--depth", "1", url, end_sha)
        _git("-C", str(end), "update-ref", "HEAD", end_sha)
        scan = _BaselineScan(tcfg, db, end, end_sha)
        end_found = scan.scan_repo(repo_cfg) + scan.families.finish() + scan.pulse.finish()
        end_date = parse_iso(_git("-C", str(end), "log", "-1", "--format=%cI", end_sha).strip())
        bid = db.upsert_build(BuildInfo(game=repo_cfg["name"], source="fullscan", ref=f"tree:{end_sha[:12]}",
                                        date=end_date, url=f"https://github.com/{repo}/tree/{end_sha}"))
        for f in end_found:
            if f.id is None:                   # famiglie e nodi Pulse si salvano già da soli
                db.add_finding(f, bid, created_at=end_date)
            fam = (f.token or "").split(":", 1)[1] if f.change_type == "class_family" else ""
            day = next((d for k, d in first_family.items() if k.lower() == fam), end_date.date().isoformat())
            alert(f, day, end_sha)
        _remove(end)
        alerts.sort(key=lambda a: a["day"])
    finally:
        db.close()

    announced = date.fromisoformat(test["announced"])
    related = [a for a in alerts if is_related(a, code_re)]
    for a in related:
        a["anticipo_giorni"] = (announced - date.fromisoformat(a["day"])).days
    months = max(1.0, (_day(test["end"]) - _day(test["start"])).days / 30.4)
    others = [a for a in alerts if not is_related(a, code_re)]
    return {
        "key": key, **{k: test[k] for k in ("title", "project", "announced", "repo", "start", "end", "codes")},
        "commits": len(commits), "start_sha": start_sha, "end_sha": end_sha,
        "learned_at_start": learned,
        "related": related[:50], "related_total": len(related),
        "first_related": related[0] if related else None,
        "alerts_total": len(alerts), "alerts_high": sum(1 for a in alerts if a["score"] >= 100),
        "others_per_month": round(len(others) / months, 1),
        "others_high_per_month": round(sum(1 for a in others if a["score"] >= 100) / months, 1),
        "others_sample": sorted(others, key=lambda a: -a["score"])[:40],
        "seconds": round(time.time() - started),
    }


def run(cfg: Config, keys: list[str] | None, workdir: Path, out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"tests": {}}
    for key, test in TESTS.items():
        if keys and key not in keys:
            continue
        log.info("Prova sul passato %s: %s (%s, %s -> %s)", key, test["title"], test["repo"], test["start"], test["end"])
        try:
            data["tests"][key] = run_test(cfg, key, test, workdir)
        except Exception as exc:
            log.exception("Prova %s non riuscita", key)
            data["tests"][key] = {"key": key, "title": test["title"], "error": str(exc)[:500]}
        data["generated"] = iso(datetime.now(timezone.utc))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if workdir.exists():
        _remove(workdir)
    return data


# ------------------------------------------------------------------ sito
KIND = {"codename": "nome di progetto mai visto", "localization": "testi di localizzazione nuovi",
        "pulse": "nodo Pulse nuovo", "structure": "cartella nuova", "class_family": "famiglia di classi nuova"}


def load_seed(path: Path = SEED) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tests": {}}


def kind(alert: dict[str, Any]) -> str:
    if alert.get("type") in KIND:
        return KIND[alert["type"]]
    if "watched_file" in (alert.get("keywords") or []) or "file sorvegliato" in (alert.get("context") or ""):
        return "riga di un file sorvegliato"
    return "parola chiave"


def summary(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """Le prove nell'ordine di TESTS, pronte per la pagina Metodologia."""
    rows = []
    for key in TESTS:
        r = (seed.get("tests") or {}).get(key)
        if not r or r.get("error"):
            continue
        first = r.get("first_related")
        rows.append({**r, "nota": TESTS[key].get("nota", ""), "first": first and {**first, "kind": kind(first),
                                               "mesi": round(first["anticipo_giorni"] / 30.4)}})
    return rows
