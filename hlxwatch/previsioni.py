"""Il registro delle previsioni: cosa dicono i video su HLX e cosa dicono i dati.

Le affermazioni dei data miner (Tyler McVicker, Gabe Follower, GAMERANGE...) sono scritte a mano in previsioni.json,
dopo averle confrontate con l'archivio, con un esito: confermata, vera in parte, smentita, non trovata nei file,
non verificabile, in attesa. Due cose si aggiornano da sole:
  - regola "niente_annuncio_entro": la previsione "HLX non sarà annunciato entro X" diventa smentita se il giorno
    dell'annuncio (annuncio_hlx nel file) viene prima della scadenza, confermata se la scadenza passa senza annuncio;
  - "segnale": per le affermazioni non trovate (o in attesa) si cercano le tracce HLX arrivate dopo il video. Se una
    corrisponde, l'esito diventa "da rivedere" e arriva un avviso: forse il video aveva visto giusto in anticipo.
Si cercano solo le tracce HLX (righe con il nome HLX), non tutto l'archivio: "temperature" o "weather" compaiono in
mille file del motore e non dicono niente su HLX.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PATH = Path(__file__).with_name("previsioni.json")
STATE = "previsioni:esiti"
HLX_KEYWORDS = ("hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hlx_game_type")
NEW_TYPES = ("added", "changed", "renamed")
ESITI = {
    "confermata": ("✅", "Confermata"),
    "in_parte": ("🟡", "Vera in parte"),
    "smentita": ("❌", "Smentita"),
    "non_trovata": ("🔍", "Non trovata nei file"),
    "non_verificata": ("❔", "Non verificabile"),
    "in_attesa": ("⏳", "In attesa"),
    "da_rivedere": ("🔔", "Da rivedere"),
}
# l'ordine delle colonne nel conteggio per autore
ORDINE = ("confermata", "in_parte", "smentita", "non_trovata", "da_rivedere", "in_attesa", "non_verificata")


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"video": [], "voci": []}


def hlx_traces(db: Any, since: str) -> list[tuple[str, str, str]]:
    """Le tracce HLX nuove (aggiunte, cambiate, rinominate) dal giorno `since`: (testo, giorno, gioco)."""
    out = []
    for text, token, keywords, when, game in db.conn.execute(
            "SELECT text, token, keywords, created_at, game FROM findings WHERE created_at >= ? AND change_type IN "
            f"({','.join('?' * len(NEW_TYPES))})", (since, *NEW_TYPES)):
        try:
            kws = json.loads(keywords) if keywords and keywords.startswith("[") else (keywords or "").split(",")
        except ValueError:
            kws = []
        if any(k in HLX_KEYWORDS for k in kws):
            out.append((" ".join(x for x in (token, text) if x), (when or "")[:10], game))
    return out


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def evaluate(data: dict[str, Any], db: Any = None, today: date | None = None) -> list[dict[str, Any]]:
    """Le voci con l'esito di oggi, il video e (se ci sono) le tracce HLX che corrispondono al segnale."""
    if today is None:
        from hlxwatch.timeutil import today_local
        today = date.fromisoformat(today_local())      # il giorno in Italia, non quello del server
    videos = {v["id"]: v for v in data.get("video") or []}
    announced = _parse_day(data.get("annuncio_hlx"))
    traces_cache: dict[str, list[tuple[str, str, str]]] = {}
    out = []
    for raw in data.get("voci") or []:
        v = dict(raw)
        video = videos.get(v.get("video"), {})
        v["video_info"] = video
        esito = v.get("esito") or "non_verificata"
        v["esito_scritto"] = esito
        v["motivo"] = ""
        deadline = _parse_day(v.get("scadenza"))
        if v.get("regola") == "niente_annuncio_entro" and deadline and esito == "in_attesa":
            if announced and announced <= deadline:
                esito, v["motivo"] = "smentita", f"HLX è stato annunciato il {announced:%d/%m/%Y}."
            elif today > deadline:
                esito, v["motivo"] = "confermata", f"La scadenza del {deadline:%d/%m/%Y} è passata senza annuncio."
            else:
                v["giorni"] = (deadline - today).days
                v["motivo"] = f"Si decide da solo il {deadline:%d/%m/%Y}: mancano {v['giorni']} giorni."
        elif deadline and esito == "in_attesa" and today > deadline:
            esito, v["motivo"] = "da_rivedere", f"La scadenza del {deadline:%d/%m/%Y} è passata: va rivista a mano."
        v["tracce"] = []
        if v.get("segnale") and db is not None and esito in ("non_trovata", "in_attesa"):
            since = str(video.get("data") or "2000-01-01")
            if since not in traces_cache:
                traces_cache[since] = hlx_traces(db, since)
            try:
                rx = re.compile(v["segnale"])
            except re.error:
                rx = None
            hits = [t for t in traces_cache[since] if rx and rx.search(t[0])]
            if hits:
                v["tracce"] = [{"testo": t[0][:160], "giorno": t[1], "gioco": t[2]} for t in hits[:5]]
                esito = "da_rivedere"
                v["motivo"] = (f"Dopo il video sono comparse {len(hits)} tracce HLX che corrispondono"
                               f" (la prima il {hits[0][1][8:10]}/{hits[0][1][5:7]}/{hits[0][1][:4]}).")
        v["esito"] = esito
        v["simbolo"], v["etichetta"] = ESITI.get(esito, ("❔", esito))
        out.append(v)
    return out


def summary(db: Any = None, data: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any]:
    data = data if data is not None else load()
    voci = evaluate(data, db, today)
    authors: dict[str, dict[str, Any]] = {}
    for v in voci:
        name = v["video_info"].get("autore") or "?"
        a = authors.setdefault(name, {"autore": name, "conti": {k: 0 for k in ORDINE}, "voci": 0, "video": set()})
        a["conti"][v["esito"]] = a["conti"].get(v["esito"], 0) + 1
        a["voci"] += 1
        a["video"].add(v.get("video"))
    for a in authors.values():
        c = a["conti"]
        checked = c["confermata"] + c["in_parte"] + c["smentita"] + c["non_trovata"]
        # quota: le confermate (le "in parte" a metà) su quelle che i dati hanno potuto controllare
        a["quota"] = round(100 * (c["confermata"] + 0.5 * c["in_parte"]) / checked) if checked else None
        a["controllate"] = checked
        a["video"] = len(a["video"])
    videos = []
    for vid in sorted(data.get("video") or [], key=lambda x: x.get("data", ""), reverse=True):
        videos.append({**vid, "voci": [v for v in voci if v.get("video") == vid["id"]]})
    totals = {k: sum(1 for v in voci if v["esito"] == k) for k in ORDINE}
    return {"video": videos, "autori": sorted(authors.values(), key=lambda a: (-a["voci"], a["autore"])),
            "totali": totals, "n": len(voci), "annuncio": data.get("annuncio_hlx"),
            "attesa": sorted([v for v in voci if v.get("regola") and v.get("giorni") is not None],
                             key=lambda v: v["giorni"]),
            "esiti": [{"chiave": k, "simbolo": ESITI[k][0], "etichetta": ESITI[k][1]} for k in ORDINE]}


def watch(cfg: Any, db: Any, notifiers: list | None = None, data: dict[str, Any] | None = None,
          today: date | None = None) -> list[dict[str, Any]]:
    """Avvisa quando l'esito di una voce cambia da solo (una scadenza passata, una traccia HLX che corrisponde).
    Il primo giro impara senza avvisi; le voci cambiate a mano nel file non mandano avvisi."""
    voci = evaluate(data if data is not None else load(), db, today)
    try:
        before = json.loads(db.get_state(STATE) or "null")
    except ValueError:
        before = None
    now = {v["id"]: v["esito"] for v in voci}
    changed = []
    if before is not None:
        for v in voci:
            old = before.get(v["id"])
            if old is not None and old != v["esito"] and v["esito"] != v["esito_scritto"]:
                changed.append(v)
    db.set_state(STATE, json.dumps(now))
    if changed and notifiers:
        from hlxwatch.notifiers import notify_text
        site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/")).rstrip("/")
        lines = [f"{v['simbolo']} {v['video_info'].get('autore', '?')}: «{v['testo']}» → {v['etichetta'].lower()}. "
                 f"{v['motivo']}" for v in changed]
        notify_text(notifiers, "HLX Watch: una previsione è cambiata",
                    "📋 " + "\n".join(lines) + f"\n\nIl registro: {site}/previsioni.html")
    return changed
