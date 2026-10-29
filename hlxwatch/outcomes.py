"""Registro degli esiti delle tracce HLX e fusioni del motore.

Nessun data miner misura quante delle sue tracce si avverano o si trasformano: si scrive "prendere con le pinze"
senza contare. Qui, per ogni traccia HLX dell'archivio (un nome o una riga, in tutti i giochi), si ricava l'esito:
  - **rinominata**: la riga HLX è ricomparsa con un altro prefisso (rilevatore delle rinomine); la destinazione
    dice dove è finita la tecnologia: motore comune (core, generic), Deadlock (citadel), un altro gioco, nessun prefisso;
  - **presente**: c'è ancora oggi nei file (scansione completa);
  - **sparita**: è stata tolta e non c'è più;
  - **da verificare**: vista nei commit, ma la scansione completa non la segue (punteggio sotto soglia).
Gli esiti che lo strumento non può ricavare da solo (una rinomina tra file diversi, una conferma di Valve) si
scrivono a mano nel config, sezione `esiti`.

**Fusione del motore**: la stessa riga compare in CS2, Dota 2 e Deadlock entro pochi giorni. Viaggia con il codice
condiviso di Source 2, quindi non indica lavoro nuovo su quel gioco: va letta con meno entusiasmo.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from hlxwatch.config import Config
from hlxwatch.evidence import area_of, evidence_of
from hlxwatch.storage import Database
from hlxwatch.timeutil import local_day

HLX_KEYWORDS = ("hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path")
MAIN_GAMES = ("cs2", "dota2", "deadlock")
MERGE_DAYS = 60
SEP = "\t"                         # separatore delle righe negli snapshot della scansione completa
PREFIX_PLACES = {"core": "motore comune", "generic": "motore comune", "citadel": "Deadlock", "dota": "Dota 2",
                 "csgo": "CS2", "cs2": "CS2", "hlvr": "Half-Life: Alyx", "": "nessun prefisso"}
STATUS_LABELS = {"rinominata": "rinominata", "presente": "presente oggi", "sparita": "sparita",
                 "da_verificare": "da verificare", "manuale": "esito scritto a mano"}


def norm(text: str | None) -> str:
    t = re.sub(r"^//\s*", "", (text or "").strip())
    return " ".join(re.sub(r"\d+", "#", t.lower()).split())[:160]


def trace_key(token: str | None, text: str | None) -> str:
    """Il token se dice qualcosa; "hlx" da solo (mod != hlx, "Thumper (HLX)", il bug reporter) no: lì conta la riga."""
    tok = (token or "").strip().lower()
    return tok if tok and tok != "hlx" else norm(text)


def _day(value: str | None) -> str | None:
    return local_day(value) if value else None


def _is_hlx(keywords: str) -> bool:
    return any(f'"{k}"' in (keywords or "") for k in HLX_KEYWORDS)


# ------------------------------------------------------------------ fusioni del motore
def engine_merge_index(db: Database) -> dict[str, dict[str, str]]:
    """chiave della traccia -> gioco -> primo giorno, solo per i tre giochi che ricevono le fusioni del motore."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    rows = db.conn.execute(
        "SELECT f.game, f.token, f.text, COALESCE(fs.commit_date, b.date, f.created_at) AS d FROM findings f "
        "LEFT JOIN builds b ON b.id = f.build_id LEFT JOIN first_seen fs ON fs.game = f.game AND fs.file_path = f.file_path "
        "AND fs.text = f.text WHERE f.game IN ('cs2', 'dota2', 'deadlock') "
        "AND f.change_type IN ('added', 'present', 'file_added', 'changed', 'pulse', 'renamed') "
        "AND f.file_path NOT LIKE '%.stringsignore'")
    for game, token, text, d in rows:
        day = _day(d)
        if not day:
            continue
        key = trace_key(token, text)
        if game not in out[key] or day < out[key][game]:
            out[key][game] = day
    return out


def merge_partners(index: dict[str, dict[str, str]], game: str, token: str | None, text: str | None,
                   day: str | None, titles: dict[str, str] | None = None) -> list[str]:
    """Gli altri giochi in cui la stessa riga è comparsa entro MERGE_DAYS giorni (vuoto se nessuno)."""
    if game not in MAIN_GAMES or not day:
        return []
    games = index.get(trace_key(token, text)) or {}
    try:
        here = date.fromisoformat(day[:10])
    except ValueError:
        return []
    out = []
    for other, when in sorted(games.items()):
        if other == game:
            continue
        try:
            if abs((date.fromisoformat(when[:10]) - here).days) <= MERGE_DAYS:
                out.append((titles or {}).get(other, other))
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------ registro degli esiti
def _present_now(db: Database) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for key, content in db.conn.execute("SELECT key, content FROM snapshots WHERE key LIKE 'fullscan:%'"):
        game = key.split(":", 1)[1]
        for line in (content or "").splitlines():
            out[game].add(norm(line.partition(SEP)[2]))
    return out


def destination(prefix: str) -> str:
    return PREFIX_PLACES.get(prefix.lower(), f"prefisso '{prefix}'")


def outcome_register(cfg: Config, db: Database) -> dict[str, Any]:
    present = _present_now(db)
    rows = db.conn.execute(
        "SELECT f.game, f.file_path, f.change_type, f.token, f.text, f.old_text, f.context, f.keywords, f.score, "
        "COALESCE(fs.commit_date, b.date, f.created_at) AS d, b.url AS url FROM findings f "
        "LEFT JOIN builds b ON b.id = f.build_id LEFT JOIN first_seen fs ON fs.game = f.game AND fs.file_path = f.file_path "
        "AND fs.text = f.text WHERE f.change_type NOT IN ('baseline', 'summary') AND f.file_path NOT LIKE '%.stringsignore'"
    ).fetchall()
    traces: dict[str, dict[str, Any]] = {}
    renames: dict[str, dict[str, Any]] = {}
    for r in rows:
        r = dict(r)
        if r["change_type"] == "renamed":
            prefix = ""
            m = re.search(r"diventato '([^']*)'", r.get("context") or "")
            if m:
                prefix = m.group(1)
            renames[norm(r.get("old_text"))] = {"new": r["text"], "where": destination(prefix), "day": _day(r["d"]),
                                                "game": r["game"], "url": r.get("url")}
            continue
        if not _is_hlx(r.get("keywords") or ""):
            continue
        key = trace_key(r.get("token"), r.get("text"))
        label = r.get("text") if (r.get("token") or "").lower() in ("", "hlx") else r.get("token")
        t = traces.setdefault(key, {"key": key, "label": (label or "").strip()[:120], "games": {},
                                    "texts": set(), "removed": {}, "added": {}, "file": r["file_path"], "url": r.get("url"),
                                    "score": 0})
        day = _day(r["d"])
        t["texts"].add(norm(r.get("text")))
        t["score"] = max(t["score"], int(r.get("score") or 0))
        if r["change_type"] in ("removed", "file_removed"):
            if day and (r["game"] not in t["removed"] or day > t["removed"][r["game"]]):
                t["removed"][r["game"]] = day
        elif day:
            if r["game"] not in t["games"] or day < t["games"][r["game"]]:
                t["games"][r["game"]] = day
                if day == min(t["games"].values()):
                    t["file"], t["url"] = r["file_path"], r.get("url") or t["url"]
            t["added"][r["game"]] = max(day, t["added"].get(r["game"], day))
    manual = {str(e.get("match", "")).lower(): e for e in ((cfg.get("esiti", {}) or {}).get("manuali") or []) if e.get("match")}
    out = []
    for t in traces.values():
        if not t["games"]:
            continue                # solo rimozioni senza la comparsa nell'archivio: niente da raccontare
        status, detail = "da_verificare", ""
        rename = next((renames[x] for x in t["texts"] if x in renames), None)
        here_now = [g for g in t["games"] if t["texts"] & present.get(g, set())]
        gone = [g for g, d in t["removed"].items() if d >= t["added"].get(g, "")]
        if rename:
            status, detail = "rinominata", f"→ {rename['new'][:90]} ({rename['where']}, {rename['day']})"
        elif here_now:
            status = "presente"
        elif gone and len(gone) == len(t["games"]):
            status, detail = "sparita", "tolta il " + max(t["removed"].values())
        for match, entry in manual.items():
            if match and (match in t["key"] or any(match in x for x in t["texts"])):
                status, detail = "manuale", str(entry.get("esito", "")) + (f": {entry['nota']}" if entry.get("nota") else "")
        ev = evidence_of(area_of(t["file"]))
        out.append({"label": t["label"], "first": min(t["games"].values()), "games": dict(sorted(t["games"].items(),
                    key=lambda kv: kv[1])), "status": status, "status_label": STATUS_LABELS[status], "detail": detail,
                    "evidence": ev[1], "file": t["file"], "url": t["url"], "score": t["score"]})
    out.sort(key=lambda x: x["first"])
    counts: dict[str, int] = defaultdict(int)
    for x in out:
        counts[x["status"]] += 1
    moved: dict[str, int] = defaultdict(int)
    for r in renames.values():
        moved[r["where"]] += 1
    return {"traces": out, "counts": dict(counts), "total": len(out), "renames": sorted(renames.values(), key=lambda r: r["day"] or ""),
            "moved": dict(moved)}
