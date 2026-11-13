"""Pagella degli avvisi: mese per mese, quanti avvisi erano tracce HLX, quanti altre novità utili e quanti rumore.

Ogni avviso mandato su Telegram si annota (stato pagella:avvisi): id della voce, giorno, tipo di voce (lo stesso del
pulsante 👎), se era una traccia HLX, punteggio. Il giudizio si ricalcola ogni volta con quello che si sa oggi, perché
un falso allarme spesso si scopre dopo:
  - rumore: la voce non c'è più (le regole l'hanno tolta: le voci si cancellano solo così), il suo punteggio oggi è
    sotto la soglia degli avvisi, oppure il suo tipo è stato silenziato (👎 o ✅ della revisione);
  - traccia HLX: una voce con parole HLX ancora valida;
  - utile: tutto il resto (hardware, build di Alyx, app nuove di Valve...).

Gli avvisi mandati prima del 13/11/2026 si ricavano dall'archivio (voci segnate come inviate): mancano quelli già
tolti dalle regole, quindi in quei mesi il rumore vero era di più. Il controllo generale del 12/11/2026 ne aveva
contati 29 su 43 negli ultimi 30 giorni.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

log = logging.getLogger(__name__)

STATE = "pagella:avvisi"
SEED = "pagella:dal"
KEEP = 5000
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre")


def _entries(db: Database) -> list[list[Any]]:
    try:
        data = json.loads(db.get_state(STATE) or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _entry(f: Finding, day: str) -> list[Any]:
    from hlxwatch import noise
    return [f.id, day[:10], noise.rule_of(f.game, f.change_type, f.keywords, f.file_path),
            1 if noise.protected(f.keywords) else 0, int(f.score or 0)]


def seed(db: Database) -> None:
    """Una volta sola: gli avvisi già mandati che sono ancora nell'archivio."""
    if db.get_state(SEED):
        return
    from hlxwatch.revisione import _row_finding
    known = {e[0] for e in _entries(db)}
    old = [_entry(_row_finding(r), str(r["created_at"] or "")) for r in db.conn.execute(
        "SELECT * FROM findings WHERE notified = 1 ORDER BY id") if r["id"] not in known]
    db.set_state(STATE, json.dumps((old + _entries(db))[-KEEP:]))
    db.set_state(SEED, utcnow().date().isoformat())
    log.info("Pagella degli avvisi: %d avvisi già mandati ricavati dall'archivio", len(old))


def record(db: Database, findings: Iterable[Finding]) -> None:
    """Annota gli avvisi appena mandati."""
    seed(db)
    entries = _entries(db)
    known = {e[0] for e in entries}
    day = utcnow().date().isoformat()
    new = [_entry(f, day) for f in findings if f.id is not None and f.id not in known]
    if new:
        db.set_state(STATE, json.dumps((entries + new)[-KEEP:]))


def verdict(entry: list[Any], row: Any, min_score: int, muted: dict[str, Any]) -> tuple[str, str]:
    """(giudizio, motivo) di un avviso, con quello che si sa oggi."""
    if row is None:
        return "rumore", "tolta dalle regole"
    if int(row["score"] or 0) < min_score:
        return "rumore", "punteggio abbassato"
    if entry[3]:
        return "hlx", ""
    if entry[2] in muted:
        return "rumore", "tipo silenziato"
    return "utile", ""


def report(cfg: Config, db: Database) -> dict[str, Any]:
    from hlxwatch import noise
    from hlxwatch.revisione import corrections
    seed(db)
    entries = _entries(db)
    min_score = int(cfg.get("general.notify_min_score", 50))
    muted = {**noise.rules(db), **corrections(db)["silenzi"]}
    rows = {}
    ids = [e[0] for e in entries]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in db.conn.execute(f"SELECT id, score FROM findings WHERE id IN ({','.join('?' * len(part))})", part):
            rows[r["id"]] = r
    since = db.get_state(SEED) or ""
    months: dict[str, dict[str, Any]] = {}
    noisy = []
    for e in entries:
        kind, why = verdict(e, rows.get(e[0]), min_score, muted)
        key = str(e[1])[:7]
        m = months.setdefault(key, {"mese": key, "n": 0, "hlx": 0, "utile": 0, "rumore": 0})
        m["n"] += 1
        m[kind] += 1
        if kind == "rumore":
            noisy.append({"giorno": e[1], "tipo": noise.describe(cfg, e[2]), "motivo": why})
    out = []
    for key in sorted(months, reverse=True)[:12]:
        m = months[key]
        year, month = key.split("-") if "-" in key else (key, "1")
        m["nome"] = f"{MONTHS[int(month) - 1]} {year}"
        m["perc"] = round(100 * m["rumore"] / m["n"]) if m["n"] else 0
        m["parziale"] = bool(since) and key <= since[:7]     # prima del registro mancano gli avvisi già tolti
        out.append(m)
    kinds: dict[str, int] = {}
    for x in noisy[-200:]:
        kinds[x["tipo"]] = kinds.get(x["tipo"], 0) + 1
    worst = sorted(kinds.items(), key=lambda kv: -kv[1])[:5]
    return {"mesi": out, "dal": since, "totale": len(entries), "peggiori": [{"tipo": k, "n": n} for k, n in worst]}
