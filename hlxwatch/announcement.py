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
HLX_FAMILY = ("hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hl3", "half_life_3", "half_life",
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
