"""Pagina "HLX in breve" (italiano e inglese) e immagine di anteprima per i link condivisi.

Tutti i numeri vengono dall'archivio, così le risposte restano vere senza riscriverle a mano."""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

from hlxwatch.config import Config

log = logging.getLogger(__name__)

# etichette del termometro in inglese, per la pagina en.html
LEVEL_EN = {
    "forte": "Lots of HLX activity", "tracce": "Recent HLX traces", "movimento": "Something is moving",
    "calma": "Quiet", "annuncio": "POSSIBLE ANNOUNCEMENT", "prova": "Announcement drill in progress",
}
# giochi in inglese: i titoli del config sono già nomi propri, tranne le voci "di servizio"
GAME_EN = {"Siti e domini di Valve": "Valve sites and domains", "Valve su GitHub": "Valve on GitHub",
           "SteamTracking (nuovi repository)": "SteamTracking (new repositories)", "Steam (nuove app)": "Steam (new apps)"}


def _it_date(day: str | None) -> str:
    return f"{day[8:10]}/{day[5:7]}/{day[:4]}" if day and len(day) >= 10 else (day or "")


def _en_date(day: str | None) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not day or len(day) < 10:
        return day or ""
    return f"{months[int(day[5:7]) - 1]} {int(day[8:10])}, {day[:4]}"


def brief_data(cfg: Config, termo: dict[str, Any], hist: dict[str, Any], reg: dict[str, Any]) -> dict[str, Any]:
    """I fatti della pagina in breve: prima traccia, giochi, ultima traccia forte, termometro, esiti."""
    traces = reg.get("traces") or []
    first = traces[0] if traces else None
    games = Counter(g for t in traces for g in t["games"])
    peak = max(hist.get("picchi") or [], key=lambda p: p["indice"], default=None)
    last = termo.get("ultima_traccia")
    return {
        "termo": termo,
        "level_en": LEVEL_EN.get(termo.get("livello", ""), termo.get("etichetta", "")),
        "first": {"day": first["first"], "it": _it_date(first["first"]), "en": _en_date(first["first"]),
                  "label": first["label"][:80],
                  "game": cfg.game_title(next(iter(first["games"])))} if first else None,
        "total": reg.get("total", 0),
        "present": (reg.get("counts") or {}).get("presente", 0),
        "renamed": (reg.get("counts") or {}).get("rinominata", 0),
        "games": [{"it": cfg.game_title(g), "en": GAME_EN.get(cfg.game_title(g), cfg.game_title(g)), "n": n}
                  for g, n in games.most_common(8)],
        "last": {**last, "it": _it_date(last["data"]), "en": _en_date(last["data"]),
                 "game_en": GAME_EN.get(last["gioco"], last["gioco"])} if last else None,
        "peak": {**peak, "it": _it_date(peak["giorno"]), "en": _en_date(peak["giorno"])} if peak else None,
        "since_it": _it_date(hist.get("inizio")), "since_en": _en_date(hist.get("inizio")),
        "days_40": hist.get("giorni_sopra_40", 0),
    }


def preview_image(data: dict[str, Any], path: Path, site_label: str) -> bool:
    """anteprima.png (1200x630) per Telegram, Discord, Reddit e WhatsApp: il termometro e l'ultima traccia forte.
    Se Pillow manca, il sito si genera lo stesso senza immagine."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow non installato: niente immagine di anteprima")
        return False

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)          # carattere scalabile incluso in Pillow (>= 10.1)
        except TypeError:
            return ImageFont.load_default()

    def fit(draw, text: str, f, width: int) -> str:
        if draw.textlength(text, font=f) <= width:
            return text
        while text and draw.textlength(text + "…", font=f) > width:
            text = text[:-1]
        return text + "…"

    termo = data["termo"]
    img = Image.new("RGB", (1200, 630), "#15181e")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1200, 10], fill="#e8743b")
    d.text((70, 55), "HLX Watch", font=font(64), fill="#e8743b")
    d.text((70, 135), "Tracce di HLX nei dati pubblici dei giochi Valve", font=font(32), fill="#c9ced8")
    # riquadro del termometro, nel colore del livello
    d.rounded_rectangle([70, 215, 450, 525], radius=26, fill=termo.get("colore", "#3A4150"))
    num = str(termo.get("indice", 0))
    big = font(170)
    d.text((260 - d.textlength(num, font=big) / 2, 235), num, font=big, fill="#ffffff")
    label = fit(d, termo.get("etichetta", ""), font(32), 340)
    d.text((260 - d.textlength(label, font=font(32)) / 2, 440), label, font=font(32), fill="#ffffff")
    # a destra: ultima traccia forte e massimo storico
    x, w = 500, 640
    d.text((x, 225), "ULTIMA TRACCIA HLX FORTE", font=font(26), fill="#9aa3b2")
    last = data.get("last")
    if last:
        d.text((x, 262), fit(d, f"{last['it']} in {last['gioco']}", font(44), w), font=font(44), fill="#ffffff")
        d.text((x, 320), fit(d, last.get("testo", ""), font(30), w), font=font(30), fill="#e5e7eb")
    else:
        d.text((x, 262), "nessuna finora", font=font(44), fill="#ffffff")
    peak = data.get("peak")
    if peak:
        d.text((x, 400), "MASSIMO DAL " + data["since_it"][-4:], font=font(26), fill="#9aa3b2")
        d.text((x, 437), f"{peak['indice']} il {peak['it']}", font=font(40), fill="#ffffff")
    d.text((70, 570), fit(d, f"{site_label}  ·  non affiliato con Valve", font(26), 1060), font=font(26), fill="#9aa3b2")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return True
