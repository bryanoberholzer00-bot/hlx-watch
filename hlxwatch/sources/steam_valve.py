"""La pagina di Valve su Steam: quanti prodotti conta e quanti ne mostra, tutti e quelli in arrivo.

A novembre 2025 la pagina sviluppatore di Valve su Steam contava 5 prodotti in arrivo ma ne elencava 4 (Insider Gaming):
un prodotto c'era già nel negozio, nascosto. Qui si legge la stessa lista che usa la pagina
(store.steampowered.com/developer/valve, l'indirizzo ajaxgetfilteredrecommendations, pubblico e senza chiave; non
SteamDB) due volte, per tutti i prodotti e per quelli in arrivo, al massimo ogni CHECK_HOURS ore. Si salvano il numero
dichiarato (total_count) e le app elencate. Avvisi, uno per cambiamento:
  - il numero dichiarato supera quelle elencate: un prodotto nascosto;
  - un'app nuova nella pagina di Valve (con il nome e il tipo, da appdetails);
  - il numero dei prodotti in arrivo sale.
Il primo giro impara senza avvisi.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

URL = "https://store.steampowered.com/developer/valve/ajaxgetfilteredrecommendations/"
DETAILS = "https://store.steampowered.com/api/appdetails"
STATE = "steam_valve:pagina"
HISTORY = "steam_valve:storia"
CHECK_HOURS = 6
KEEP = 400
APPID_RE = re.compile(r'data-ds-appid=\\?"(\d+)')
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; hlx-watch; +https://github.com/bryanoberholzer00-bot/hlx-watch)",
           "Accept-Language": "en-US"}


def read_list(session: Any, sort: str | None = None) -> dict[str, Any] | None:
    """{"dichiarati": total_count, "app": [appid elencati]} di una lista della pagina, o None se Steam non risponde."""
    params = {"query": "", "start": 0, "count": 100, "tagids": "", "app_types": "", "curations": "", "reset": "false",
              "cc": "us", "l": "english"}
    if sort:
        params["sort"] = sort
    try:
        r = session.get(URL, params=params, headers=HEADERS, timeout=30)
        data = r.json() if r.status_code == 200 else None
    except Exception as exc:                     # rete, JSON non valido: si riprova al giro dopo
        log.warning("Pagina di Valve su Steam non leggibile: %s", exc)
        return None
    if not data or not data.get("success"):
        return None
    ids = list(dict.fromkeys(APPID_RE.findall(data.get("results_html") or "")))
    return {"dichiarati": int(data.get("total_count") or 0), "app": ids}


def app_name(session: Any, appid: str) -> tuple[str, str]:
    try:
        r = session.get(DETAILS, params={"appids": appid, "filters": "basic", "cc": "us", "l": "english"},
                        headers=HEADERS, timeout=30)
        d = (r.json() or {}).get(str(appid)) or {}
        info = d.get("data") or {}
        return str(info.get("name") or f"app {appid}"), str(info.get("type") or "")
    except Exception:
        return f"app {appid}", ""


def changes(old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    """Le novità da segnalare tra due letture (vuoto al primo giro)."""
    out: list[dict[str, Any]] = []
    for key, label in (("tutti", "prodotti"), ("arrivo", "prodotti in arrivo")):
        cur = new[key]
        hidden = cur["dichiarati"] - len(cur["app"])
        before = (old or {}).get(key)
        old_hidden = (before["dichiarati"] - len(before["app"])) if before else 0
        if hidden > 0 and hidden > old_hidden and old is not None:
            out.append({"tipo": "nascosto", "lista": key, "testo":
                        f"La pagina di Valve su Steam conta {cur['dichiarati']} {label} ma ne mostra {len(cur['app'])}: "
                        f"{hidden} {'è nascosto' if hidden == 1 else 'sono nascosti'}."})
        if before is not None:
            for appid in cur["app"]:
                if appid not in before["app"]:
                    out.append({"tipo": "nuova", "lista": key, "appid": appid})
            if key == "arrivo" and cur["dichiarati"] > before["dichiarati"]:
                out.append({"tipo": "sale", "lista": key, "testo":
                            f"I prodotti di Valve in arrivo su Steam passano da {before['dichiarati']} a {cur['dichiarati']}."})
    # la stessa app nuova in tutte e due le liste: un solo avviso
    seen: set[str] = set()
    unique = []
    for c in out:
        if c["tipo"] == "nuova":
            if c["appid"] in seen:
                continue
            seen.add(c["appid"])
        unique.append(c)
    return unique


def _load(db: Any, key: str, default: Any) -> Any:
    try:
        value = json.loads(db.get_state(key) or "null")
    except ValueError:
        return default
    return default if value is None else value


def check(cfg: Any, db: Any, notifiers: list | None = None, session: Any = None, now: datetime | None = None,
          force: bool = False) -> list[dict[str, Any]] | None:
    """Una lettura (se sono passate CHECK_HOURS ore). Ritorna le novità segnalate, o None se non è il momento."""
    now = now or datetime.now(timezone.utc)
    old = _load(db, STATE, None)
    if old and not force:
        try:
            at = datetime.fromisoformat(old["at"].replace("Z", "+00:00"))
            if now - at < timedelta(hours=CHECK_HOURS):
                return None
        except (KeyError, ValueError):
            pass
    if session is None:
        import requests
        session = requests.Session()
    every, upcoming = read_list(session), read_list(session, "upcoming")
    if every is None or upcoming is None:
        return None
    new = {"at": now.isoformat(timespec="seconds"), "tutti": every, "arrivo": upcoming}
    found = changes(old, new)
    for c in found:
        if c["tipo"] == "nuova":
            name, kind = app_name(session, c["appid"])
            c["testo"] = (f"Nuovo prodotto nella pagina di Valve su Steam: {name}"
                          + (f" ({kind})" if kind else "") + f" — https://store.steampowered.com/app/{c['appid']}/")
    db.set_state(STATE, json.dumps(new))
    history = _load(db, HISTORY, [])
    row = {"giorno": now.date().isoformat(), "tutti": every["dichiarati"], "tutti_elencati": len(every["app"]),
           "arrivo": upcoming["dichiarati"], "arrivo_elencati": len(upcoming["app"])}
    if not history or {k: v for k, v in history[-1].items() if k != "giorno"} != {k: v for k, v in row.items() if k != "giorno"}:
        history.append(row)                    # solo quando qualcosa cambia (e il primo giorno)
        db.set_state(HISTORY, json.dumps(history[-KEEP:]))
    if found and notifiers:
        from hlxwatch.notifiers import notify_text
        notify_text(notifiers, "HLX Watch: la pagina di Valve su Steam è cambiata", "🛒 " + "\n".join(c["testo"] for c in found))
    if found:
        log.info("Pagina di Valve su Steam: %d novità", len(found))
    return found


def summary(db: Any) -> dict[str, Any] | None:
    cur = _load(db, STATE, None)
    if not cur:
        return None
    return {"at": cur["at"], "tutti": cur["tutti"]["dichiarati"], "tutti_elencati": len(cur["tutti"]["app"]),
            "arrivo": cur["arrivo"]["dichiarati"], "arrivo_elencati": len(cur["arrivo"]["app"]),
            "arrivo_app": cur["arrivo"]["app"], "storia": list(reversed(_load(db, HISTORY, [])))[:20]}
