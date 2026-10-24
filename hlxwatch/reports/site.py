"""Sito statico della dashboard: generato a ogni scansione e pubblicato su GitHub Pages.

Non serve un server: la ricerca nello storico avviene nel browser su findings.json.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.dashboard.app import _row, group_rows, timeline_svg
from hlxwatch.reports.daily import collect_report_data, period_for, render_html
from hlxwatch.storage import Database

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
            "seen": (item.get("created_at") or "")[:10], "new": bool(item["is_new"]),
            "url": item.get("build_url"), "ref": (item.get("build_ref") or "")[:10], "kw": item["keywords"],
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
                dates.append((fs["commit_date"][:10], bool(fs["at_least"])))
        g["first_seen"] = min(dates) if dates else None
        g["severity"] = "high" if g["score"] >= 100 else "medium" if g["score"] >= 50 else "low"
    return out[:limit]


def build_site(db: Database, cfg: Config, out_dir: Path, timeline_days: int = 180) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    titles = {g: ("Steam (nuove app)" if g == "steam" else cfg.game_title(g)) for g in db.stats()["games"]}
    env.globals.update(version=__version__, titles=titles,
                       generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

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
        repos=cfg.github_repos, steam_apps=[a.get("title", a.get("name")) for a in cfg.steam_watch_apps],
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
    (out_dir / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")

    reports_dir = out_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    for days in (1, 7, 30):
        start, end = period_for(None, days)
        (reports_dir / f"report-{days}d.html").write_text(render_html(collect_report_data(db, cfg, start, end)),
                                                          encoding="utf-8")
    (out_dir / ".nojekyll").touch()
    log.info("Sito generato in %s: %d voci esportate, %d ad alta rilevanza", out_dir, len(findings), len(high))
    return out_dir
