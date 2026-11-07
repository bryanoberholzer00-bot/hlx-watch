"""Storico dei file condivisi dei personaggi (game/core/ai_*.fgd) dal 2024, una volta sola.

Questi file sono "sorvegliati": ogni riga aggiunta o tolta diventa una voce (watched_files nel config). Ma lo storico
di HLX Watch parte da quando è stato acceso, e i cambiamenti di prima mancavano: per esempio le parti dei veicoli
colpibili (HITGROUP_CHASSIS, HITGROUP_DOOR_REAR_LEFT, HITGROUP_HEADLIGHT_LEFT...) arrivate in Dota 2 il 24 marzo 2026.
Qui, per ogni file e gioco, si elencano con l'API di GitHub i commit che lo hanno toccato da SINCE, si scaricano le
versioni (raw.githubusercontent.com, file piccoli) e si confrontano a coppie. Ogni riga aggiunta o tolta entra
nell'archivio con la data del suo commit, come fa l'archeologia: niente notifiche, niente "ultime scoperte".
I commit già nell'archivio non vengono ripetuti. Fatto un gioco, lo stato storia_file:<gioco> lo segna.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage.db import parse_iso

log = logging.getLogger(__name__)

SINCE = "2024-01-01T00:00:00Z"
FOLDER = "game/core"
FILE_RE = re.compile(r"^ai_[a-z0-9_]+\.fgd$")
STATE = "storia_file:"
MAX_BYTES = 4 * 1024 * 1024
MAX_PER_COMMIT = 400
CONTEXT = "storia dei file dei personaggi (recuperata dopo)"


def changed_lines(old: str, new: str, ignore: list[re.Pattern[str]]) -> list[tuple[str, str]]:
    """Righe aggiunte ("+") e tolte ("-") tra due versioni, senza le righe solo spostate o riformattate."""
    from hlxwatch.sources.github_tracking import TRIVIAL_LINE, format_key
    old_lines = [ln.strip() for ln in old.splitlines() if ln.strip()]
    new_lines = [ln.strip() for ln in new.splitlines() if ln.strip()]
    old_keys = {format_key(x) for x in old_lines}
    new_keys = {format_key(x) for x in new_lines}
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for sign, lines, other in (("+", new_lines, old_keys), ("-", old_lines, new_keys)):
        for text in lines:
            if format_key(text) in other or (sign, text) in seen or TRIVIAL_LINE.fullmatch(text):
                continue
            if any(x.search(text) for x in ignore):
                continue
            seen.add((sign, text))
            out.append((sign, text))
    return out


def _watched(cfg: Any, path: str) -> tuple[str, int, list[re.Pattern[str]]] | None:
    for w in cfg.get("watched_files", []) or []:
        if w.get("pattern") and re.search(str(w["pattern"]), path, re.I):
            return (str(w.get("title", w["pattern"])), int(w.get("score", 50)),
                    [re.compile(str(x)) for x in (w.get("ignore") or [])])
    return None


def _commits(client: Any, repo: str, branch: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 11):
        data = client.get_json(f"/repos/{repo}/commits", {"sha": branch, "path": path, "since": SINCE,
                                                          "per_page": 100, "page": page})
        if not data:
            break
        out += data
        if len(data) < 100:
            break
    out.reverse()                                   # dal più vecchio
    return out


def run(cfg: Any, db: Any, client: Any = None, names: tuple[str, ...] = ("cs2", "dota2", "deadlock"),
        force: bool = False) -> dict[str, dict[str, int]]:
    from hlxwatch.repos import tracked_repos
    from hlxwatch.sources.archaeology import LINE_KEY, recorded_lines
    if client is None:
        from hlxwatch.sources.github_tracking import GitHubClient
        client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                              cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
    stats: dict[str, dict[str, int]] = {}
    for repo_cfg in tracked_repos(cfg, db):
        name, repo, branch = repo_cfg["name"], repo_cfg.get("repo"), repo_cfg.get("branch") or "master"
        if name not in names or not repo or (db.get_state(STATE + name) == "fatto" and not force):
            continue
        st = stats.setdefault(name, {"file": 0, "commit": 0, "voci": 0})
        listing = client.get_json(f"/repos/{repo}/contents/{FOLDER}", {"ref": branch}) or []
        files = [f"{FOLDER}/{x['name']}" for x in listing if isinstance(x, dict) and FILE_RE.match(x.get("name", ""))]
        matcher = KeywordMatcher(cfg.keywords, ignore_ids=repo_cfg.get("ignore_keywords", []) or [])
        for path in files:
            watched = _watched(cfg, path)
            if not watched:
                continue
            st["file"] += 1
            commits = _commits(client, repo, branch, path)
            if not commits:
                continue
            parents = commits[0].get("parents") or []
            previous = client.raw_file(repo, parents[0]["sha"], path, MAX_BYTES) if parents else ""
            for c in commits:
                current = client.raw_file(repo, c["sha"], path, MAX_BYTES)
                if current is None:
                    continue
                lines = changed_lines(previous or "", current, watched[2])
                previous = current
                if not lines:
                    continue
                date = parse_iso(c["commit"]["committer"]["date"]) or datetime.now(timezone.utc)
                findings = []
                for sign, text in lines[:MAX_PER_COMMIT]:
                    change_type = "added" if sign == "+" else "removed"
                    res = matcher.analyze_line(text, file_path=path, change_type=change_type)
                    if res:
                        f = Finding(game=name, source="github", file_path=path, change_type=change_type,
                                    text=text[:1000], score=res.score, keywords=res.keywords, token=res.token, context=CONTEXT)
                    else:
                        f = Finding(game=name, source="github", file_path=path, change_type=change_type,
                                    text=text[:500], score=watched[1], keywords=["watched_file"],
                                    context=f"{CONTEXT}: {watched[0]}")
                    findings.append(f)
                existing = db.build_row(name, "github", c["sha"])
                if existing is not None:
                    build_id = int(existing["id"])
                    have = recorded_lines(db, build_id)
                    findings = [f for f in findings if (f.file_path, f.text[:LINE_KEY], f.change_type) not in have]
                else:
                    msg = (c["commit"].get("message") or "").splitlines()[0] if c["commit"].get("message") else ""
                    build_id = db.upsert_build(BuildInfo(game=name, source="github", ref=c["sha"], date=date,
                                                         url=f"https://github.com/{repo}/commit/{c['sha']}",
                                                         message=f"[storia] {msg}"[:500], files_changed=None))
                for f in findings:
                    db.add_finding(f, build_id, created_at=date)
                st["commit"] += 1
                st["voci"] += len(findings)
        db.set_state(STATE + name, "fatto")
        log.info("[%s] storia dei file dei personaggi: %d file, %d commit, %d voci", name, st["file"], st["commit"], st["voci"])
    db.conn.commit()
    return stats


def near(day: str, days: set[str], width: int = 1) -> bool:
    """Il giorno è a non più di `width` giorni da uno di `days` (date di commit e fusi orari spostano di un giorno)."""
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return False
    return any((d + timedelta(days=k)).isoformat() in days for k in range(-width, width + 1))


def overview(db: Any, hlx_days: set[str]) -> dict[str, Any] | None:
    """I giorni di cambiamento dei file dei personaggi dal 2024, quanti cadono vicino a una traccia HLX e quanti ne
    cadrebbero per caso (la parte di giorni del periodo che sta a un giorno da una traccia HLX)."""
    from datetime import date, timedelta
    days = summary(db, hlx_days, limit=1000)
    days = [d for d in days if d["giorno"] >= SINCE[:10]]
    if not days:
        return None
    start, end = date.fromisoformat(SINCE[:10]), date.fromisoformat(max(d["giorno"] for d in days))
    total = (end - start).days + 1
    covered = sum(1 for k in range(total) if near((start + timedelta(days=k)).isoformat(), hlx_days))
    hits = sum(1 for d in days if d["hlx"])
    return {"giorni": days[:30], "n": len(days), "vicini": hits, "attesi": round(len(days) * covered / total, 1),
            "dal": SINCE[:10]}


def summary(db: Any, hlx_days: set[str] | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """I giorni in cui i file dei personaggi hanno ricevuto righe nuove (prima comparsa in qualunque gioco), con
    qualche esempio e se entro un giorno è comparsa una traccia HLX."""
    first: dict[str, tuple[str, str]] = {}
    for text, when, game in db.conn.execute(
            "SELECT text, created_at, game FROM findings WHERE file_path LIKE 'game/core/ai\\_%.fgd' ESCAPE '\\' "
            "AND change_type IN ('added', 'present', 'baseline') ORDER BY created_at"):
        key = " ".join((text or "").split())
        if key and key not in first:
            first[key] = ((when or "")[:10], game)
    days: dict[str, dict[str, Any]] = {}
    for text, (day, game) in first.items():
        d = days.setdefault(day, {"giorno": day, "righe": 0, "esempi": [], "giochi": set()})
        d["righe"] += 1
        d["giochi"].add(game)
        if len(d["esempi"]) < 6:
            d["esempi"].append(text[:120])
    out = sorted(days.values(), key=lambda d: d["giorno"], reverse=True)[:limit]
    for d in out:
        d["giochi"] = sorted(d["giochi"])
        d["hlx"] = bool(hlx_days) and near(d["giorno"], hlx_days)
    return out
