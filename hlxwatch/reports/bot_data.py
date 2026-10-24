"""Dati compatti per i comandi del bot Telegram (/ultime, /cerca, /stato).

Il Worker di Cloudflare ha pochi millisecondi di calcolo per richiesta: invece dei 4 MB di
findings.json legge questi tre file piccoli, generati a ogni run nella cartella bot/ del sito.
Orari e titoli sono già pronti in ora italiana, così il Worker deve solo impaginarli.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hlxwatch.config import Config
from hlxwatch.analysis import DEFAULT_GENERIC, is_generic
from hlxwatch.dashboard.app import _row
from hlxwatch.sources.first_seen import line_hash
from hlxwatch.repos import followed_repos
from hlxwatch.storage import Database
from hlxwatch.health import Health
from hlxwatch.storage.db import iso, utcnow
from hlxwatch.timeutil import local, local_day, now_local

INDEX_MIN_SCORE = 40


def _title(cfg: Config, game: str) -> str:
    return "Steam (nuove app)" if game == "steam" else cfg.game_title(game)


def _item(cfg: Config, db: Database, r: dict[str, Any]) -> dict[str, Any]:
    day = r["day"]
    if r["change_type"] == "present":
        # per le voci già presenti conta la prima comparsa, non il giorno della scansione
        fs = db.get_first_seen(r["game"], r["file_path"], line_hash(r["text"]))
        day = (("dal " if not fs["at_least"] else "almeno dal ") + local_day(fs["commit_date"]))             if fs and fs["commit_date"] else "già presente"
    return {
        "s": r["score"], "g": _title(cfg, r["game"]), "t": r["change_type"],
        "k": (r.get("token") or "")[:80], "x": (r.get("text") or "")[:140], "f": (r.get("file_path") or "")[:90],
        "d": day, "n": int(r.get("dup_count") or 1), "u": r.get("build_url") or "",
    }


def _group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Come sul sito, ma le voci già presenti si raggruppano per riga: righe diverse con lo stesso token
    (per esempio 'HLX' nel bug tracker e nella FGD) restano separate."""
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        label = r["text"] if r["change_type"] == "present" else (r.get("token") or r["text"])
        key = (" ".join(label.split()).lower(), r.get("build_id") if r["change_type"] != "present" else r["game"],
               r["change_type"])
        if key in out:
            out[key]["dup_count"] += 1
        else:
            out[key] = dict(r, dup_count=1)
    return list(out.values())


def export_bot_data(db: Database, cfg: Config, out_dir: Path, site_url: str) -> dict[str, int]:
    bot = out_dir / "bot"
    bot.mkdir(parents=True, exist_ok=True)

    generic = set(cfg.get("ai.generic_keywords") or DEFAULT_GENERIC)
    recent_rows = [dict(r) for r in db.search_findings(min_score=50, limit=400, order="created_at",
                                                        exclude_change_type=("baseline", "present"))]
    recent = _group([_row(r) for r in recent_rows if not is_generic(r, generic)])
    ultime = [_item(cfg, db, r) for r in recent[:15]]
    (bot / "ultime.json").write_text(json.dumps(ultime, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    rows = _group([_row(r) for r in db.search_findings(min_score=INDEX_MIN_SCORE, limit=20000, order="score",
                                                        exclude_change_type="baseline")])
    index = [_item(cfg, db, r) for r in rows]
    (bot / "indice.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    stats = db.stats()
    analysis = db.latest_analysis()
    content = json.loads(analysis["content"]) if analysis else {}
    stato = {
        "aggiornato": now_local().strftime("%Y-%m-%d %H:%M"),
        "aggiornato_utc": iso(utcnow()),
        "ultima_scansione_utc": stats.get("last_scan") or "",
        "fonti_in_errore": [v["label"] + f" ({v['fails']} run)" for v in Health(cfg, db, []).summary().values()
                            if not v["ok"]],
        "ultima_scansione": local(stats.get("last_scan")) if stats.get("last_scan") else "mai",
        "voci": stats["findings"], "alta": stats["findings_high"], "nuove": stats["findings_new"],
        "gia_presenti": stats.get("findings_present", 0), "commit": stats["builds"],
        "build_steam": stats["steam_builds"], "app_sospette": stats["steam_apps_flagged"],
        "giochi": [_title(cfg, g) for g in stats["games"] if g not in ("steam", "steamtracking")],
        "repo_scoperti": [r["repo"] for r in followed_repos(db)],
        "report_giornaliero": db.get_state("daemon:last_report_day") or "mai",
        "analisi": ({"data": local(analysis["created_at"]), "titolo": content.get("titolo", ""),
                     "novita_rilevanti": bool(content.get("novita_rilevanti"))} if analysis else None),
        "sito": site_url, "soglia_indice": INDEX_MIN_SCORE,
    }
    (bot / "stato.json").write_text(json.dumps(stato, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ultime": len(ultime), "indice": len(index),
            "indice_kb": (bot / "indice.json").stat().st_size // 1024}
