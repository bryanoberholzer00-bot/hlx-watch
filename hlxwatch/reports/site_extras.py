"""Aiuti per le pagine del sito: link allo Schema Explorer, area di un cambiamento, stato delle fonti, hardware Valve.

- **Schema Explorer** (s2v.app, dai dump di DumpSource2): ogni classe ha una pagina per gioco e modulo, per esempio
  https://s2v.app/SchemaExplorer/cs2/server/CPulseCell_Outflow_PlayDynamicVCD. Le voci che vengono da un file
  `DumpSource2/schemas/<modulo>/<Classe>.h` ricevono il link alla classe.
- **Aree**: le dieci aree in cui l'azione `commit-summary` di SteamTracking divide ogni aggiornamento, ricavate qui
  dal percorso del file, per filtrare la Ricerca.
- **Stato delle fonti**: ultimo commit di SteamTracking e ultimo riassunto AI per repository, ultimi run e salti di
  Steam, fonti con errori. Tutto dipende da un solo manutentore: se una fonte tace, si vede.
- **Hardware Valve**: driver, modelli 3D e giochi con i comandi per Steam Frame in SteamVR, e le voci sull'hardware.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from hlxwatch.config import Config
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import parse_iso, utcnow
from hlxwatch.timeutil import local_day

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ Schema Explorer
SCHEMA_GAMES = {"cs2": "cs2", "dota2": "dota2", "deadlock": "deadlock"}
SCHEMA_PATH = re.compile(r"^DumpSource2/schemas/(?P<module>[A-Za-z0-9_]+)/(?P<cls>[A-Za-z0-9_]+)\.h$")


def schema_url(game: str, path: str) -> str | None:
    """Pagina della classe su s2v.app, solo per i file di schema dei giochi che lo Schema Explorer conosce."""
    m = SCHEMA_PATH.match(path or "")
    slug = SCHEMA_GAMES.get(game)
    if not m or not slug:
        return None
    return f"https://s2v.app/SchemaExplorer/{slug}/{m.group('module')}/{m.group('cls')}"


# ------------------------------------------------------------------ aree e tipo di prova (hlxwatch/evidence.py)
from hlxwatch.evidence import AREA_LABELS, AREAS, area_of, evidence_of  # noqa: E402,F401


# ------------------------------------------------------------------ stato delle fonti
MAIN_GAMES = ("cs2", "dota2", "deadlock")
GAP_UNUSUAL_HOURS = 3          # Steam elenca le app cambiate per circa 3-4 ore: un salto dopo meno ore è insolito
STALE_DAYS = 14                # i giochi principali ricevono commit ogni settimana: due settimane di silenzio sono strane


def _days_ago(value: str | None) -> int | None:
    when = parse_iso(value) if value else None
    return None if when is None else max(0, (utcnow() - when).days)


def fast_status(cfg: Config) -> dict[str, Any] | None:
    """Stato del controllo veloce dell'annuncio, letto dal bot su Cloudflare (fast_check.status_url)."""
    url = (cfg.get("fast_check", {}) or {}).get("status_url")
    if not url:
        return None
    import requests
    try:
        data = requests.get(url, timeout=10).json()
    except Exception as exc:                      # il bot non risponde: il sito lo dice, senza fermarsi
        log.warning("Controllo veloce: stato non leggibile (%s)", exc)
        return {"ultimo_ok": None, "minuti": None, "fermo": True, "irraggiungibile": True}
    return data if isinstance(data, dict) else None


def source_status(cfg: Config, db: Database, fast: dict[str, Any] | None = None) -> dict[str, Any]:
    """Una riga per repository seguito più lo stato generale, per la tabella "Stato delle fonti"."""
    summaries: dict[str, str] = {}
    for key, value in db.conn.execute("SELECT key, value FROM state WHERE key LIKE 'summary:%'"):
        game = key.split(":")[1]
        try:
            created = json.loads(value).get("created_at") or ""
        except (ValueError, AttributeError):
            continue
        if created > summaries.get(game, ""):
            summaries[game] = created
    rows = []
    for repo in tracked_repos(cfg, db):
        name = repo["name"]
        last = db.conn.execute(
            "SELECT MAX(date) FROM builds WHERE game = ? AND source = 'github' AND length(ref) = 40 "
            "AND COALESCE(message, '') NOT LIKE '[archeologia]%'", (name,)).fetchone()[0]
        days = _days_ago(last)
        rows.append({
            "name": name, "title": repo.get("title", name), "repo": repo["repo"],
            "last_commit": local_day(last) if last else None, "days": days,
            "summary": local_day(summaries[name]) if name in summaries else None,
            "stale": name in MAIN_GAMES and (days is None or days > STALE_DAYS),
        })
    problems = []
    for key, value in db.conn.execute("SELECT key, value FROM state WHERE key LIKE 'health:%'"):
        try:
            info = json.loads(value)
        except ValueError:
            continue
        if not isinstance(info, dict):
            continue                 # health:upstream_alerted e simili sono solo "1"/"0"
        if int(info.get("fails") or 0) > 0:
            problems.append({"source": key.split(":", 1)[1], "fails": int(info["fails"]),
                             "last_ok": local_day(info.get("last_ok")) if info.get("last_ok") else None})
    gap = db.get_state("steam:changes_gap_at")
    try:
        gap_hours = float(db.get_state("steam:changes_gap_hours") or "nan")
    except ValueError:
        gap_hours = float("nan")
    from hlxwatch.announcement import drill_state
    from hlxwatch.restore_check import state as restore_state
    return {
        "drill": drill_state(db),
        "restore": restore_state(db),
        "repos": rows, "problems": problems,
        "last_scan": db.get_state("last_scan_at"), "last_fullscan": db.get_state("fullscan:last_at"),
        # con i run ogni 4-5 ore Steam salta quasi sempre l'elenco: si mostra solo se è successo dopo poche ore
        "changes_gap": gap, "changes_gap_hours": None if gap_hours != gap_hours else round(gap_hours, 1),
        "changes_gap_recent": bool(gap and (_days_ago(gap) or 0) < 2 and gap_hours < GAP_UNUSUAL_HOURS),
        "fast": fast,
    }


# ------------------------------------------------------------------ hardware Valve
FCC_GRANTEE = "2AES4"
FCC_URL = "https://www.fcc.gov/oet/ea/fccid"


def _state_list(db: Database, key: str) -> list[str]:
    raw = db.get_state(key)
    try:
        return sorted(json.loads(raw)) if raw else []
    except ValueError:
        return []


def hardware_data(cfg: Config, db: Database, limit: int = 150) -> dict[str, Any]:
    seen = json.loads(db.get_state("codenames:seen") or "{}")
    devices = sorted(name for name, info in seen.items() if isinstance(info, dict) and info.get("posto") == "vr_device")
    rows = db.conn.execute(
        "SELECT f.*, b.url AS build_url, b.date AS build_date FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.change_type NOT IN ('baseline', 'present') AND (f.game = 'steamvr' OR f.keywords LIKE '%valve_hardware%' "
        "OR (f.change_type = 'codename' AND f.context LIKE '%SteamVR%')) ORDER BY COALESCE(b.date, f.created_at) DESC LIMIT ?",
        (limit,)).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["day"] = local_day(item.get("build_date") or item.get("created_at"))
        items.append(item)
    return {
        "drivers": _state_list(db, "structure:steamvr:drivers"),
        "rendermodels": _state_list(db, "structure:steamvr:resources/rendermodels"),
        "frame_games": _state_list(db, "structure:steamvr:drivers/frame_controller/resources/input"),
        "devices": devices, "findings": items, "fcc_grantee": FCC_GRANTEE, "fcc_url": FCC_URL,
    }
