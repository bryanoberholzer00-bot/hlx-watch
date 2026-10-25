"""Sito statico della dashboard: generato a ogni scansione e pubblicato su GitHub Pages.

Non serve un server: la ricerca nello storico avviene nel browser su findings.json.
"""
from __future__ import annotations

import json
import shutil
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.dashboard.app import _row, group_rows, timeline_svg
from hlxwatch.reports.daily import collect_report_data, render_html
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow
from hlxwatch.links import link_label
from hlxwatch.timeutil import local_day, now_local, register_filters, tz_label

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "site_templates"


def export_findings(db: Database, limit: int = 25000) -> list[dict[str, Any]]:
    rows = db.search_findings(limit=limit, order="created_at", exclude_change_type="baseline")
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _row(row)
        out.append({
            "id": item["id"], "score": item["score"], "game": item["game"], "source": item["source"],
            "type": item["change_type"], "token": item.get("token"), "text": (item["text"] or "")[:200],
            "file": item["file_path"], "line": item.get("line_no"), "day": item["day"],
            "seen": local_day(item.get("created_at")), "new": bool(item["is_new"]),
            "url": item.get("build_url"), "ul": link_label(item.get("build_url")), "ref": (item.get("build_ref") or "")[:10], "kw": item["keywords"],
        })
    return out


def load_analysis(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["content"] = json.loads(item["content"]) if item.get("content") else {}
    return item


def present_groups(db: Database, limit: int = 60) -> list[dict[str, Any]]:
    """Occorrenze 'già presenti' (scansione completa) raggruppate per cartella e parola chiave:
    281 file di animazione nella stessa cartella diventano una riga sola."""
    rows = [_row(r) for r in db.search_findings(change_type="present", limit=20000, order="score")]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        folder = "/".join(r["file_path"].split("/")[:3])
        kw = (r["keywords"] or ["?"])[0]
        g = groups.setdefault((folder, kw), {"folder": folder, "keyword": kw, "games": set(), "count": 0,
                                             "score": 0, "example": r, "files": set()})
        g["games"].add(r["game"])
        g["count"] += 1
        g["files"].add(r["file_path"])
        g.setdefault("lines", []).append((r["game"], r["file_path"], r["text"]))
        if r["score"] > g["score"]:
            g["score"], g["example"] = r["score"], r
    out = sorted(groups.values(), key=lambda g: (-g["score"], -len(g["games"]), g["folder"]))
    for g in out:
        g["games"] = sorted(g["games"])
        g["n_files"] = len(g["files"])
        # prima comparsa del gruppo: la data più vecchia tra le righe già datate
        from hlxwatch.sources.first_seen import line_hash
        dates = []
        for game, path, text in g.pop("lines", []):
            fs = db.get_first_seen(game, path, line_hash(text))
            if fs and fs["commit_date"]:
                dates.append((local_day(fs["commit_date"]), bool(fs["at_least"])))
        g["first_seen"] = min(dates) if dates else None
        g["severity"] = "high" if g["score"] >= 100 else "medium" if g["score"] >= 50 else "low"
    return out[:limit]


def build_site(db: Database, cfg: Config, out_dir: Path, timeline_days: int = 180) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    titles = {g: ("Steam (nuove app)" if g == "steam" else cfg.game_title(g)) for g in db.stats()["games"]}
    env.globals.update(version=__version__, titles=titles,
                       generated=f"{now_local().strftime('%Y-%m-%d %H:%M')} ({tz_label()})")
    register_filters(env)
    push = cfg.get("notifiers.webpush", {}) or {}
    env.globals["push_url"] = str(push.get("worker_url", "")).rstrip("/") if push.get("enabled") else ""

    stats = db.stats()
    analyses = [load_analysis(r) for r in db.analyses(60)]
    high = group_rows([_row(r) for r in db.search_findings(min_score=100, limit=300, order="created_at",
                                                             exclude_change_type=("baseline", "present"))])
    recent = group_rows([_row(r) for r in db.search_findings(min_score=50, limit=80, order="created_at",
                                                               exclude_change_type=("baseline", "present"))])
    index_html = env.get_template("index.html").render(
        stats=stats, high=high, recent=recent[:40],
        chart=timeline_svg(db.timeline(days=timeline_days), timeline_days, link="search.html?since={day}&until={day}"),
        timeline_days=timeline_days, tokens=[dict(r) for r in db.top_tokens(25)],
        builds=[dict(r) for r in db.recent_builds(15)], apps=[dict(r) for r in db.flagged_apps(20)],
        steam_builds=[dict(r) for r in db.steam_builds(12)], present=present_groups(db),
        analysis=analyses[0] if analyses else None,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    kw = cfg.keywords
    (out_dir / "metodologia.html").write_text(env.get_template("metodologia.html").render(
        repos=tracked_repos(cfg, db), steam_apps=[a.get("title", a.get("name")) for a in cfg.steam_watch_apps],
        full_scan={**{"interval_hours": 24, "min_score": 50, "prefilter": ""}, **(cfg.get("full_scan", {}) or {})},
        keywords=kw.get("items", []), codenames=kw.get("codenames", []) or [], codename_weight=kw.get("codename_weight", 30),
        bonuses=kw.get("bonuses", {}) or {}, severity={"high": 100, "medium": 50, **(kw.get("severity") or {})},
        global_excludes=kw.get("global_excludes", []) or [], notify_min_score=cfg.get("general.notify_min_score", 50),
        ai_model="Claude Opus 5.5", repo_url=str(cfg.get("general.repo_url", "https://github.com/bryanoberholzer00-bot/hlx-watch")),
    ), encoding="utf-8")
    (out_dir / "analisi.html").write_text(env.get_template("analisi.html").render(analyses=analyses),
                                           encoding="utf-8")
    (out_dir / "search.html").write_text(env.get_template("search.html").render(games=stats["games"]), encoding="utf-8")

    findings = export_findings(db)
    from hlxwatch.reports.bot_data import export_bot_data
    bot_info = export_bot_data(db, cfg, out_dir, str(cfg.get("general.site_url",
                                                             "https://bryanoberholzer00-bot.github.io/hlx-watch/")))
    log.info("Dati per il bot: %d ultime, indice di %d voci (%d KB)", bot_info["ultime"], bot_info["indice"],
             bot_info["indice_kb"])
    (out_dir / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")

    reports_dir = out_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    # finestre mobili che finiscono adesso, come il report su Telegram: al mattino "oggi" sarebbe quasi vuoto
    end = utcnow().replace(second=0, microsecond=0)
    for days, label in ((1, "ultime 24 ore"), (7, "ultimi 7 giorni"), (30, "ultimi 30 giorni")):
        data = collect_report_data(db, cfg, end - timedelta(days=days), end)
        data["label"] = label
        (reports_dir / f"report-{days}d.html").write_text(render_html(data), encoding="utf-8")
    # icone e manifest: il sito si può aggiungere alla schermata Home ed è la base dell'app Android
    static = Path(__file__).parent / "site_static"
    for f in [*static.glob("*.png"), static / "sw.js"]:       # sw.js: riceve le notifiche di sistema
        shutil.copyfile(f, out_dir / f.name)
    (out_dir / "manifest.webmanifest").write_text(json.dumps({
        "name": "HLX Watch", "short_name": "HLX Watch",
        "description": "Riferimenti a HLX nei dati pubblici dei giochi Valve su Source 2",
        "start_url": "./", "scope": "./", "display": "standalone", "lang": "it",
        "background_color": "#15181e", "theme_color": "#1f2430",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / ".nojekyll").touch()
    log.info("Sito generato in %s: %d voci esportate, %d ad alta rilevanza", out_dir, len(findings), len(high))
    return out_dir
