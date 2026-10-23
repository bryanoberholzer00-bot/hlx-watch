"""Report giornaliero (o per periodo) in Markdown e HTML, raggruppato per gioco."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from hlxwatch.config import Config
from hlxwatch.storage import Database

HTML_TEMPLATE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<title>HLX Watch - report {{ data.label }}</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222;background:#fafafa}
 h1{border-bottom:3px solid #e8743b} h2{margin-top:2.2rem;border-bottom:1px solid #ddd}
 table{border-collapse:collapse;width:100%;font-size:.92rem} th,td{border:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}
 th{background:#f0f0f0} code{background:#eee;padding:0 .25rem;border-radius:3px;word-break:break-all}
 .high{background:#ffe3e0} .medium{background:#fff2dc} .low{background:#f4f4f4}
 .summary{display:flex;gap:1.5rem;flex-wrap:wrap} .summary div{background:#fff;border:1px solid #ddd;padding:.6rem 1rem;border-radius:6px}
 .summary b{font-size:1.4rem;display:block} pre{white-space:pre-wrap;font-size:.8rem;margin:0}
 small{color:#666}
</style></head><body>
<h1>HLX Watch &mdash; report {{ data.label }}</h1>
<p>Periodo: {{ data.start }} &rarr; {{ data.end }} (UTC). Generato il {{ data.generated }}.</p>
<div class="summary">
 <div><b>{{ data.totals.findings }}</b> voci</div>
 <div><b>{{ data.totals.new }}</b> stringhe mai viste</div>
 <div><b>{{ data.totals.high }}</b> ad alta rilevanza</div>
 <div><b>{{ data.totals.builds }}</b> build / commit</div>
</div>
{% if not data.games %}<p><em>Nessuna novit&agrave; nel periodo.</em></p>{% endif %}
{% for game in data.games %}
<h2>{{ game.title }} <small>({{ game.findings|length }} voci, {{ game.n_new }} nuove, punteggio max {{ game.max_score }})</small></h2>
{% if game.builds %}
<h3>Build e commit</h3>
<ul>{% for b in game.builds %}
 <li>{{ b.date[:16].replace('T',' ') }} &middot; <code>{{ b.ref[:12] }}</code>
 {% if b.url %}<a href="{{ b.url }}">link</a>{% endif %} &middot; {{ b.message or '' }}
 {% if b.steam_buildid %}<small>(buildid {{ b.steam_buildid }})</small>{% endif %}
 &middot; <small>{{ b.n_findings or 0 }} voci</small></li>
{% endfor %}</ul>{% endif %}
{% if game.findings %}
<h3>Novit&agrave; (per punteggio)</h3>
<table><tr><th>Punteggio</th><th>Tipo</th><th>Token / stringa</th><th>File</th><th>Keyword</th><th>Nuova</th></tr>
{% for f in game.findings %}
<tr class="{{ f.severity }}"><td>{{ f.score }}</td><td>{{ f.change_type }}</td>
<td><code>{{ f.token or f.text[:120] }}</code>{% if f.token and f.text != f.token %}<br><small>{{ f.text[:160] }}</small>{% endif %}</td>
<td>{{ f.file_path }}{% if f.line_no %}:{{ f.line_no }}{% endif %}{% if f.build_url %} <a href="{{ f.build_url }}">commit</a>{% endif %}</td>
<td>{{ f.keywords|join(', ') }}</td><td>{{ 'sì' if f.is_new else '' }}</td></tr>
{% endfor %}</table>{% endif %}
{% endfor %}
</body></html>
"""

_env = Environment(autoescape=select_autoescape(["html"]))
_html_template = _env.from_string(HTML_TEMPLATE)


def period_for(day: date | None = None, days: int = 1) -> tuple[datetime, datetime]:
    """Intervallo [start, end) in UTC che termina alla fine del giorno indicato (default oggi)."""
    day = day or datetime.now(timezone.utc).date()
    end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return end - timedelta(days=max(1, days)), end


def _severity(score: int) -> str:
    return "high" if score >= 100 else "medium" if score >= 50 else "low"


def collect_report_data(db: Database, cfg: Config, start: datetime, end: datetime) -> dict[str, Any]:
    rows = db.search_findings(since=start, until=end, limit=200_000, order="score")
    findings: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["keywords"] = json.loads(item.get("keywords") or "[]")
        except json.JSONDecodeError:
            item["keywords"] = []
        item["severity"] = _severity(item["score"])
        findings.append(item)
    builds = [dict(r) for r in db.builds_between(start, end)]

    games: dict[str, dict[str, Any]] = {}
    for item in findings:
        g = games.setdefault(item["game"], {"name": item["game"], "findings": [], "builds": []})
        g["findings"].append(item)
    for build in builds:
        g = games.setdefault(build["game"], {"name": build["game"], "findings": [], "builds": []})
        g["builds"].append(build)
    ordered: list[dict[str, Any]] = []
    for name, g in games.items():
        g["title"] = "Steam (nuove app)" if name == "steam" else cfg.game_title(name)
        g["n_new"] = sum(1 for f in g["findings"] if f["is_new"])
        g["max_score"] = max((f["score"] for f in g["findings"]), default=0)
        ordered.append(g)
    ordered.sort(key=lambda g: (g["max_score"], len(g["findings"])), reverse=True)

    label = start.strftime("%Y-%m-%d") if (end - start) <= timedelta(days=1) \
        else f"{start.strftime('%Y-%m-%d')} / {(end - timedelta(seconds=1)).strftime('%Y-%m-%d')}"
    return {
        "label": label,
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%Y-%m-%d %H:%M"),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "games": ordered,
        "totals": {
            "findings": len(findings),
            "new": sum(1 for f in findings if f["is_new"]),
            "high": sum(1 for f in findings if f["score"] >= 100),
            "builds": len(builds),
        },
    }


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict[str, Any]) -> str:
    out = [f"# HLX Watch \u2014 report {data['label']}", "",
           f"Periodo: {data['start']} \u2192 {data['end']} (UTC). Generato il {data['generated']}.", "",
           f"**{data['totals']['findings']}** voci, **{data['totals']['new']}** stringhe mai viste, "
           f"**{data['totals']['high']}** ad alta rilevanza, **{data['totals']['builds']}** build/commit.", ""]
    if not data["games"]:
        out.append("_Nessuna novit\u00e0 nel periodo._")
    for game in data["games"]:
        out += [f"## {game['title']}", "",
                f"{len(game['findings'])} voci, {game['n_new']} nuove, punteggio max {game['max_score']}", ""]
        if game["builds"]:
            out.append("### Build e commit")
            for b in game["builds"]:
                link = f" [link]({b['url']})" if b.get("url") else ""
                buildid = f" (buildid {b['steam_buildid']})" if b.get("steam_buildid") else ""
                out.append(f"- {b['date'][:16].replace('T', ' ')} `{b['ref'][:12]}`{link} \u2014 "
                           f"{_cell(b.get('message'))[:120]}{buildid} \u2014 {b.get('n_findings') or 0} voci")
            out.append("")
        if game["findings"]:
            out += ["### Novit\u00e0 (per punteggio)", "",
                    "| Punteggio | Tipo | Token / stringa | File | Keyword | Nuova |",
                    "|---:|---|---|---|---|:---:|"]
            for f in game["findings"]:
                where = f["file_path"] + (f":{f['line_no']}" if f.get("line_no") else "")
                if f.get("build_url"):
                    where = f"[{_cell(where)}]({f['build_url']})"
                label = _cell(f.get("token") or f["text"][:120])
                out.append(f"| {f['score']} | {f['change_type']} | `{label}` | {where} | "
                           f"{', '.join(f['keywords'])} | {'s\u00ec' if f['is_new'] else ''} |")
            out.append("")
    return "\n".join(out)


def render_html(data: dict[str, Any]) -> str:
    return _html_template.render(data=data)


def summary_text(data: dict[str, Any]) -> str:
    t = data["totals"]
    lines = [f"{t['findings']} voci, {t['new']} mai viste, {t['high']} ad alta rilevanza, {t['builds']} build/commit."]
    for game in data["games"][:8]:
        top = game["findings"][:3]
        top_txt = "; ".join(f"[{f['score']}] {(f.get('token') or f['text'])[:50]}" for f in top)
        lines.append(f"- {game['title']}: {len(game['findings'])} voci (max {game['max_score']}) {top_txt}")
    return "\n".join(lines)


def generate_report(db: Database, cfg: Config, *, day: date | None = None, days: int = 1,
                    fmt: str = "both", out_dir: Path | None = None) -> tuple[list[Path], dict[str, Any]]:
    start, end = period_for(day, days)
    data = collect_report_data(db, cfg, start, end)
    out_dir = out_dir or cfg.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"report-{(end - timedelta(seconds=1)).strftime('%Y-%m-%d')}" + (f"-{days}d" if days > 1 else "")
    paths: list[Path] = []
    if fmt in ("md", "both"):
        p = out_dir / f"{stem}.md"
        p.write_text(render_markdown(data), encoding="utf-8")
        paths.append(p)
    if fmt in ("html", "both"):
        p = out_dir / f"{stem}.html"
        p.write_text(render_html(data), encoding="utf-8")
        paths.append(p)
    return paths, data
