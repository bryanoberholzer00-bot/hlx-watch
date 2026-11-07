"""Piano annuncio: cosa fa HLX Watch se Valve annuncia un gioco Half-Life.

Un annuncio si vede da due parti: un'app Steam nuova di Valve con un nome Half-Life (la pagina store, che per Alyx è
uscita con il trailer) e un repository nuovo di SteamTracking (GameTracking-HalfLife3 o simile). Senza un piano,
nel repository del gioco "hlx" comparirebbe in ogni riga e ogni aggiornamento manderebbe decine di voci HLX.
Qui:
  - un repository o un'app con HLX o Half-Life nel nome diventa un solo avviso "possibile annuncio", con
    punteggio alto, titolo dedicato e notifica anche nell'orario di silenzio;
  - nel repository del gioco le parole della famiglia HLX vengono ignorate, come per Half-Life: Alyx;
  - l'app Steam viene aggiunta da sola ai giochi seguiti (stato `steam:watch_extra`), così se ne registrano
    build e rami.
"""
from __future__ import annotations

import json
import re
from typing import Any

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.storage import Database

HLX_NAME = re.compile(r"hlx|half[\s_-]*life|(?<![a-z0-9])hl[\s_-]?3(?![0-9])", re.IGNORECASE)
HLX_FAMILY = ("hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hlx_game_type", "hl3", "half_life_3", "half_life",
              "hl_universe", "hl2_achievement")
KEYWORD = "possible_announcement"
HEADING = "HLX Watch: POSSIBILE ANNUNCIO di Valve"
STATE_WATCH = "steam:watch_extra"
TEXT_PREFIX = "POSSIBILE ANNUNCIO: "


def enabled(cfg: Config) -> bool:
    return bool((cfg.get("announcement", {}) or {}).get("enabled", True))


def score(cfg: Config) -> int:
    return int((cfg.get("announcement", {}) or {}).get("score", 200))


def looks_like_hlx(*texts: str | None) -> bool:
    return any(t and HLX_NAME.search(t) for t in texts)


def hlx_family(cfg: Config) -> list[str]:
    """Le parole chiave da ignorare nel repository del gioco: quelle della famiglia HLX presenti nel config."""
    ids = [str(k.get("id")) for k in (cfg.keywords.get("items") or [])]
    return [i for i in ids if i in HLX_FAMILY or i.startswith("hlx")]


def announcement_heading(alerts: list[Finding]) -> str | None:
    return HEADING if any(KEYWORD in (f.keywords or []) for f in alerts) else None


def mark(finding: Finding, cfg: Config) -> Finding:
    """Trasforma una voce in avviso di possibile annuncio (una volta sola: il testo cambia solo se non è già marcato)."""
    finding.score = max(finding.score, score(cfg))
    if KEYWORD not in finding.keywords:
        finding.keywords.append(KEYWORD)
    if not finding.text.startswith(TEXT_PREFIX):
        finding.text = TEXT_PREFIX + finding.text
    return finding


# ------------------------------------------------------------------ app Steam aggiunte da sole
def extra_watch(db: Database) -> list[dict[str, Any]]:
    try:
        return json.loads(db.get_state(STATE_WATCH) or "[]")
    except ValueError:
        return []


def add_watch(db: Database, cfg: Config, appid: int, title: str) -> dict[str, Any] | None:
    current = extra_watch(db)
    if any(int(a["appid"]) == appid for a in current) or any(int(a.get("appid", 0)) == appid for a in cfg.steam_watch_apps):
        return None
    slug = re.sub(r"[^a-z0-9]+", "", (title or "").lower()) or f"app{appid}"
    app = {"appid": appid, "name": slug, "title": title or f"App {appid}", "ignore_keywords": hlx_family(cfg),
           "auto": True}
    current.append(app)
    db.set_state(STATE_WATCH, json.dumps(current, ensure_ascii=False))
    return app


# ------------------------------------------------------------------ prova generale
# Un finto "possibile annuncio", ben segnato come prova, mandato su ogni canale (Telegram, notifiche del sito, app
# Android) per controllare tutta la catena prima che serva davvero. Non entra nell'archivio: resta solo lo stato
# `announcement:drill`, che il sito mostra e che mette la prova in bot/ultime.json per DRILL_HOURS ore, il tempo
# perché l'app Android la legga anche se il telefono controlla di rado.
DRILL_STATE = "announcement:drill"
DRILL_HEADING = "HLX Watch: PROVA GENERALE dell'annuncio (non è un annuncio vero)"
DRILL_TEXT = "PROVA GENERALE: Valve non ha annunciato niente. Se leggi questo, l'avviso di annuncio funziona"
DRILL_HOURS = 12


def drill_state(db: Database) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(DRILL_STATE) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("at") else None


def run_drill(cfg: Config, db: Database, notifiers: list) -> bool:
    from hlxwatch.notifiers import notify_findings
    from hlxwatch.storage.db import iso, utcnow
    finding = Finding(game="hlxwatch", source="prova", file_path="prova generale del piano annuncio",
                      change_type="prova", text=DRILL_TEXT, score=score(cfg), keywords=[KEYWORD, "prova"],
                      context="prova lanciata a mano", is_new=True)
    ok = notify_findings(notifiers, [finding], cfg, DRILL_HEADING) if notifiers else False
    db.set_state(DRILL_STATE, json.dumps({"at": iso(utcnow()), "ok": ok, "canali": [n.name for n in notifiers]}))
    return ok


def drill_item(db: Database, site_url: str) -> dict[str, Any] | None:
    """La voce di prova per bot/ultime.json (campo "p": l'app Android la presenta come prova), se è recente."""
    from hlxwatch.storage.db import parse_iso, utcnow
    from hlxwatch.timeutil import local, local_day
    state = drill_state(db)
    when = parse_iso(state["at"]) if state else None
    if when is None or (utcnow() - when).total_seconds() > DRILL_HOURS * 3600:
        return None
    return {"s": 200, "g": "HLX Watch", "t": "prova", "k": f"PROVA GENERALE {local(state['at'])}",
            "x": DRILL_TEXT, "f": "", "d": local_day(state["at"]), "n": 1, "u": f"{site_url}metodologia.html#prova",
            "ul": "Metodologia", "a": 1, "p": 1, "c": state["at"]}


# ------------------------------------------------------------------ dopo l'annuncio
# La modalità annuncio la decide il bot su Cloudflare (telegram-welcome/src/annuncio.js) e il sito la legge da
# /fast/stato.json. Qui i dati della pagina annuncio.html: le voci dell'annuncio, le novità arrivate dopo e, accanto,
# cosa era comparso nei file pubblici dopo l'annuncio di Alyx (alyx_seed.json): quasi niente per 104 giorni, poi le
# voci della promozione in SteamVR negli ultimi giorni prima dell'uscita.
def alyx_after(seed: dict[str, Any] | None, day: int | None = None) -> dict[str, Any] | None:
    from collections import Counter
    from datetime import date
    if not seed or not seed.get("names"):
        return None
    ann, rel = date.fromisoformat(seed["announced"]), date.fromisoformat(seed["released"])
    offsets = sorted((date.fromisoformat(n["day"]) - ann).days for n in seed["names"] if n["day"] >= seed["announced"])
    per_day = Counter(offsets)
    steps = [{"giorno": d, "nomi": c, "data": (ann.toordinal() + d)} for d, c in sorted(per_day.items())]
    for s in steps:
        s["data"] = date.fromordinal(s["data"]).isoformat()
        s["esempi"] = [n["name"] for n in seed["names"] if (date.fromisoformat(n["day"]) - ann).days == s["giorno"]][:3]
    return {"annuncio": seed["announced"], "uscita": seed["released"], "giorni_uscita": (rel - ann).days,
            "dopo": len(offsets), "prima": len(seed["names"]) - len(offsets), "tappe": steps,
            "primo": offsets[0] if offsets else None,
            "fino_a": sum(1 for d in offsets if day is not None and d <= day) if day is not None else None}


def after_page(db: Database, mode: dict[str, Any] | None, seed: dict[str, Any] | None) -> dict[str, Any]:
    if not mode:
        return {"attiva": False, "alyx": alyx_after(seed)}
    since = str(mode.get("da") or "")[:19]            # stesso formato di findings.created_at (2026-11-06T01:10:43Z)
    rows = db.conn.execute(
        "SELECT game, file_path, text, token, score, keywords, created_at FROM findings WHERE created_at >= ? "
        "AND score >= 50 AND change_type NOT IN ('baseline', 'present', 'summary') ORDER BY score DESC, created_at DESC "
        "LIMIT 40", (since,)).fetchall() if since else []
    news = [{"gioco": r[0], "file": r[1], "testo": (r[3] or r[2] or "")[:160], "punteggio": r[4], "quando": r[6],
             "annuncio": KEYWORD in (r[5] or "")} for r in rows]
    day = int(mode.get("giorno") or 1)
    return {"attiva": True, "modo": mode, "giorno": day, "novita": news, "alyx": alyx_after(seed, day)}
