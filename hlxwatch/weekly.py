"""Riepilogo settimanale su Telegram: il lunedì, la settimana precedente (da lunedì a domenica, ora italiana).

Numeri per gioco (commit di SteamTracking, build), voci rilevanti e una sintesi scritta da Claude. Se nella
settimana non c'è nessuna voce rilevante l'AI non viene chiamata: il messaggio riporta solo i numeri.
Il costo rientra nel limite mensile dell'analisi AI (stato ai:extra_cost:AAAA-MM).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from hlxwatch.analysis import DEFAULT_GENERIC, SITE_URL, Analyzer, _evidence, is_generic, structured_call
from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow
from hlxwatch.timeutil import local_day, local_day_bounds_utc, now_local

log = logging.getLogger(__name__)
EVIDENCE_RE = re.compile(r"\s*\((?:E\d+[,;\s]*)+\)")     # "(E1, E2)": utile all'analisi, non a chi legge

MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
          "ottobre", "novembre", "dicembre"]

WEEKLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sintesi": {"type": "string", "description": "Due-quattro frasi sulla settimana, in italiano semplice"},
        "punti": {"type": "array", "items": {"type": "string"},
                  "description": "Da tre a cinque fatti importanti della settimana, una frase ciascuno"},
        "da_osservare": {"type": "array", "items": {"type": "string"}, "description": "Al massimo tre cose da tenere d'occhio"},
        "novita_hlx": {"type": "boolean", "description": "true solo se la settimana dice qualcosa di nuovo su HLX"},
    },
    "required": ["sintesi", "punti", "da_osservare", "novita_hlx"],
    "additionalProperties": False,
}


def last_week(today: date | None = None) -> tuple[date, date]:
    """Lunedì e domenica della settimana conclusa prima di quella di `today` (ora italiana)."""
    today = today or now_local().date()
    monday = today - timedelta(days=today.weekday()) - timedelta(days=7)
    return monday, monday + timedelta(days=6)


def week_label(monday: date, sunday: date) -> str:
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTHS[sunday.month - 1]}"
    return f"{monday.day} {MONTHS[monday.month - 1]} – {sunday.day} {MONTHS[sunday.month - 1]}"


def collect_week(db: Database, cfg: Config, monday: date, sunday: date) -> dict[str, Any]:
    start, _ = local_day_bounds_utc(monday)
    _, end = local_day_bounds_utc(sunday)
    s, e = iso(start), iso(end)
    conn = db.conn
    commits = {r[0]: r[1] for r in conn.execute(
        "SELECT game, COUNT(*) FROM builds WHERE source = 'github' AND date >= ? AND date < ? GROUP BY game", (s, e))}
    builds = {r[0]: r[1] for r in conn.execute(
        "SELECT f.game, COUNT(*) FROM findings f JOIN builds b ON b.id = f.build_id "
        "WHERE f.change_type = 'build' AND b.date >= ? AND b.date < ? GROUP BY f.game", (s, e))}
    rows = [dict(r) for r in conn.execute(
        "SELECT f.*, b.url AS build_url, b.date AS build_date FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.score >= 50 AND f.change_type NOT IN ('baseline', 'present', 'build') "
        "AND COALESCE(b.date, f.created_at) >= ? AND COALESCE(b.date, f.created_at) < ? ORDER BY f.score DESC",
        (s, e))]
    generic = set(cfg.get("ai.generic_keywords") or DEFAULT_GENERIC)
    relevant = [r for r in rows if not is_generic(r, generic)]
    evidence, _ = _evidence(relevant, 1, "settimana")
    for item in evidence:
        item.pop("_key", None)
    daily = []
    for a in db.analyses(20):
        if s <= a["created_at"] < iso(end + timedelta(hours=12)):
            c = json.loads(a["content"])
            daily.append({"data": local_day(a["created_at"]), "titolo": c.get("titolo"), "sintesi": c.get("sintesi"),
                          "novita_rilevanti": c.get("novita_rilevanti")})
    games = sorted(set(commits) | set(builds), key=lambda g: -(commits.get(g, 0) + builds.get(g, 0)))
    return {
        "settimana": {"dal": monday.isoformat(), "al": sunday.isoformat()},
        "per_gioco": [{"gioco": cfg.game_title(g) if g != "steam" else "Steam (nuove app)",
                       "commit": commits.get(g, 0), "build": builds.get(g, 0)} for g in games],
        "voci_rilevanti": len(relevant),
        "alta_rilevanza": sum(1 for r in relevant if r["score"] >= 100),
        "stringhe_mai_viste": sum(1 for r in relevant if r.get("is_new")),
        "voci": evidence[:40],
        "analisi_giornaliere": list(reversed(daily)),
    }


def ai_summary(cfg: Config, db: Database, data: dict[str, Any], client: Any = None) -> dict[str, Any] | None:
    analyzer = Analyzer(cfg, db, client)
    if not analyzer.enabled():
        return None
    budget = float((cfg.get("ai", {}) or {}).get("monthly_budget_usd", 10))
    if analyzer.month_spend() >= budget:
        log.warning("Riepilogo settimanale: limite mensile dell'AI raggiunto, solo numeri")
        return None
    prompt = ("Questa volta non serve l'analisi giornaliera: scrivi il RIEPILOGO DELLA SETTIMANA per un messaggio "
              "Telegram, breve e chiaro per un appassionato non tecnico. Basati sulle voci della settimana e sulle "
              "analisi giornaliere già fatte; non ripetere i numeri per gioco, che il messaggio mostra già. "
              "Se la settimana non dice niente di nuovo su HLX, dillo con semplicità. Non citare gli identificativi "
              "delle prove (E1, E2...): il messaggio non li mostra.\n\n"
              + json.dumps(data, ensure_ascii=False, default=str))
    content, cost = structured_call(db, analyzer.client, prompt=prompt, schema=WEEKLY_SCHEMA,
                                    label="Riepilogo settimanale", effort="medium", max_tokens=4000)
    if content is not None:
        log.info("Riepilogo settimanale: sintesi AI pronta (costo %.4f $)", cost)
    return content


def weekly_text(data: dict[str, Any], ai: dict[str, Any] | None, monday: date, sunday: date,
                site: str = SITE_URL) -> str:
    lines: list[str] = []
    if ai:
        ai = {k: (EVIDENCE_RE.sub("", v) if isinstance(v, str) else [EVIDENCE_RE.sub("", x) for x in v]
                  if isinstance(v, list) else v) for k, v in ai.items()}
        lines += [ai.get("sintesi", "").strip(), ""]
    elif not data["voci_rilevanti"]:
        lines += ["Settimana tranquilla: nessuna voce rilevante su HLX.", ""]
    lines.append("Aggiornamenti della settimana:")
    if data["per_gioco"]:
        for g in data["per_gioco"]:
            parts = [f"{g['commit']} commit" if g["commit"] else "", f"{g['build']} build" if g["build"] else ""]
            lines.append(f"• {g['gioco']}: {', '.join(p for p in parts if p)}")
    else:
        lines.append("• nessun aggiornamento")
    lines.append(f"Voci rilevanti: {data['voci_rilevanti']} (ad alta rilevanza: {data['alta_rilevanza']}, "
                 f"stringhe mai viste: {data['stringhe_mai_viste']})")
    if ai and ai.get("punti"):
        lines += ["", "Punti principali:"] + [f"• {p}" for p in ai["punti"][:5]]
    if ai and ai.get("da_osservare"):
        lines += ["", "Da tenere d'occhio:"] + [f"• {p}" for p in ai["da_osservare"][:3]]
    lines += ["", f"Tutte le voci della settimana: {site}search.html?since={monday.isoformat()}"
                  f"&until={quote(sunday.isoformat())}&min=50"]
    return "\n".join(lines)


def weekly_title(monday: date, sunday: date) -> str:
    return f"HLX Watch: riepilogo della settimana {week_label(monday, sunday)}"


def maybe_weekly(cfg: Config, db: Database, notifiers: list, report_time: str, *, force: bool = False,
                 send: bool = True, client: Any = None) -> str | None:
    """Una volta per settimana, alla prima occasione da lunedì dopo `report_time`: restituisce il testo inviato."""
    if not (cfg.get("weekly", {}) or {}).get("enabled", True) and not force:
        return None
    now = now_local()
    monday, sunday = last_week(now.date())
    week_id = monday.isoformat()
    if not force:
        if db.get_state("weekly:last") == week_id:
            return None
        if now.date() == monday + timedelta(days=7) and now.strftime("%H:%M") < report_time:
            return None                      # lunedì mattina presto: si aspetta l'ora del report
    data = collect_week(db, cfg, monday, sunday)
    ai = ai_summary(cfg, db, data, client) if data["voci_rilevanti"] else None
    text = weekly_text(data, ai, monday, sunday, str(cfg.get("general.site_url", SITE_URL)))
    if send:
        from hlxwatch.notifiers import notify_text
        label = week_label(monday, sunday)
        if not notifiers:
            db.set_state("weekly:last", week_id)       # nessuno a cui inviarlo: inutile ritentare a ogni giro
            log.info("Riepilogo settimanale (%s): nessun notificatore configurato", label)
        elif notify_text(notifiers, weekly_title(monday, sunday), text):
            db.set_state("weekly:last", week_id)       # segnato come inviato solo se l'invio è riuscito
            log.info("Riepilogo settimanale inviato (%s)", label)
        else:
            log.warning("Riepilogo settimanale (%s) non inviato: si ritenta al prossimo giro", label)
    return text
