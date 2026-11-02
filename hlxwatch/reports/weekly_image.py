"""Immagine del riepilogo settimanale (1200x675) per Telegram: la settimana in un colpo d'occhio.

Il termometro di oggi, il grafico degli ultimi 30 giorni (voci importanti in altezza, la settimana evidenziata,
il rumore in una fascia sottile) e i pezzi HLX della settimana con il loro tipo: gioco, motore, eredità di Alyx.
Stesse regole del sito: le date delle voci sono quelle corrette dalla prima comparsa, come nel grafico della home.
Se Pillow manca, il riepilogo parte lo stesso, solo testo.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hlxwatch.config import Config
from hlxwatch.fase import HLX_KEYWORDS, alert_kind, piece_key
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso
from hlxwatch.timeutil import local_day_bounds_utc, now_local

log = logging.getLogger(__name__)

MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
KIND_STYLE = {"gioco": ("GIOCO", "#2e9d4f"), "motore": ("MOTORE", "#3b82f6"), "alyx": ("ALYX", "#8b5cf6"),
              None: ("HLX", "#6b7280")}
KIND_ORDER = {"alyx": 0, "gioco": 1, "motore": 2, None: 3}


def week_pieces(db: Database, cfg: Config, monday: date, sunday: date) -> list[dict[str, Any]]:
    """Le tracce HLX rilevanti trovate nella settimana, una per pezzo, con il loro tipo (prima eredità e gioco).
    Se Valve le aveva aggiunte prima (la scansione completa le ha trovate dopo), `added` dice quando."""
    from hlxwatch.reports.site import apply_first_seen, first_seen_days
    first_days = first_seen_days(db)
    start, _ = local_day_bounds_utc(monday)
    _, end = local_day_bounds_utc(sunday)
    rows = db.conn.execute(
        "SELECT f.game, f.token, f.text, f.keywords, f.score, f.source, f.change_type, f.file_path, "
        "f.created_at, b.date AS build_date FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.score >= 50 AND f.change_type NOT IN ('baseline', 'present', 'build') "
        "AND COALESCE(b.date, f.created_at) >= ? AND COALESCE(b.date, f.created_at) < ? ORDER BY f.score DESC",
        (iso(start), iso(end))).fetchall()
    out, seen = [], set()
    for r in rows:
        try:
            keywords = json.loads(r["keywords"] or "[]")
        except ValueError:
            keywords = []
        if not HLX_KEYWORDS.intersection(keywords):
            continue
        label = (r["token"] or r["text"] or "").strip()
        key = piece_key(label)
        if key in seen:
            continue
        seen.add(key)
        day = apply_first_seen({**dict(r), "day": ""}, first_days)["day"]
        added = f"{day[8:10]}/{day[5:7]}" if day and day < monday.isoformat() else ""
        out.append({"label": label, "game": cfg.game_title(r["game"]), "score": r["score"], "added": added,
                    "kind": alert_kind(r["token"], r["text"], keywords)})
    out.sort(key=lambda p: (KIND_ORDER.get(p["kind"], 3), -p["score"]))
    return out


def week_days(db: Database, sunday: date, days: int = 30) -> list[dict[str, Any]]:
    """Gli ultimi `days` giorni fino a domenica, con le voci per livello (date corrette dalla prima comparsa)."""
    from hlxwatch.reports.site import first_seen_days, timeline_rows
    first = sunday - timedelta(days=days - 1)
    window = (now_local().date() - first).days + 2
    per = {r["day"]: r for r in timeline_rows(db, window, first_seen_days(db))}
    out = []
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        r = per.get(day, {})
        high, mid = int(r.get("n_high", 0)), int(r.get("n_mid", 0))
        out.append({"day": day, "high": high, "mid": mid, "low": max(0, int(r.get("n", 0)) - high - mid)})
    return out


def draw(data: dict[str, Any], path: Path, site_label: str) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow non installato: riepilogo settimanale senza immagine")
        return False
    from hlxwatch.dashboard.app import _scale_top

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def fit(d, text: str, f, width: int) -> str:
        if d.textlength(text, font=f) <= width:
            return text
        while text and d.textlength(text + "…", font=f) > width:
            text = text[:-1]
        return text + "…"

    W, H = 1200, 675
    img = Image.new("RGB", (W, H), "#15181e")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill="#e8743b")
    d.text((60, 40), "HLX Watch", font=font(34), fill="#e8743b")
    # il carattere incluso in Pillow non ha "–" né "≥": si usano "-" e "100+"
    d.text((60, 84), fit(d, f"Settimana {data['label'].replace('–', '-')}", font(48), 1080), font=font(48), fill="#ffffff")

    # termometro di oggi
    termo = data["termo"]
    d.rounded_rectangle([60, 170, 330, 420], radius=22, fill=termo.get("colore", "#3A4150"))
    num, big = str(termo.get("indice", 0)), font(130)
    d.text((195 - d.textlength(num, font=big) / 2, 190), num, font=big, fill="#ffffff")
    lab = fit(d, termo.get("etichetta", ""), font(26), 240)
    d.text((195 - d.textlength(lab, font=font(26)) / 2, 345), lab, font=font(26), fill="#ffffff")
    d.text((195 - d.textlength("termometro HLX", font=font(20)) / 2, 385), "termometro HLX", font=font(20), fill="#f1f1f1")

    # grafico degli ultimi 30 giorni
    days = data["days"]
    left, right, top, axis = 380, 1140, 200, 380
    col = (right - left) / len(days)
    week_x = left + (len(days) - 7) * col
    d.rectangle([week_x, top - 30, right, axis + 26], fill="#222a38")
    d.text((week_x + 8, top - 26), "questa settimana", font=font(20), fill="#c9ced8")
    d.text((left, top - 26), "ultimi 30 giorni", font=font(20), fill="#9aa3b2")
    top_n = _scale_top([x["high"] + x["mid"] for x in days])
    max_low = max([x["low"] for x in days] + [1])
    for i, x in enumerate(days):
        x0 = left + i * col + 2
        x1 = left + (i + 1) * col - 2
        total = x["high"] + x["mid"]
        if total:
            full = (axis - top) * min(total, top_n) / top_n
            y = axis
            for n, color in ((x["high"], "#e53935"), (x["mid"], "#fb8c00")):
                if n:
                    h = max(3.0, full * n / total)
                    d.rectangle([x0, y - h, x1, y], fill=color)
                    y -= h
            if total > top_n:
                t = str(total)
                d.text(((x0 + x1) / 2 - d.textlength(t, font=font(18)) / 2, top - 4), t, font=font(18), fill="#ffffff")
        if x["low"]:
            h = max(2.0, 14 * math.log10(1 + x["low"]) / math.log10(1 + max_low))
            d.rectangle([x0, axis + 4, x1, axis + 4 + h], fill="#5b6575")
    d.line([left, axis, right, axis], fill="#6b7280", width=2)
    first, last = days[0]["day"], days[-1]["day"]
    d.text((left, axis + 30), f"{int(first[8:])} {MESI[int(first[5:7]) - 1]}", font=font(20), fill="#9aa3b2")
    end_label = f"{int(last[8:])} {MESI[int(last[5:7]) - 1]}"
    d.text((right - d.textlength(end_label, font=font(20)), axis + 30), end_label, font=font(20), fill="#9aa3b2")
    d.text((left + 120, axis + 30), "rosso: voci 100+ · arancio: 50-99 · grigio: rumore", font=font(18), fill="#7d8696")

    # i pezzi HLX della settimana (al massimo 3: il resto si dice nel titolo)
    y = 455
    pieces = data["pieces"]
    if pieces:
        head = f"Tracce HLX trovate questa settimana: {data['n_pieces']}"
        if data["n_pieces"] > 3:
            head += ", le prime 3"
        d.text((60, y), head, font=font(28), fill="#ffffff")
        y += 46
        for p in pieces[:3]:
            tag, color = KIND_STYLE.get(p["kind"], KIND_STYLE[None])
            tw = d.textlength(tag, font=font(20)) + 24
            d.rounded_rectangle([60, y, 60 + tw, y + 32], radius=16, fill=color)
            d.text((72, y + 5), tag, font=font(20), fill="#ffffff")
            extra = f"  ·  aggiunta da Valve il {p['added']}" if p.get("added") else ""
            d.text((60 + tw + 14, y + 2), fit(d, f"{p['label']}  ·  {p['game']}{extra}", font(24), 1080 - tw - 14),
                   font=font(24), fill="#e5e7eb")
            y += 42
    else:
        d.text((60, y), "Nessuna traccia HLX nuova questa settimana.", font=font(28), fill="#ffffff")
        d.text((60, y + 46), f"Voci rilevanti di altro tipo: {data['relevant']}", font=font(24), fill="#c9ced8")
    d.text((60, H - 34), fit(d, f"{site_label}  ·  non affiliato con Valve", font(20), 1080), font=font(20), fill="#7d8696")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return True


def weekly_image(cfg: Config, db: Database, week: dict[str, Any], monday: date, sunday: date, path: Path,
                 label: str) -> bool:
    """Raccoglie i dati e disegna l'immagine. `week` è il risultato di weekly.collect_week."""
    from hlxwatch.reports.termometro import compute
    pieces = week_pieces(db, cfg, monday, sunday)
    data = {"label": label, "termo": compute(cfg, db), "days": week_days(db, sunday), "pieces": pieces,
            "n_pieces": len(pieces), "relevant": week.get("voci_rilevanti", 0)}
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    try:
        return draw(data, path, site.split("//", 1)[-1].rstrip("/"))
    except Exception:                       # l'immagine è un di più: un errore non deve fermare il riepilogo
        log.exception("Immagine del riepilogo settimanale non riuscita")
        return False
