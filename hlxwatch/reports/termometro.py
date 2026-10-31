"""Termometro HLX: un indice da 0 a 100 del momento, per il widget dell'app Android (bot/termometro.json) e per la
pagina termometro.html con lo storico giorno per giorno.

Conta le tracce HLX forti (parole chiave della famiglia HLX stretta, punteggio almeno 100) per data del commit di
Valve, non per data di registrazione: l'archivio è stato riempito tutto insieme a ottobre 2026. Ogni riga distinta pesa
punteggio/100 e il peso dimezza ogni settimana (HALF_LIFE_DAYS); l'indice è 100 * (1 - e^(-punti/SCALE)), quindi cresce
in fretta con le prime tracce e non arriva mai a 100 da solo. 100 è riservato a un possibile annuncio (ultimi 3 giorni)
o alla prova generale in corso. Sui dati reali: 0 a settembre 2026, 65 il giorno dopo le tracce del 29 ottobre in CS2
e Dota 2, 38 una settimana dopo, 12 dopo tre settimane.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timedelta, timezone
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
HISTORY_START = "2021-01-01"
PEAK_MIN = 40                 # un picco dello storico: indice almeno 40...
PEAK_GAP_DAYS = 21            # ...e ad almeno tre settimane dal picco precedente
LEVELS = [  # (indice minimo, chiave, etichetta, colore di sfondo del widget)
    (75, "forte", "Molta attività HLX", "#C0392B"),
    (40, "tracce", "Tracce HLX recenti", "#B4531B"),
    (15, "movimento", "Qualcosa si muove", "#8A6D00"),
    (0, "calma", "Calma", "#3A4150"),
]
ANNOUNCEMENT = ("annuncio", "POSSIBILE ANNUNCIO", "#B71C1C")
DRILL = ("prova", "Prova generale in corso", "#1F2430")
# fatti noti da segnare sul grafico (si cambiano in config: termometro.eventi)
DEFAULT_EVENTS = [
    {"data": "2024-08-23", "testo": "Deadlock annunciato"},
    {"data": "2025-11-12", "testo": "Annunciati Steam Machine, Steam Frame e il nuovo Steam Controller"},
]


def _strong(keywords: str | None) -> bool:
    try:
        return bool(set(json.loads(keywords or "[]")) & STRONG)
    except ValueError:
        return False


def traces(db: Database) -> list[dict[str, Any]]:
    """Le tracce HLX forti con la data del commit, dalla più vecchia."""
    rows = db.conn.execute(
        "SELECT f.game, f.score, f.keywords, f.token, f.text, f.dedup_hash, COALESCE(b.date, f.created_at) AS d "
        "FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.change_type NOT IN ('baseline', 'present') AND f.score >= ? AND f.game != 'hlxwatch'",
        (MIN_SCORE,)).fetchall()
    out = []
    for r in rows:
        at = parse_iso(r["d"])
        if at is None or not _strong(r["keywords"]):
            continue
        out.append({"at": at, "hash": r["dedup_hash"], "score": min(int(r["score"]), 250), "game": r["game"],
                    "label": ((r["token"] or r["text"] or "").strip())[:60]})
    return sorted(out, key=lambda t: t["at"])


def index_at(items: list[dict[str, Any]], now: datetime) -> tuple[int, dict[str, Any] | None]:
    """Indice al momento `now` (senza annunci né prove) e la traccia che pesa di più."""
    weights: dict[str, float] = {}
    top, top_w = None, 0.0
    for t in items:
        age = (now - t["at"]).total_seconds() / 86400
        if age < -1 / 24 or age > WINDOW_DAYS:
            continue
        w = t["score"] / 100 * 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
        if w > weights.get(t["hash"], 0.0):
            weights[t["hash"]] = w
        if w > top_w:
            top, top_w = t, w
    points = sum(weights.values())
    return min(99, round(100 * (1 - math.exp(-points / SCALE)))), top


def level_of(index: int) -> tuple[str, str, str]:
    return next((k, lab, c) for floor, k, lab, c in LEVELS if index >= floor)


def compute(cfg: Config, db: Database, latest: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utcnow()
    items = [t for t in traces(db) if t["at"] <= now + timedelta(hours=1)]
    index, _ = index_at(items, now)
    key, label, color = level_of(index)
    last = items[-1] if items else None
    recent_30 = {t["hash"] for t in items if (now - t["at"]).total_seconds() <= 30 * 86400}

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
    if last is not None:
        out["ultima_traccia"] = {
            "giorni": max(0, (now - last["at"]).days), "data": local_day(last["at"]),
            "gioco": cfg.game_title(last["game"]), "testo": last["label"],
        }
    if latest:
        out["ultima_voce"] = {"gioco": latest.get("g", ""), "testo": (latest.get("k") or latest.get("x") or "")[:70],
                              "data": latest.get("d", ""), "punteggio": latest.get("s", 0)}
    return out


# ------------------------------------------------------------------ storico
def history(cfg: Config, db: Database, start: str = HISTORY_START) -> dict[str, Any]:
    """Indice giorno per giorno (a mezzogiorno UTC) da `start` a oggi, con i picchi e gli eventi da segnare."""
    items = traces(db)
    today = utcnow().date()
    day = date.fromisoformat(start)
    series: list[dict[str, Any]] = []
    tops: dict[str, dict[str, Any] | None] = {}
    while day <= today:
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
        value, top = index_at(items, noon)
        series.append({"giorno": day.isoformat(), "indice": value})
        tops[day.isoformat()] = top
        day += timedelta(days=1)
    peaks: list[dict[str, Any]] = []
    for i, p in enumerate(series):
        v = p["indice"]
        if v < PEAK_MIN:
            continue
        before = series[i - 1]["indice"] if i else -1
        after = series[i + 1]["indice"] if i + 1 < len(series) else -1
        if v < before or v < after:
            continue
        when = date.fromisoformat(p["giorno"])
        if peaks and (when - date.fromisoformat(peaks[-1]["giorno"])).days < PEAK_GAP_DAYS:
            if v > peaks[-1]["indice"]:
                peaks.pop()
            else:
                continue
        top = tops.get(p["giorno"])
        peaks.append({"giorno": p["giorno"], "indice": v, "gioco": cfg.game_title(top["game"]) if top else "",
                      "traccia": top["label"] if top else "", "traccia_del": local_day(top["at"]) if top else ""})
    events = (cfg.get("termometro", {}) or {}).get("eventi") or DEFAULT_EVENTS
    values = [p["indice"] for p in series]
    return {"serie": series, "picchi": peaks, "eventi": events, "inizio": start,
            "massimo": max(values) if values else 0, "giorni_sopra_40": sum(1 for v in values if v >= 40)}


def history_svg(h: dict[str, Any]) -> str:
    """Grafico SVG dello storico: fasce dei livelli, linea dell'indice, picchi e fatti noti (colori dal tema del sito)."""
    series = h["serie"]
    if not series:
        return ""
    w, ht, left, right, top, bottom = 1000, 280, 34, 10, 14, 30
    first = date.fromisoformat(series[0]["giorno"])
    span = max(1, (date.fromisoformat(series[-1]["giorno"]) - first).days)

    def x(day: str) -> float:
        return left + (date.fromisoformat(day) - first).days / span * (w - left - right)

    def y(v: float) -> float:
        return top + (100 - v) / 100 * (ht - top - bottom)

    parts = [f'<svg viewBox="0 0 {w} {ht}" class="termo" role="img" aria-label="Termometro HLX giorno per giorno">']
    bands = [(75, 100, "#C0392B"), (40, 75, "#B4531B"), (15, 40, "#8A6D00")]
    for lo, hi, color in bands:
        parts.append(f'<rect x="{left}" y="{y(hi):.1f}" width="{w - left - right}" height="{y(lo) - y(hi):.1f}" '
                     f'fill="{color}" opacity="0.10"/>')
    for v in (0, 15, 40, 75, 100):
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="currentColor" '
                     f'opacity="0.15"/><text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="currentColor" opacity="0.6">{v}</text>')
    for year in range(first.year, date.fromisoformat(series[-1]["giorno"]).year + 1):
        d = f"{year}-01-01"
        if d >= series[0]["giorno"]:
            parts.append(f'<line x1="{x(d):.1f}" x2="{x(d):.1f}" y1="{top}" y2="{ht - bottom}" stroke="currentColor" '
                         f'opacity="0.12"/><text x="{x(d) + 3:.1f}" y="{ht - 10}" font-size="12" fill="currentColor" '
                         f'opacity="0.7">{year}</text>')
    for e in h["eventi"]:
        if series[0]["giorno"] <= e["data"] <= series[-1]["giorno"]:
            ex = x(e["data"])
            parts.append(f'<line x1="{ex:.1f}" x2="{ex:.1f}" y1="{top}" y2="{ht - bottom}" stroke="#2f80ed" '
                         f'stroke-dasharray="4 3"><title>{e["data"]}: {html.escape(str(e["testo"]))}</title></line>')
    pts = " ".join(f"{x(p['giorno']):.1f},{y(p['indice']):.1f}" for p in series)
    parts.append(f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>')
    for p in h["picchi"]:
        parts.append(f'<circle cx="{x(p["giorno"]):.1f}" cy="{y(p["indice"]):.1f}" r="3.5" fill="var(--accent)">'
                     f'<title>{p["giorno"]}: {p["indice"]} ({html.escape(p["gioco"])}: {html.escape(p["traccia"])})</title></circle>')
    parts.append("</svg>")
    return "".join(parts)
