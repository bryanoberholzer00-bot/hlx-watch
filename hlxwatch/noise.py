"""Voci segnate come rumore con il pulsante 👎 sotto gli avvisi di Telegram.

Il bot su Cloudflare registra i voti (id della voce) e li espone in /rumore.json (telegram-welcome/src/noise.js).
A ogni run si leggono i voti e da ogni voce votata si ricava il suo "tipo": gioco, tipo di modifica, parola chiave
principale e area dei file (per esempio: Deadlock, localizzazione, half_life, traduzioni). Le voci nuove dello stesso
tipo restano in archivio e sul sito ma non mandano più avvisi. Ripremere il pulsante annulla il voto: le regole si
ricalcolano ogni volta da capo dai voti attuali. Non si possono silenziare le tracce HLX forti né i possibili annunci.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.evidence import AREA_LABELS, area_of
from hlxwatch.models import Finding
from hlxwatch.storage import Database

log = logging.getLogger(__name__)

STATE = "rumore:regole"
DEFAULT_URL = "https://hlx-watch-bot.bryan-oberholzer00.workers.dev/rumore.json"
PROTECTED = {"hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hl3", "half_life_3", "possible_announcement"}


def protected(keywords: list[str]) -> bool:
    return bool(set(keywords or []) & PROTECTED)


def rule_of(game: str, change_type: str, keywords: list[str], file_path: str) -> str:
    kw = next((k for k in keywords or [] if k != "possible_announcement"), "")
    return f"{game}|{change_type}|{kw}|{area_of(file_path or '', change_type)}"


def describe(cfg: Config, rule: str) -> str:
    game, change_type, kw, area = (rule.split("|") + ["", "", "", ""])[:4]
    parts = [cfg.game_title(game), change_type]
    if kw:
        parts.append(kw)
    parts.append(AREA_LABELS.get(area, area))
    return " · ".join(p for p in parts if p)


def refresh(cfg: Config, db: Database, session: requests.Session | None = None) -> dict[str, Any] | None:
    """Legge i voti dal bot e ricalcola le regole. None (regole invariate) se il bot non risponde."""
    ncfg = cfg.get("noise", {}) or {}
    if not ncfg.get("enabled", True):
        return None
    url = str(ncfg.get("url") or DEFAULT_URL)
    s = session or requests
    try:
        r = s.get(url, timeout=15, headers={"User-Agent": f"hlx-watch/{__version__}"})
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        votes = (r.json() or {}).get("voti") or {}
    except Exception as exc:                       # il bot non risponde: restano le regole dell'ultimo run
        log.info("Voti 👎 non letti dal bot: %s", exc)
        return None
    rules: dict[str, dict[str, Any]] = {}
    for vid, at in votes.items():
        try:
            row = db.conn.execute("SELECT game, change_type, keywords, file_path, token, text FROM findings WHERE id = ?",
                                  (int(vid),)).fetchone()
        except ValueError:
            continue
        if row is None:
            continue
        keywords = json.loads(row["keywords"] or "[]")
        if protected(keywords):
            continue
        key = rule_of(row["game"], row["change_type"], keywords, row["file_path"])
        entry = rules.setdefault(key, {"voti": 0, "esempio": (row["token"] or row["text"] or "")[:100],
                                       "descrizione": describe(cfg, key), "dal": str(at)[:10]})
        entry["voti"] += 1
    db.set_state(STATE, json.dumps(rules, ensure_ascii=False, sort_keys=True))
    if rules:
        log.info("Tipi di voce silenziati con 👎: %d", len(rules))
    return rules


def rules(db: Database) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(db.get_state(STATE) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def muted(finding: Finding, active: dict[str, Any]) -> bool:
    if not active or protected(finding.keywords):
        return False
    return rule_of(finding.game, finding.change_type, finding.keywords, finding.file_path) in active
