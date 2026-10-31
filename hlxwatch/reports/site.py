"""Sito statico della dashboard: generato a ogni scansione e pubblicato su GitHub Pages.

Non serve un server: la ricerca nello storico avviene nel browser su findings.json.
"""
from __future__ import annotations

import importlib.util
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
from hlxwatch.storage.db import iso, parse_iso, utcnow
from hlxwatch.sources.steam_news import SteamNewsSource
from hlxwatch.outcomes import engine_merge_index, merge_partners, outcome_register
from hlxwatch.analyzers.codenames import CodenameTracker
from hlxwatch.links import link_label
from hlxwatch.reports.site_extras import (AREA_LABELS, AREAS, area_of, evidence_of, hardware_data, schema_url,
                                          fast_status, source_status)
from hlxwatch.timeutil import local_day, now_local, register_filters, tz_label

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "site_templates"


def first_seen_days(db: Database) -> dict[tuple[str, str, str], tuple[str, bool]]:
    """(gioco, file, hash della riga) -> (giorno della prima comparsa, 'almeno dal'), letti una volta sola."""
    rows = db.conn.execute("SELECT game, file_path, line_hash, commit_date, at_least FROM first_seen "
                           "WHERE commit_date IS NOT NULL").fetchall()
    return {(r[0], r[1], r[2]): (local_day(r[3]), bool(r[4])) for r in rows}


def apply_first_seen(item: dict[str, Any], days: dict[tuple[str, str, str], tuple[str, bool]]) -> dict[str, Any]:
    """Le voci della scansione completa portano la data della scansione, non quella di Valve: se la prima
    comparsa è nota la usano come data della modifica (le classi CHLX di Hammer: 23 settembre, non 29 ottobre)."""
    if item.get("source") == "fullscan" and item.get("change_type") in ("added", "present"):
        from hlxwatch.sources.first_seen import line_hash
        hit = days.get((item["game"], item["file_path"], line_hash(item["text"] or "")))
        if hit:
            item["day"], item["day_at_least"] = hit
    return item


def with_summaries(db: Database, builds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggiunge a ogni commit il riassunto automatico di SteamTracking, se c'è (titolo e link)."""
    for b in builds:
        raw = db.get_state(f"summary:{b['game']}:{b['ref']}") if b.get("source") == "github" else None
        b["summary"] = json.loads(raw) if raw else None
    return builds


def decorate(item: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Area SteamTracking del cambiamento, tipo di prova, link allo Schema Explorer e, se la stessa riga è comparsa
    anche negli altri giochi entro pochi giorni, i giochi della fusione del motore."""
    item["area"] = area_of(item.get("file_path") or "", item.get("change_type") or "")
    item["area_label"] = AREA_LABELS[item["area"]]
    item["evidence"], item["evidence_label"], _ = evidence_of(item["area"])
    item["schema_url"] = schema_url(item.get("game") or "", item.get("file_path") or "")
    item["merge"] = (merge_partners(ctx["merge"], item.get("game") or "", item.get("token"), item.get("text"),
                                    item.get("day"), ctx.get("titles")) if ctx else [])
    return item


def bundle_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per la pagina iniziale: le voci dello stesso commit, file e tipo diventano una riga sola
    (es. CHLXCoverPreviewHelper e GetAttributeTable_CHLXCoverPreviewHelper nello stesso hammer_strings.txt).
    La prima voce resta in vista, le altre finiscono in `related`."""
    out: list[dict[str, Any]] = []
    index: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if row.get("build_id") is None:
            out.append(dict(row, related=[]))
            continue
        key = (row["build_id"], row.get("file_path"), row.get("change_type"))
        if key in index:
            head = index[key]
            head["related"].append(row.get("token") or row.get("text") or "")
            head["is_new"] = head.get("is_new") or row.get("is_new")
            if row["score"] > head["score"]:
                head["score"], head["severity"] = row["score"], row["severity"]
        else:
            head = index[key] = dict(row, related=[])
            out.append(head)
    return out


def glance(termo: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """Riquadro in cima alla pagina iniziale: termometro, ultima traccia forte e salute delle fonti in una riga."""
    problems: list[str] = []
    if status.get("problems"):
        problems.append(f"{len(status['problems'])} fonti con errori")
    stale = [r["title"] for r in status.get("repos", []) if r.get("stale")]
    if stale:
        problems.append("ferme: " + ", ".join(stale))
    fast = status.get("fast") or {}
    if fast.get("irraggiungibile"):
        problems.append("il bot non risponde")
    elif fast.get("fermo"):
        problems.append("controllo veloce fermo")
    restore = status.get("restore") or {}
    if restore and not restore.get("ok", True):
        problems.append("prova di ripristino non riuscita")
    hours = None
    if status.get("last_scan"):
        last = parse_iso(status["last_scan"])
        if last is not None:
            hours = (utcnow() - last).total_seconds() / 3600
    if hours is None or hours > 12:
        problems.append("nessuna scansione da più di 12 ore" if hours is not None else "nessuna scansione")
    return {"termo": termo, "problems": problems, "ok": not problems}


def write_feed(db: Database, out_dir: Path, site_url: str, ctx: dict[str, Any], limit: int = 40) -> int:
    """Feed Atom delle voci rilevanti (da 100 punti), per seguire HLX Watch con un lettore di feed."""
    from xml.sax.saxutils import escape
    rows = group_rows([decorate(_row(r), ctx) for r in db.search_findings(
        min_score=100, limit=200, order="created_at", exclude_change_type=("baseline", "present"))])[:limit]
    entries = []
    for f in rows:
        title = f"[{f['score']}] {ctx['titles'].get(f['game'], f['game'])}: {(f.get('token') or f['text'])[:90]}"
        link = f.get("build_url") or f"{site_url}search.html"
        summary = f"{f['text'][:300]} | prova: {f['evidence_label']}" + (
            f" | fusione del motore, anche in {', '.join(f['merge'])}" if f["merge"] else "")
        updated = (f.get("created_at") or iso(utcnow())).replace("+00:00", "Z")
        entries.append(f"""  <entry>
    <id>tag:hlx-watch,2026:finding-{f['id']}</id>
    <title>{escape(title)}</title>
    <updated>{escape(updated)}</updated>
    <link href="{escape(link, {'"': '&quot;'})}"/>
    <summary>{escape(summary)}</summary>
  </entry>""")
    feed = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>HLX Watch</title>
  <subtitle>Riferimenti a HLX nei dati pubblici dei giochi Valve su Source 2 (non affiliato con Valve)</subtitle>
  <id>{escape(site_url)}</id>
  <link href="{escape(site_url)}"/>
  <link rel="self" href="{escape(site_url)}feed.xml"/>
  <updated>{iso(utcnow()).replace("+00:00", "Z")}</updated>
{chr(10).join(entries)}
</feed>
"""
    (out_dir / "feed.xml").write_text(feed, encoding="utf-8")
    return len(entries)


def export_findings(db: Database, limit: int = 25000, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = db.search_findings(limit=limit, order="created_at", exclude_change_type="baseline")
    days = first_seen_days(db)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = decorate(apply_first_seen(_row(row), days), ctx)
        out.append({
            "id": item["id"], "score": item["score"], "game": item["game"], "source": item["source"],
            "type": item["change_type"], "token": item.get("token"), "text": (item["text"] or "")[:200],
            "file": item["file_path"], "line": item.get("line_no"), "day": item["day"],
            "seen": local_day(item.get("created_at")), "new": bool(item["is_new"]), "al": bool(item.get("day_at_least")),
            "url": item.get("build_url"), "ul": link_label(item.get("build_url")), "ref": (item.get("build_ref") or "")[:10], "kw": item["keywords"],
            "area": item["area"], "ev": item["evidence_label"], **({"sx": item["schema_url"]} if item["schema_url"] else {}),
            **({"fm": item["merge"]} if item["merge"] else {}),
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
    from hlxwatch.announcement import extra_watch
    for repo in tracked_repos(cfg, db):                  # repository seguiti da soli: il loro titolo, non la chiave
        titles.setdefault(repo["name"], repo.get("title", repo["name"]))
        if titles[repo["name"]] == repo["name"]:
            titles[repo["name"]] = repo.get("title", repo["name"])
    for app in extra_watch(db):
        titles[app["name"]] = app["title"]
    site_url = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    site_url = site_url if site_url.endswith("/") else site_url + "/"
    stamp = now_local()
    env.globals.update(version=__version__, titles=titles,
                       generated=f"{stamp.strftime('%Y-%m-%d %H:%M')} ({tz_label()})",
                       generated_en=f"{stamp.strftime('%Y-%m-%d %H:%M')} (Italian time)",
                       # ?v=: Telegram e Discord tengono in memoria le anteprime, così vedono quella nuova
                       og_image=(f"{site_url}anteprima.png?v={stamp.strftime('%Y%m%d%H')}"
                                 if importlib.util.find_spec("PIL") else ""),
                       repo_url=str(cfg.get("general.repo_url", "https://github.com/bryanoberholzer00-bot/hlx-watch")))
    register_filters(env)
    push = cfg.get("notifiers.webpush", {}) or {}
    env.globals["push_url"] = str(push.get("worker_url", "")).rstrip("/") if push.get("enabled") else ""

    stats = db.stats()
    analyses = [load_analysis(r) for r in db.analyses(60)]
    days = first_seen_days(db)
    ctx = {"merge": engine_merge_index(db), "titles": titles}
    high = group_rows([decorate(apply_first_seen(_row(r), days), ctx) for r in db.search_findings(min_score=100, limit=300, order="created_at",
                                                             exclude_change_type=("baseline", "present"))])
    recent = group_rows([decorate(apply_first_seen(_row(r), days), ctx) for r in db.search_findings(min_score=50, limit=80, order="created_at",
                                                               exclude_change_type=("baseline", "present"))])
    from hlxwatch.reports.termometro import compute as termo_now, history, history_svg
    termo_current = termo_now(cfg, db)
    status = source_status(cfg, db, fast_status(cfg))
    index_html = env.get_template("index.html").render(
        stats=stats, high=bundle_rows(high), recent=bundle_rows(recent)[:40], glance=glance(termo_current, status),
        chart=timeline_svg(db.timeline(days=timeline_days), timeline_days, link="search.html?since={day}&until={day}"),
        timeline_days=timeline_days, tokens=[dict(r) for r in db.top_tokens(25)],
        builds=with_summaries(db, [dict(r) for r in db.recent_builds(15)]), apps=[dict(r) for r in db.flagged_apps(20)],
        steam_builds=[dict(r) for r in db.steam_builds(12)], present=present_groups(db),
        analysis=analyses[0] if analyses else None, status=status,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    (out_dir / "hardware.html").write_text(env.get_template("hardware.html").render(hw=hardware_data(cfg, db)),
                                           encoding="utf-8")
    outcomes = outcome_register(cfg, db)
    (out_dir / "esiti.html").write_text(env.get_template("esiti.html").render(reg=outcomes),
                                        encoding="utf-8")
    # termometro HLX: valore di adesso e storico giorno per giorno (bot/termometro_storico.json, dati aperti)
    termo = history(cfg, db)
    (out_dir / "bot").mkdir(parents=True, exist_ok=True)
    (out_dir / "bot" / "termometro_storico.json").write_text(json.dumps(termo, ensure_ascii=False, separators=(",", ":")),
                                                             encoding="utf-8")
    (out_dir / "termometro.html").write_text(env.get_template("termometro.html").render(
        h=termo, svg=history_svg(termo), now=termo_current), encoding="utf-8")
    # risposte veloci per chi arriva da un motore di ricerca, in italiano e in inglese, più l'anteprima dei link
    from hlxwatch.reports.brief import brief_data, preview_image
    brief = brief_data(cfg, termo_current, termo, outcomes)
    for name in ("hlx.html", "en.html"):
        (out_dir / name).write_text(env.get_template(name).render(b=brief), encoding="utf-8")
    if not preview_image(brief, out_dir / "anteprima.png", site_url.split("//", 1)[-1].rstrip("/")):
        env.globals["og_image"] = ""
    write_feed(db, out_dir, site_url, ctx)
    kw = cfg.keywords
    (out_dir / "metodologia.html").write_text(env.get_template("metodologia.html").render(
        repos=tracked_repos(cfg, db), steam_apps=[a.get("title", a.get("name")) for a in cfg.steam_watch_apps],
        news_apps=[a["title"] for a in SteamNewsSource(cfg, db).apps()],
        full_scan={**{"interval_hours": 24, "min_score": 50, "prefilter": ""}, **(cfg.get("full_scan", {}) or {})},
        keywords=kw.get("items", []), codenames=kw.get("codenames", []) or [], codename_weight=kw.get("codename_weight", 30),
        bonuses=kw.get("bonuses", {}) or {}, severity={"high": 100, "medium": 50, **(kw.get("severity") or {})},
        global_excludes=kw.get("global_excludes", []) or [], notify_min_score=cfg.get("general.notify_min_score", 50),
        project_names=sorted(CodenameTracker(cfg, db).known), codename_score=int((cfg.get("codenames", {}) or {}).get("score", 120)),
        watched_files=cfg.get("watched_files", []) or [],
        structure_score=int((cfg.get("structure", {}) or {}).get("score", 100)),
        loc={"min_tokens": 20, "score_new_file": 100, "score_new_prefix": 60, **(cfg.get("localization", {}) or {})},
        ai_model="Claude Opus 5.5", repo_url=str(cfg.get("general.repo_url", "https://github.com/bryanoberholzer00-bot/hlx-watch")),
    ), encoding="utf-8")
    from hlxwatch.timeline import build_entries
    (out_dir / "cronologia.html").write_text(env.get_template("cronologia.html").render(
        data=build_entries(cfg, db), ai_model="Claude Opus 5.5"), encoding="utf-8")
    (out_dir / "analisi.html").write_text(env.get_template("analisi.html").render(analyses=analyses),
                                           encoding="utf-8")
    (out_dir / "search.html").write_text(env.get_template("search.html").render(games=stats["games"], areas=AREAS),
                                         encoding="utf-8")

    findings = export_findings(db, ctx=ctx)
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
