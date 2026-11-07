"""Le immagini degli strumenti di Valve: icone nuove degli editor, con la data in cui il grafico le ha create.

I data miner aprono i pacchetti dei giochi per vedere icone e grafica nuove (Tyler McVicker, HLX Files #18: le icone
"asset industrial", "asset military", "kitchen", "Half-Life: Alyx" del 24 marzo 2026). Non serve scaricare i giochi:
SteamTracking pubblica già le immagini degli strumenti in chiaro (game/core/tools/images, circa 2.000 PNG per gioco),
e sono file pubblici. In più quasi tutte le icone portano dentro i dati di Photoshop (XMP) con il giorno in cui sono
state create: la differenza con il giorno in cui escono dice da quanto Valve ci lavorava.

Per ogni gioco si elencano con l'API di GitHub i commit che toccano la cartella delle immagini (dal 2024 la prima
volta, poi solo i nuovi), si confronta l'elenco dei file prima e dopo (2 chiamate per commit: la cartella e il suo
albero) e le immagini nuove si salvano nello stato `immagini:nuove`, con la data del commit e, letta dal PNG, quella
di creazione. Lo storico entra in silenzio; dopo, un'immagine nuova con un nome che le parole chiave considerano
rilevante (Half-Life, HLX, Xen...) manda un avviso.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

ROOT = "game/core/tools/images"
SINCE = "2024-01-01T00:00:00Z"
GAMES = ("cs2", "dota2", "deadlock")
IMG_RE = re.compile(r"\.(png|svg|jpe?g|gif)$", re.I)
STATE_LIST = "immagini:elenco:"         # gioco -> {"commit", "data", "files": {percorso: blob}}
STATE_NEW = "immagini:nuove"            # elenco delle immagini nuove
MAX_READ = 600                          # PNG letti per giro (i dati di creazione, pochi KB l'uno): il resto al giro dopo
MAX_BYTES = 400 * 1024
NOTIFY_MIN = 50
TAG_RE = re.compile(r"^(asset_[a-z0-9]+|[a-z0-9]+_asset|kitchen)\.png$", re.I)
XMP_DATE = re.compile(rb'(?:xmp:CreateDate|photoshop:DateCreated)\s*(?:=\s*"|>)\s*(\d{4}-\d{2}-\d{2}(?:T[0-9:.]+)?(?:[+-]\d{2}:\d{2}|Z)?)')
PNG_TIME = re.compile(rb"tIME(.{7})", re.S)


def created_at(data: bytes | None) -> str | None:
    """La data di creazione scritta dentro l'immagine (XMP di Photoshop), o None."""
    if not data:
        return None
    m = XMP_DATE.search(data)
    if m:
        return m.group(1).decode("ascii", "replace")
    return None


def _listing(client: Any, repo: str, ref: str) -> dict[str, str] | None:
    """{percorso: blob} delle immagini della cartella a quel commit; None se la cartella non c'è."""
    parent, name = ROOT.rsplit("/", 1)
    entries = client.get_json(f"/repos/{repo}/contents/{parent}", {"ref": ref}) or []
    sha = next((e.get("sha") for e in entries if isinstance(e, dict) and e.get("name") == name), None)
    if not sha:
        return None
    tree = client.get_json(f"/repos/{repo}/git/trees/{sha}", {"recursive": 1}) or {}
    return {f"{ROOT}/{t['path']}": t["sha"] for t in tree.get("tree", [])
            if t.get("type") == "blob" and IMG_RE.search(t.get("path", ""))}


def _commits(client: Any, repo: str, branch: str, since: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 11):
        data = client.get_json(f"/repos/{repo}/commits", {"sha": branch, "path": ROOT, "since": since,
                                                          "per_page": 100, "page": page})
        if not data:
            break
        out += data
        if len(data) < 100:
            break
    out.reverse()
    return out


def _raw_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(path)}"


def _load(db: Any, key: str, default: Any) -> Any:
    try:
        value = json.loads(db.get_state(key) or "null")
    except ValueError:
        return default
    return default if value is None else value


def scan(cfg: Any, db: Any, client: Any = None, notifiers: list | None = None) -> dict[str, int]:
    """Un giro: per ogni gioco i commit nuovi della cartella delle immagini. Ritorna quante immagini nuove per gioco."""
    from hlxwatch.repos import tracked_repos
    if client is None:
        from hlxwatch.sources.github_tracking import GitHubClient
        client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                              cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
    found: list[dict[str, Any]] = _load(db, STATE_NEW, [])
    stats: dict[str, int] = {}
    alerts: list[dict[str, Any]] = []
    for repo_cfg in tracked_repos(cfg, db):
        name, repo, branch = repo_cfg["name"], repo_cfg.get("repo"), repo_cfg.get("branch") or "master"
        if name not in GAMES or not repo:
            continue
        st = _load(db, STATE_LIST + name, None)
        history = st is None                       # primo giro: lo storico dal 2024, in silenzio
        commits = _commits(client, repo, branch, SINCE if history else st["data"])
        if not history:
            commits = [c for c in commits if c["sha"] != st["commit"] and c["commit"]["committer"]["date"] >= st["data"]]
        if history:
            # il punto di partenza: le immagini che c'erano prima del primo commit del periodo (o, se dal 2024 la
            # cartella non è mai cambiata, quelle di oggi)
            if not commits:
                files = _listing(client, repo, branch) or {}
            elif commits[0].get("parents"):
                files = _listing(client, repo, commits[0]["parents"][0]["sha"]) or {}
            else:
                files = {}
            st = {"commit": "", "data": SINCE, "files": files}
        n = 0
        for c in commits:
            current = _listing(client, repo, c["sha"])
            if current is None:
                continue
            date = c["commit"]["committer"]["date"]
            if history and not st["files"]:
                # la cartella non c'era prima (SteamTracking ha cominciato a seguire il gioco, come Deadlock ad agosto
                # 2024): il primo elenco è il punto di partenza, non 2.000 immagini nuove
                st = {"commit": c["sha"], "data": date, "files": current}
                continue
            for path, blob in sorted(current.items()):
                if path in st["files"]:
                    continue
                item = {"gioco": name, "percorso": path, "nome": path.rsplit("/", 1)[-1], "data": date,
                        "commit": c["sha"], "url": _raw_url(repo, c["sha"], path), "creata": None, "letta": False,
                        "repo": repo}
                found.append(item)
                n += 1
                if not history:
                    alerts.append(item)
            st = {"commit": c["sha"], "data": date, "files": current}
        db.set_state(STATE_LIST + name, json.dumps(st, ensure_ascii=False))
        stats[name] = n
        if n:
            log.info("[%s] immagini degli strumenti nuove: %d%s", name, n, " (storico)" if history else "")
    # la data di creazione dentro il PNG, poche per giro
    read = 0
    for item in found:
        if item.get("letta") or read >= MAX_READ:
            continue
        data = _raw_bytes(client, item)
        item["creata"], item["letta"] = created_at(data), True
        read += 1
    db.set_state(STATE_NEW, json.dumps(found, ensure_ascii=False))
    if alerts and notifiers:
        _notify(cfg, notifiers, alerts)
    return stats


def _raw_bytes(client: Any, item: dict[str, Any]) -> bytes | None:
    try:
        response = client._request(item["url"], stream=True)
    except Exception:
        return None
    if response is None:
        return None
    try:
        data = b""
        for chunk in response.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                return None
        return data
    finally:
        response.close()


def _notify(cfg: Any, notifiers: list, alerts: list[dict[str, Any]]) -> None:
    from hlxwatch.analyzers import KeywordMatcher
    from hlxwatch.notifiers import notify_text
    matcher = KeywordMatcher(cfg.keywords)
    hits = []
    for a in alerts:
        res = matcher.analyze_line(a["percorso"], file_path=a["percorso"])
        if res and res.score >= NOTIFY_MIN:
            hits.append(a)
    if not hits:
        return
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/")).rstrip("/")
    lines = [f"• {h['nome']} ({h['gioco']}): {h['url']}" for h in hits[:8]]
    notify_text(notifiers, "HLX Watch: immagini nuove negli strumenti di Valve",
                "🖼️ " + "\n".join(lines) + f"\n\nTutte: {site}/immagini.html")


def summary(db: Any, hlx_days: set[str] | None = None, limit: int = 400) -> dict[str, Any] | None:
    """Le immagini nuove, una per percorso (il primo gioco e il primo giorno), raggruppate per giorno di uscita."""
    from datetime import date
    from hlxwatch.sources.storia_file import near
    found = _load(db, STATE_NEW, [])
    if not found:
        return None
    by_path: dict[str, dict[str, Any]] = {}
    for it in sorted(found, key=lambda x: x["data"]):
        o = by_path.setdefault(it["percorso"], {**it, "giochi": []})
        if it["gioco"] not in o["giochi"]:
            o["giochi"].append(it["gioco"])
        if not o.get("creata") and it.get("creata"):
            o["creata"] = it["creata"]
    items = sorted(by_path.values(), key=lambda x: (x["data"], x["percorso"]), reverse=True)
    leads = []
    for o in items:
        o["giorno"] = o["data"][:10]
        o["cartella"] = o["percorso"][len(ROOT) + 1:].rsplit("/", 1)[0] if "/" in o["percorso"][len(ROOT) + 1:] else ""
        o["anticipo"] = None
        if o.get("creata"):
            try:
                o["anticipo"] = (date.fromisoformat(o["giorno"]) - date.fromisoformat(o["creata"][:10])).days
                if o["anticipo"] >= 0:
                    leads.append(o["anticipo"])
            except ValueError:
                pass
    days: dict[str, dict[str, Any]] = {}
    for o in items[:limit]:
        d = days.setdefault(o["giorno"], {"giorno": o["giorno"], "immagini": [], "giochi": set(),
                                          "hlx": bool(hlx_days) and near(o["giorno"], hlx_days or set())})
        d["immagini"].append(o)
        d["giochi"] |= set(o["giochi"])
    for d in days.values():
        d["giochi"] = sorted(d["giochi"])
    leads.sort()
    # le etichette delle risorse: le icone delle categorie in cui Valve divide modelli e materiali nel browser delle
    # risorse (Half-Life: Alyx, industriale, militare, scienza, Xen, cucina...): dicono che tipo di oggetti si costruiscono
    tags = [o for o in items if TAG_RE.search(o["nome"])]
    return {"giorni": sorted(days.values(), key=lambda d: d["giorno"], reverse=True), "n": len(items),
            "etichette": sorted(tags, key=lambda o: o["data"], reverse=True),
            "con_data": sum(1 for o in items if o.get("creata")), "da": SINCE[:10],
            "anticipo_tipico": leads[len(leads) // 2] if leads else None,
            "giorni_hlx": sum(1 for d in days.values() if d["hlx"])}
