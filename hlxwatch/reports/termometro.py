"""Termometro HLX: un indice da 0 a 100 del momento, per il widget dell'app Android (bot/termometro.json).

Conta le tracce HLX forti (parole chiave della famiglia HLX stretta, punteggio almeno 100) per data del commit di
Valve, non per data di registrazione: l'archivio è stato riempito tutto insieme a ottobre 2026. Ogni riga distinta pesa
punteggio/100 e il peso dimezza ogni settimana (HALF_LIFE_DAYS); l'indice è 100 * (1 - e^(-punti/SCALE)), quindi cresce
in fretta con le prime tracce e non arriva mai a 100 da solo. 100 è riservato a un possibile annuncio (ultimi 3 giorni)
o alla prova generale in corso. Sui dati reali: 0 a settembre 2026, 65 il giorno dopo le tracce del 29 ottobre in CS2
e Dota 2, 38 una settimana dopo, 12 dopo tre settimane.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Any

from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow
from hlxwatch.timeutil import local, local_day

STRONG = {"hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hl3", "half_life_3"}
MIN_SCORE = 100
HALF_LIFE_DAYS = 7.0
SCALE = 6.0
WINDOW_DAYS = 60
LEVELS = [  # (indice minimo, chiave, etichetta, colore di sfondo del widget)
    (75, "forte", "Molta attività HLX", "#C0392B"),
    (40, "tracce", "Tracce HLX recenti", "#B4531B"),
    (15, "movimento", "Qualcosa si muove", "#8A6D00"),
    (0, "calma", "Calma", "#3A4150"),
]
ANNOUNCEMENT = ("annuncio", "POSSIBILE ANNUNCIO", "#B71C1C")
DRILL = ("prova", "Prova generale in corso", "#1F2430")


def _strong(keywords: str | None) -> bool:
    try:
        return bool(set(json.loads(keywords or "[]")) & STRONG)
    except ValueError:
        return False


def compute(cfg: Config, db: Database, latest: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utcnow()
    rows = db.conn.execute(
        "SELECT f.game, f.score, f.keywords, f.token, f.text, f.dedup_hash, COALESCE(b.date, f.created_at) AS d "
        "FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.change_type NOT IN ('baseline', 'present') AND f.score >= ? AND f.game != 'hlxwatch'",
        (MIN_SCORE,)).fetchall()
    weights: dict[str, float] = {}
    last: dict[str, Any] | None = None
    last_at = None
    recent_30: set[str] = set()
    for r in rows:
        if not _strong(r["keywords"]):
            continue
        at = parse_iso(r["d"])
        if at is None or at > now + timedelta(hours=1):
            continue
        age = (now - at).total_seconds() / 86400
        if last_at is None or at > last_at:
            last_at, last = at, dict(r)
        if age <= 30:
            recent_30.add(r["dedup_hash"])
        if age <= WINDOW_DAYS:
            w = min(int(r["score"]), 250) / 100 * 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
            weights[r["dedup_hash"]] = max(weights.get(r["dedup_hash"], 0.0), w)
    points = sum(weights.values())
    index = min(99, round(100 * (1 - math.exp(-points / SCALE))))
    key, label, color = next((k, lab, c) for floor, k, lab, c in LEVELS if index >= floor)

    announced = db.conn.execute(
        "SELECT COUNT(*) FROM findings WHERE keywords LIKE '%\"possible_announcement\"%' AND game != 'hlxwatch' "
        "AND created_at >= ?", (iso(now - timedelta(days=3)),)).fetchone()[0]
    from hlxwatch.announcement import drill_item
    drill = drill_item(db, "") is not None
    if announced:
        index, (key, label, color) = 100, ANNOUNCEMENT
    elif drill:
        index, (key, label, color) = 100, DRILL

    out: dict[str, Any] = {
        "indice": index, "livello": key, "etichetta": label, "colore": color,
        "tracce_30g": len(recent_30), "aggiornato": local(now), "annuncio": bool(announced),
        "prova": drill and not announced, "ultima_traccia": None, "ultima_voce": None,
    }
    if last is not None and last_at is not None:
        out["ultima_traccia"] = {
            "giorni": max(0, (now - last_at).days), "data": local_day(last_at),
            "gioco": cfg.game_title(last["game"]), "testo": ((last["token"] or last["text"] or "").strip())[:60],
        }
    if latest:
        out["ultima_voce"] = {"gioco": latest.get("g", ""), "testo": (latest.get("k") or latest.get("x") or "")[:70],
                              "data": latest.get("d", ""), "punteggio": latest.get("s", 0)}
    return out
