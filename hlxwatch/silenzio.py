"""Il silenzio: da quanti giorni non compare un nome HLX nuovo, accanto alle pause di HLX e a quelle di Alyx.

Alyx prima dell'annuncio (alyx_seed.json): un nome nuovo ogni due mesi circa, al massimo 138 giorni di pausa; l'ultimo
il 13 giugno 2019, 161 giorni prima dell'annuncio, la pausa più lunga della sua storia. Dopo l'annuncio altri 104 giorni
senza niente, poi le voci della promozione in SteamVR negli ultimi giorni prima dell'uscita. È un caso solo: il silenzio
dice che il lavoro non passa più dai giochi pubblici, non che l'annuncio è vicino.

Due avvisi, uno per volta:
  - "silenzio record": HLX supera la sua pausa più lunga (stato silenzio:avvisato = l'ultima traccia di quel silenzio);
  - "uscita vicina": in modalità annuncio (decisa dal bot, letta da /fast/stato.json) compaiono nomi HLX nuovi dopo
    l'inizio; con Alyx era successo a ridosso dell'uscita (stato silenzio:dopo_annuncio = le tracce già segnalate).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

STATE_RECORD = "silenzio:avvisato"
STATE_AFTER = "silenzio:dopo_annuncio"
PROMO = re.compile(r"promo|store|destination|callout|achievement|trailer|preorder|wishlist|dismiss", re.I)


def _gaps(days: list[str]) -> list[dict[str, Any]]:
    out = []
    for a, b in zip(days, days[1:]):
        out.append({"da": a, "a": b, "giorni": (date.fromisoformat(b) - date.fromisoformat(a)).days})
    return out


def alyx_silence(seed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not seed or not seed.get("names"):
        return None
    ann = seed["announced"]
    before = sorted({n["day"] for n in seed["names"] if n["day"] < ann})
    after = sorted({n["day"] for n in seed["names"] if n["day"] >= ann})
    if len(before) < 3:
        return None
    gaps = [g["giorni"] for g in _gaps(before)]
    last_gap = (date.fromisoformat(ann) - date.fromisoformat(before[-1])).days
    return {"annuncio": ann, "uscita": seed["released"], "ultimo": before[-1], "prima_annuncio": last_gap,
            "massima": max(gaps), "tipica": round(statistics.median(gaps)),
            "dopo": (date.fromisoformat(after[0]) - date.fromisoformat(ann)).days if after else None,
            "totale": ((date.fromisoformat(after[0]) if after else date.fromisoformat(seed["released"]))
                       - date.fromisoformat(before[-1])).days}


def summary(reg: dict[str, Any], seed: dict[str, Any] | None, today: date | None = None) -> dict[str, Any] | None:
    traces = [t for t in reg.get("traces") or [] if t.get("first")]
    if not traces:
        return None
    today = today or date.today()
    days = sorted({t["first"][:10] for t in traces})
    if len(days) < 3:                        # servono almeno due pause per dire se questa è lunga
        return None
    last = days[-1]
    latest = sorted((t for t in traces if t["first"][:10] == last), key=lambda t: t.get("label") or "")
    since = (today - date.fromisoformat(last)).days
    gaps = _gaps(days)
    longest = max(gaps, key=lambda g: g["giorni"]) if gaps else None
    record = bool(longest) and since > longest["giorni"]
    alyx = alyx_silence(seed)
    return {"giorni": since, "ultimo": last, "ultime": [t.get("label") or t.get("key") for t in latest][:3],
            "massima": longest, "tipica": round(statistics.median([g["giorni"] for g in gaps])) if gaps else None,
            "pause": len(gaps), "record": record,
            "oltre_alyx": bool(alyx) and since > alyx["prima_annuncio"], "alyx": alyx,
            "quota": min(1.0, since / longest["giorni"]) if longest else None}


def _site(cfg: Any) -> str:
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    return site if site.endswith("/") else site + "/"


def watch(cfg: Any, db: Any, notifiers: list, fast: dict[str, Any] | None, reg: dict[str, Any] | None = None,
          seed: dict[str, Any] | None = None, today: date | None = None) -> list[str]:
    """I due avvisi. Ritorna i titoli dei messaggi mandati (per i log e i test)."""
    from hlxwatch.notifiers import notify_text
    if reg is None:
        from hlxwatch.outcomes import outcome_register
        reg = outcome_register(cfg, db)
    if seed is None:
        from hlxwatch import alyx
        try:
            seed = alyx.load_seed()
        except Exception:
            seed = None
    sent: list[str] = []
    s = summary(reg, seed, today)
    a = alyx_silence(seed)
    # 1. silenzio record: una volta per silenzio (cambia l'ultima traccia, l'avviso si riarma)
    if s and s["record"] and db.get_state(STATE_RECORD) != s["ultimo"]:
        db.set_state(STATE_RECORD, s["ultimo"])
        title = "HLX Watch: il silenzio più lungo di HLX"
        text = (f"🤫 Nessun nome HLX nuovo da {s['giorni']} giorni (l'ultimo: «{s['ultime'][0]}», {s['ultimo']}). "
                f"La pausa più lunga finora era di {s['massima']['giorni']} giorni.")
        if a:
            text += (f"\nPrima dell'annuncio di Alyx i suoi nomi erano spariti per {a['prima_annuncio']} giorni, la pausa più "
                     "lunga della sua storia. È un caso solo: il silenzio dice che il lavoro non passa più dai giochi "
                     "pubblici, non che l'annuncio è vicino.")
        notify_text(notifiers, title, text + f"\n{_site(cfg)}segnali.html#silenzio")
        sent.append(title)
    # 2. uscita vicina: nomi HLX nuovi dopo l'inizio della modalità annuncio
    mode = ((fast or {}).get("avvio") or {}).get("annuncio")
    if mode and mode.get("da"):
        start = str(mode["da"])[:10]
        try:
            told = set(json.loads(db.get_state(STATE_AFTER) or "[]"))
        except ValueError:
            told = set()
        fresh = [t for t in reg.get("traces") or [] if (t.get("first") or "")[:10] >= start
                 and (t.get("key") or t.get("label")) not in told]
        if fresh:
            told |= {t.get("key") or t.get("label") for t in fresh}
            db.set_state(STATE_AFTER, json.dumps(sorted(told), ensure_ascii=False))
            promo = [t for t in fresh if PROMO.search(f"{t.get('label', '')} {t.get('file', '')}")]
            title = "HLX Watch: nomi nuovi dopo l'annuncio"
            lines = [f"• «{t.get('label')}» ({t['first'][:10]})" for t in fresh[:6]]
            if len(fresh) > 6:
                lines.append(f"…e altri {len(fresh) - 6}")
            text = "🟠 " + "\n".join(lines)
            if a and a.get("dopo") is not None:
                text += (f"\nDopo l'annuncio di Alyx i file pubblici erano rimasti muti per {a['dopo']} giorni: i nomi "
                         "nuovi erano arrivati a ridosso dell'uscita, ed erano la promozione del gioco.")
            if promo:
                text += " Anche qui ci sono nomi da promozione (negozio, obiettivi, avvisi): l'uscita potrebbe essere vicina."
            notify_text(notifiers, title, text + f"\n{_site(cfg)}annuncio.html")
            sent.append(title)
            log.info("Dopo l'annuncio: %d nomi HLX nuovi", len(fresh))
    return sent
