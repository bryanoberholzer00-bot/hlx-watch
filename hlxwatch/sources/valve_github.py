"""Repository pubblici di Valve su GitHub (organizzazione ValveSoftware).

Valve pubblica lì una parte del suo codice: Proton, SteamOS, gamescope, GameNetworkingSockets, OpenVR, gli SDK.
Non sono i file dei giochi, ma è codice scritto da Valve, e un nome di progetto può scappare in un commit
(un define, un controller, un percorso). Qui, a ogni run:
  - si elencano i repository dell'organizzazione (una o due chiamate API): uno nuovo diventa una voce "new_repo";
    se il nome o la descrizione parlano di un Half-Life nuovo è un possibile annuncio;
  - per i repository con un push dopo l'ultimo controllo (`pushed_at`) si leggono i commit nuovi del ramo principale
    e si valuta ogni riga del messaggio: una riga sopra `min_score` con le parole chiave, o che parla di un Half-Life
    nuovo (la stessa regola dei post di Steam), diventa una voce "commit".
Il primo giro registra lo stato e basta. Provato su 813 commit di un anno (19 repository): nessuna voce.

App Steam nel codice (`appid_files`): il file `proton` di Proton elenca le app con una correzione dedicata (187 a fine
ottobre 2026, circa 45 nuove in un anno). Quando il repository riceve un push si rilegge il file; ogni numero nuovo si
controlla sul negozio Steam (api/appdetails, pubblica): un'app non pubblica o di Valve è una voce sopra la soglia, un
gioco già in vendita di un altro editore resta in archivio con pochi punti.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import timedelta
from typing import Any

import requests

from hlxwatch import __version__, announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.steam_news import new_half_life
from hlxwatch.storage import Database
from hlxwatch.storage.db import parse_iso, utcnow

log = logging.getLogger(__name__)

GAME = "valvesoftware"
KNOWN_KEY = "valvegh:known"
PUSHED_KEY = "valvegh:pushed"
SEEN_KEY = "valvegh:seen"          # sha dei commit già letti (un push può riportare commit con date vecchie)
KEEP_SEEN = 1000
MARGIN = timedelta(days=2)         # i commit si chiedono da un po' prima dell'ultimo push: le date dei commit mentono
APPID_RE = re.compile(r"""["'](\d{3,8})["']""")
STORE_API = "https://store.steampowered.com/api/appdetails"
MAX_LOOKUPS = 15                   # app controllate sul negozio a ogni run (le altre al run successivo)


def extract_appids(text: str) -> set[str]:
    """Numeri tra virgolette usati come app: chiavi e voci di elenchi o confronti (== "754530"), non i valori assegnati
    (MESA_EXTENSION_MAX_YEAR = "2003") né quelli dopo i due punti di un dizionario ("19900": "16")."""
    out: set[str] = set()
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        for m in APPID_RE.finditer(code):
            before = code[:m.start()].rstrip()
            if before.endswith(":") or (before.endswith("=") and not before.endswith(("==", "!="))):
                continue
            out.add(m.group(1))
    return out


class ValveGitHubSource:
    name = "valve_github"

    def __init__(self, cfg: Config, db: Database, client: Any):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.vcfg: dict[str, Any] = cfg.get("valve_github", {}) or {}
        self.org = str(self.vcfg.get("org", "ValveSoftware"))
        self.base_score = int(self.vcfg.get("base_score", 55))
        self.min_score = int(self.vcfg.get("min_score", 50))
        self.max_pages = int(self.vcfg.get("max_commit_pages", 3))
        self.appid_files: dict[str, list[str]] = {
            str(k): [str(p) for p in v] for k, v in (self.vcfg.get("appid_files") or {}).items()}
        self.score_private = int(self.vcfg.get("score_private_app", 70))
        self.score_valve = int(self.vcfg.get("score_valve_app", 80))
        self.score_public = int(self.vcfg.get("score_public_app", 20))
        self.matcher = KeywordMatcher(cfg.keywords)
        self.store = requests.Session()
        self.store.headers["User-Agent"] = f"hlx-watch/{__version__}"
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.vcfg.get("enabled", True))

    def list_repos(self) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for page in range(1, 6):
            data = self.client.get_json(f"/orgs/{self.org}/repos",
                                        {"per_page": 100, "page": page, "type": "public", "sort": "created"})
            if not data:
                break
            repos += data
            if len(data) < 100:
                break
        return repos

    # ------------------------------------------------------------------ run
    def run(self) -> list[Finding]:
        self.errors = []
        if not self.enabled:
            return []
        repos = self.list_repos()
        if not repos:
            raise RuntimeError(f"nessuna risposta da GitHub per l'organizzazione {self.org}")
        known = set(json.loads(self.db.get_state(KNOWN_KEY) or "[]"))
        pushed: dict[str, str] = json.loads(self.db.get_state(PUSHED_KEY) or "{}")
        seen: list[str] = json.loads(self.db.get_state(SEEN_KEY) or "[]")
        first = not known
        findings: list[Finding] = []
        if not first:
            for repo in sorted((r for r in repos if r["full_name"] not in known), key=lambda r: r.get("created_at", "")):
                findings.append(self._new_repo(repo))
        for repo in repos:
            name, now_pushed = repo["full_name"], str(repo.get("pushed_at") or "")
            before = pushed.get(name)
            # i file con le app hanno il loro stato: un errore lì non ferma la lettura dei commit, e viceversa
            for path in self.appid_files.get(name, []):
                try:
                    findings += self._appids(repo, path)
                except Exception as exc:
                    log.warning("[%s] %s non letto: %s", name, path, exc)
                    self.errors.append(f"{name}/{path}: {exc}")
            if not first and before and now_pushed > before:
                try:
                    findings += self._commits(repo, before, seen)
                except Exception as exc:          # un repository che non risponde non ferma gli altri: si riprova dopo
                    log.warning("[%s] commit non letti: %s", name, exc)
                    self.errors.append(f"{name}: {exc}")
                    continue
            pushed[name] = now_pushed
        self.db.set_state(KNOWN_KEY, json.dumps(sorted(known | {r["full_name"] for r in repos})))
        self.db.set_state(PUSHED_KEY, json.dumps(pushed, sort_keys=True))
        self.db.set_state(SEEN_KEY, json.dumps(seen[-KEEP_SEEN:]))
        if first:
            log.info("Valve su GitHub: %d repository di %s registrati, senza avvisi", len(repos), self.org)
        return findings

    # ------------------------------------------------------------------ repository nuovi
    def _new_repo(self, repo: dict[str, Any]) -> Finding:
        full_name, name = repo["full_name"], repo["name"]
        description = (repo.get("description") or "").strip()
        text = f"Nuovo repository {full_name}" + (f": {description}" if description else "")
        score, keywords = self.base_score, ["valve_github", "new_repo"]
        probe = f"{name.replace('-', ' ').replace('_', ' ')} {name} {description}"
        res = self.matcher.analyze_line(probe, file_path=full_name, change_type="added")
        if res:
            score = max(score, res.score + 20)
            keywords += res.keywords
        build = BuildInfo(game=GAME, source="github", ref=f"repo:{full_name}",
                          date=parse_iso(repo.get("created_at")) or utcnow(), url=repo.get("html_url"),
                          message=text[:500])
        finding = Finding(game=GAME, source="valve_github", file_path=full_name, change_type="new_repo", text=text,
                          score=min(score, 250), keywords=keywords, token=f"repo:{full_name.lower()}",
                          context=json.dumps({k: repo.get(k) for k in ("created_at", "description", "html_url")},
                                             ensure_ascii=False))
        finding.build = build
        if announcement.enabled(self.cfg) and announcement.looks_like_hlx(name, description):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("NUOVO repository di Valve %s (punteggio %d)", full_name, finding.score)
        return finding

    # ------------------------------------------------------------------ commit
    def _commits(self, repo: dict[str, Any], before: str, seen: list[str]) -> list[Finding]:
        since = (parse_iso(before) or utcnow()) - MARGIN
        branch = repo.get("default_branch") or "main"
        commits = self.client.list_commits(repo["full_name"], branch, since=since, max_pages=self.max_pages)
        findings: list[Finding] = []
        done = set(seen)
        for commit in commits:
            sha = commit.get("sha") or ""
            if not sha or sha in done:
                continue
            seen.append(sha)
            done.add(sha)
            finding = self._commit_finding(repo, commit)
            if finding is not None:
                findings.append(finding)
        log.info("[%s] %d commit letti, %d voci", repo["full_name"], len(commits), len(findings))
        return findings

    def _commit_finding(self, repo: dict[str, Any], commit: dict[str, Any]) -> Finding | None:
        message = ((commit.get("commit") or {}).get("message") or "").strip()
        if not message:
            return None
        lines = [line.strip() for line in message.splitlines() if line.strip()][:200]
        is_new_hl = new_half_life(lines[0], message)
        best, score, keywords = "", 0, ["valve_github"]
        for line in lines:
            res = self.matcher.analyze_line(line, file_path=repo["full_name"], change_type="added")
            if res and res.score >= self.min_score and res.score > score:
                best, score, keywords = line, res.score, ["valve_github", *res.keywords]
        if not best and not is_new_hl:
            return None
        if not best:
            best, score = next((ln for ln in lines if new_half_life(ln, ln)), lines[0]), max(self.min_score, 100)
        sha = commit["sha"]
        date = parse_iso(((commit.get("commit") or {}).get("committer") or {}).get("date")) or utcnow()
        url = commit.get("html_url") or f"https://github.com/{repo['full_name']}/commit/{sha}"
        build = BuildInfo(game=GAME, source="github", ref=f"commit:{repo['full_name']}@{sha}", date=date, url=url,
                          message=f"{repo['full_name']}: {lines[0][:300]}")
        finding = Finding(game=GAME, source="valve_github", file_path=f"{repo['full_name']} (commit {sha[:10]})",
                          change_type="commit", text=best[:500], score=min(score, 250), keywords=keywords,
                          context=f"messaggio del commit {sha[:10]} in {repo['full_name']}: {message[:400]}")
        finding.build = build
        if is_new_hl and announcement.enabled(self.cfg):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("[%s] commit di Valve segnalato: %s", repo["full_name"], best[:160])
        return finding

    # ------------------------------------------------------------------ app Steam nel codice
    def _appids(self, repo: dict[str, Any], path: str) -> list[Finding]:
        """Numeri di app nuovi nel file. La prima volta registra soltanto; poi lo rilegge a ogni push nuovo."""
        key = f"valvegh:appids:{repo['full_name']}:{path}"
        raw = self.db.get_state(key)
        pushed = str(repo.get("pushed_at") or "")
        if raw is not None and self.db.get_state(f"{key}:pushed") == pushed:
            return []
        branch = repo.get("default_branch") or "main"
        text = self.client.raw_file(repo["full_name"], branch, path, 2_000_000)
        if not text:
            raise RuntimeError(f"file {path} non letto")
        ids = sorted(extract_appids(text), key=int)
        if raw is None:
            self.db.set_state(key, json.dumps(ids))
            self.db.set_state(f"{key}:pushed", pushed)
            log.info("[%s] %s: %d app Steam registrate", repo["full_name"], path, len(ids))
            return []
        old = set(json.loads(raw))
        new = [i for i in ids if i not in old]
        findings: list[Finding] = []
        done: set[str] = set()
        for appid in new[:MAX_LOOKUPS]:          # le altre (e quelle con il negozio muto) al prossimo giro
            info = self.lookup(appid)
            if info is None:
                continue
            findings.append(self._app_finding(repo, path, appid, info))
            done.add(appid)
        self.db.set_state(key, json.dumps(sorted(old | done, key=int)))
        if len(done) == len(new):
            self.db.set_state(f"{key}:pushed", pushed)
        return findings

    def lookup(self, appid: str) -> dict[str, Any] | None:
        """Dati pubblici dell'app sul negozio; {} se l'app non è pubblica, None se il negozio non risponde."""
        try:
            r = self.store.get(STORE_API, params={"appids": appid, "l": "english"}, timeout=30)
            if r.status_code >= 400:
                return None
            entry = (r.json() or {}).get(str(appid)) or {}
        except (requests.RequestException, ValueError):
            return None
        finally:
            time.sleep(1.5)              # il negozio tollera circa 200 richieste ogni 5 minuti
        return (entry.get("data") or {}) if entry.get("success") else {}

    def _app_finding(self, repo: dict[str, Any], path: str, appid: str, info: dict[str, Any]) -> Finding:
        store = f"https://store.steampowered.com/app/{appid}/"
        if not info:
            score, what = self.score_private, f"app {appid} NON pubblica sul negozio"
        else:
            makers = [*(info.get("developers") or []), *(info.get("publishers") or [])]
            valve = any("valve" in str(m).lower() for m in makers)
            score = self.score_valve if valve else self.score_public
            what = f"{info.get('name') or appid} ({appid}{', Valve' if valve else ''})"
        project = repo["name"]
        text = f"{project}: correzione dedicata a {what}"
        build = BuildInfo(game=GAME, source="github", ref=f"appid:{repo['full_name']}:{appid}", date=utcnow(),
                          url=f"https://github.com/{repo['full_name']}/blob/{repo.get('default_branch') or 'main'}/{path}",
                          message=text[:500])
        finding = Finding(game=GAME, source="valve_github", file_path=f"{repo['full_name']}/{path}",
                          change_type="appid", text=text, score=score, keywords=["valve_github", "appid_new"],
                          token=f"appid:{project.lower()}:{appid}",
                          context=json.dumps({"appid": appid, "negozio": store,
                                              "pubblica": bool(info)}, ensure_ascii=False))
        finding.build = build
        if info and announcement.enabled(self.cfg) and announcement.looks_like_hlx(info.get("name")):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        (log.warning if score >= 50 else log.info)("[%s] %s", repo["full_name"], text)
        return finding
