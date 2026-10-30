"""Dashboard web locale (FastAPI + Jinja2): ricerca nello storico e timeline delle scoperte."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.links import duplicates_label
from hlxwatch.reports import collect_report_data, period_for, render_html
from hlxwatch.storage import Database
from hlxwatch.timeutil import local_day, local_day_bounds_utc, register_filters

TEMPLATES_DIR = Path(__file__).parent / "templates"
PER_PAGE = 50
CHANGE_TYPES = ["added", "removed", "changed", "present", "file_added", "file_removed", "file_renamed", "build", "new_app", "new_repo", "baseline"]
SOURCES = ["github", "discovery", "valve_github", "certs", "fullscan", "steam_pics", "steam_apps", "local"]


def _row(row: Any) -> dict[str, Any]:
    item = dict(row)
    try:
        item["keywords"] = json.loads(item.get("keywords") or "[]")
    except (json.JSONDecodeError, TypeError):
        item["keywords"] = []
    score = item.get("score", 0)
    item["severity"] = "high" if score >= 100 else "medium" if score >= 50 else "low"
    # giorno della modifica di Valve (data del commit/build); la scoperta può essere molto dopo (backfill)
    item["day"] = local_day(item.get("build_date") or item.get("created_at"))
    return item


def group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accorpa le voci con lo stesso token, commit e tipo (es. una stringa presente in 9 dump di binari)."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        key = ((row.get("token") or row.get("text") or "").lower(), row.get("build_id"), row.get("change_type"))
        if key in grouped:
            g = grouped[key]
            g["dup_count"] += 1
            g["dup_files"].append(row["file_path"])
            g["score"] = max(g["score"], row["score"])
            g["is_new"] = g["is_new"] or row["is_new"]
        else:
            row = dict(row, dup_count=1, dup_files=[])
            grouped[key] = row
            order.append(key)
    out = [grouped[k] for k in order]
    for g in out:
        g["dup_label"] = duplicates_label(g["file_path"], g["dup_files"])
        g["dup_short"] = duplicates_label(g["file_path"], g["dup_files"], short=True)
    return out


def timeline_svg(timeline: list[dict[str, Any]], days: int = 90,
                 link: str = "/findings?since={day}&until={day}") -> str:
    """Grafico a barre inline: voci per giorno, colore in base al punteggio massimo.
    `link` è il modello dell'URL di ogni barra (diverso tra dashboard locale e sito statico)."""
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "max": 0, "new": 0})
    for row in timeline:
        d = per_day[row["day"]]
        d["n"] += int(row["n"] or 0)
        d["new"] += int(row["n_new"] or 0)
        d["max"] = max(d["max"], int(row["max_score"] or 0))
    if not per_day:
        return '<p class="muted">Nessun dato ancora: lancia una scansione o il backfill.</p>'
    days_sorted = sorted(per_day)
    width, height, pad = 1000, 180, 24
    bar_w = max(2.0, (width - 2 * pad) / max(len(days_sorted), 1))
    max_n = max(d["n"] for d in per_day.values()) or 1
    parts = [f'<svg viewBox="0 0 {width} {height}" class="timeline" role="img" aria-label="timeline">']
    for i, day in enumerate(days_sorted):
        d = per_day[day]
        h = max(2, (height - 2 * pad) * d["n"] / max_n)
        x = pad + i * bar_w
        y = height - pad - h
        color = "#e53935" if d["max"] >= 100 else "#fb8c00" if d["max"] >= 50 else "#90a4ae"
        title = html.escape(f"{day}: {d['n']} voci ({d['new']} nuove), punteggio max {d['max']}")
        parts.append(f'<a href="{link.format(day=day)}"><rect x="{x:.1f}" y="{y:.1f}" width="{max(1.0, bar_w - 1):.1f}" '
                     f'height="{h:.1f}" fill="{color}"><title>{title}</title></rect></a>')
        if len(days_sorted) <= 31 or i % max(1, len(days_sorted) // 12) == 0:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 6}" font-size="10" text-anchor="middle">{day[5:]}</text>')
    parts.append(f'<text x="{pad}" y="14" font-size="11">max {max_n} voci/giorno (ultimi {days} giorni)</text>')
    parts.append("</svg>")
    return "".join(parts)


def create_app(cfg: Config, db: Database) -> FastAPI:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    register_filters(templates.env)
    templates.env.globals.update(version=__version__, title_of=cfg.game_title,
                                 change_types=CHANGE_TYPES, sources=SOURCES)
    app = FastAPI(title="HLX Watch", docs_url=None, redoc_url=None)

    def games() -> list[str]:
        return db.stats()["games"]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, days: int = Query(90, ge=7, le=730)):
        stats = db.stats()
        highlights = [_row(r) for r in db.search_findings(min_score=50, limit=25, order="created_at",
                                                          exclude_change_type=("baseline", "present"))]
        return templates.TemplateResponse(request, "index.html", {
            "stats": stats, "games": stats["games"], "highlights": highlights,
            "chart": timeline_svg(db.timeline(days=days), days), "days": days,
            "builds": [dict(r) for r in db.recent_builds(12)],
            "tokens": [dict(r) for r in db.top_tokens(20)],
            "apps": [dict(r) for r in db.flagged_apps(10)],
        })

    @app.get("/findings", response_class=HTMLResponse)
    def findings(request: Request, q: str = "", game: str = "", source: str = "", change_type: str = "",
                 min_score: int = 0, only_new: int = 0, since: str = "", until: str = "",
                 order: str = "created_at", group: int = 1, page: int = Query(1, ge=1)):
        from datetime import date as _date
        # i filtri sono giorni del calendario locale, il database è in UTC
        since_dt = local_day_bounds_utc(_date.fromisoformat(since))[0] if since else None
        until_dt = local_day_bounds_utc(_date.fromisoformat(until))[1] if until else None
        rows = db.search_findings(q=q or None, game=game or None, source=source or None,
                                  change_type=change_type or None, min_score=min_score or None,
                                  only_new=bool(only_new), since=since_dt, until=until_dt,
                                  limit=PER_PAGE + 1, offset=(page - 1) * PER_PAGE, order=order)
        has_next = len(rows) > PER_PAGE
        items = [_row(r) for r in rows[:PER_PAGE]]
        if group:
            items = group_rows(items)
        return templates.TemplateResponse(request, "findings.html", {
            "rows": items, "games": games(), "page": page, "has_next": has_next,
            "params": {"q": q, "game": game, "source": source, "change_type": change_type, "min_score": min_score,
                       "only_new": only_new, "since": since, "until": until, "order": order, "group": group},
        })

    @app.get("/finding/{finding_id}", response_class=HTMLResponse)
    def finding(request: Request, finding_id: int):
        row = db.get_finding(finding_id)
        if row is None:
            return HTMLResponse("<h1>Voce non trovata</h1>", status_code=404)
        item = _row(row)
        related = [_row(r) for r in db.search_findings(q=item["token"], limit=30)] if item.get("token") else []
        return templates.TemplateResponse(request, "finding.html", {"f": item, "related": related, "games": games()})

    @app.get("/builds", response_class=HTMLResponse)
    def builds(request: Request, game: str = ""):
        return templates.TemplateResponse(request, "builds.html", {
            "builds": [dict(r) for r in db.recent_builds(200, game or None)],
            "steam_builds": [dict(r) for r in db.steam_builds(100, game or None)],
            "games": games(), "game": game,
        })

    @app.get("/apps", response_class=HTMLResponse)
    def apps(request: Request, all_apps: int = 0):
        rows = db.recent_apps(300) if all_apps else db.flagged_apps(300)
        return templates.TemplateResponse(request, "apps.html", {"apps": [dict(r) for r in rows],
                                                                 "all_apps": all_apps, "games": games()})

    @app.get("/report", response_class=HTMLResponse)
    def report(days: int = Query(1, ge=1, le=365)):
        start, end = period_for(None, days)
        return HTMLResponse(render_html(collect_report_data(db, cfg, start, end)))

    # ------------------------------------------------------------------ API
    @app.get("/api/stats")
    def api_stats():
        return JSONResponse(db.stats())

    @app.get("/api/findings")
    def api_findings(q: str = "", game: str = "", source: str = "", change_type: str = "", min_score: int = 0,
                     only_new: int = 0, limit: int = Query(100, le=1000), offset: int = 0, order: str = "created_at"):
        rows = db.search_findings(q=q or None, game=game or None, source=source or None,
                                  change_type=change_type or None, min_score=min_score or None,
                                  only_new=bool(only_new), limit=limit, offset=offset, order=order)
        return JSONResponse([_row(r) for r in rows])

    @app.get("/api/timeline")
    def api_timeline(days: int = Query(90, le=730), min_score: int = 0):
        return JSONResponse(db.timeline(days=days, min_score=min_score))

    return app
