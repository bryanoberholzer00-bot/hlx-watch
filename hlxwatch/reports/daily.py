"""Report giornaliero (o per periodo) in Markdown e HTML, raggruppato per gioco."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from hlxwatch.links import duplicates_label
from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.timeutil import local, local_day, local_day_bounds_utc, now_local, register_filters, tz_label

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
<p>Periodo: {{ data.start }} &rarr; {{ data.end }} ({{ data.tz }}). Generato il {{ data.generated }}.</p>
<div class="summary">
 <div><b>{{ data.totals.findings }}</b> voci</div>
 <div><b>{{ data.totals.new }}</b> stringhe mai viste</div>
 <div><b>{{ data.totals.high }}</b> ad alta rilevanza</div>
 <div><b>{{ data.totals.builds }}</b> build / commit</div>
</div>
{% if not data.games %}<p><em>Nessuna novit&agrave; nel periodo.</em></p>{% endif %}
{% for game in data.games %}
<h2>{{ game.title }} <small>({{ game.n_total }} voci, {{ game.n_rows }} raggruppate, {{ game.n_new }} nuove, punteggio max {{ game.max_score }})</small></h2>
{% if game.builds %}
<h3>Build e commit</h3>
<ul>{% for b in game.builds %}
 <li>{{ b.date|local }} &middot; <code>{{ b.ref[:12] }}</code>
 {% if b.url %}<a href="{{ b.url }}">{{ b.url|linklabel }}</a>{% endif %} &middot; {{ b.message or '' }}
 {% if b.steam_buildid %}<small>(buildid {{ b.steam_buildid }})</small>{% endif %}
 &middot; <small>{{ b.n_findings or 0 }} voci</small></li>
{% endfor %}</ul>{% endif %}
{% if game.findings %}
<h3>Novit&agrave; (per punteggio)</h3>
<table><tr><th>Punteggio</th><th>Tipo</th><th>Token / stringa</th><th>File</th><th>Keyword</th><th>Nuova</th></tr>
{% for f in game.findings %}
<tr class="{{ f.severity }}"><td>{{ f.score }}</td><td>{{ f.change_type }}</td>
<td><code>{{ f.token or f.text[:120] }}</code>{% if f.token and f.text != f.token %}<br><small>{{ f.text[:160] }}</small>{% endif %}</td>
<td>{{ f.file_path }}{% if f.line_no %}:{{ f.line_no }}{% endif %}{% if f.dup_count > 1 %} <small title="{{ f.dup_files|join('&#10;') }}">{{ f.dup_short }}</small>{% endif %}{% if f.build_url %} <a href="{{ f.build_url }}">{{ f.build_url|linklabel }}</a>{% endif %}</td>
<td>{{ f.keywords|join(', ') }}</td><td>{{ 'sì' if f.is_new else '' }}</td></tr>
{% endfor %}
{% if game.n_hidden %}<tr><td colspan="6"><em>&hellip; e altre {{ game.n_hidden }} righe a punteggio pi&ugrave; basso (vedi dashboard)</em></td></tr>{% endif %}
</table>{% endif %}
{% endfor %}
</body></html>
"""

_env = Environment(autoescape=select_autoescape(["html"]))
register_filters(_env)
_html_template = _env.from_string(HTML_TEMPLATE)


def period_for(day: date | None = None, days: int = 1) -> tuple[datetime, datetime]:
    """Intervallo [start, end), espresso in UTC, dei `days` giorni del calendario locale che finiscono con `day`
    (default oggi): con il fuso Europe/Rome un "giorno" va dalla mezzanotte italiana alla successiva."""
    day = day or now_local().date()
    start, _ = local_day_bounds_utc(day - timedelta(days=max(1, days) - 1))
    _, end = local_day_bounds_utc(day)
    return start, end


def _severity(score: int) -> str:
    return "high" if score >= 100 else "medium" if score >= 50 else "low"


MAX_ROWS_PER_GAME = 200


def _group(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stesso token, stesso commit, stesso tipo: una riga sola con il conteggio dei file."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for item in findings:
        key = ((item.get("token") or item.get("text") or "").lower(), item.get("build_id"), item["change_type"])
        if key in grouped:
            g = grouped[key]
            g["dup_count"] += 1
            g["dup_files"].append(item["file_path"])
            g["is_new"] = g["is_new"] or item["is_new"]
        else:
            item = dict(item, dup_count=1, dup_files=[])
            grouped[key] = item
            order.append(key)
    out = [grouped[k] for k in order]
    for g in out:
        g["dup_short"] = duplicates_label(g["file_path"], g["dup_files"], short=True)
    return out


def collect_report_data(db: Database, cfg: Config, start: datetime, end: datetime) -> dict[str, Any]:
    rows = db.search_findings(since=start, until=end, limit=200_000, order="score",
                              exclude_change_type=("baseline", "present"))
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
        g["n_total"] = len(g["findings"])
        g["n_new"] = sum(1 for f in g["findings"] if f["is_new"])
        g["max_score"] = max((f["score"] for f in g["findings"]), default=0)
        g["n_high"] = sum(1 for f in g["findings"] if f["score"] >= 100)
        g["n_med"] = sum(1 for f in g["findings"] if 50 <= f["score"] < 100)
        g["findings"] = _group(g["findings"])          # già ordinate per punteggio
        g["n_rows"] = len(g["findings"])
        g["n_hidden"] = max(0, g["n_rows"] - MAX_ROWS_PER_GAME)
        g["findings"] = g["findings"][:MAX_ROWS_PER_GAME]
        ordered.append(g)
    ordered.sort(key=lambda g: (g["max_score"], len(g["findings"])), reverse=True)

    first_day, last_day = local_day(start), local_day(end - timedelta(seconds=1))
    label = first_day if first_day == last_day else f"{first_day} / {last_day}"
    return {
        "label": label,
        "start": local(start),
        "end": local(end),
        "generated": now_local().strftime("%Y-%m-%d %H:%M"),
        "tz": tz_label(),
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
           f"Periodo: {data['start']} \u2192 {data['end']} ({data['tz']}). Generato il {data['generated']}.", "",
           f"**{data['totals']['findings']}** voci, **{data['totals']['new']}** stringhe mai viste, "
           f"**{data['totals']['high']}** ad alta rilevanza, **{data['totals']['builds']}** build/commit.", ""]
    if not data["games"]:
        out.append("_Nessuna novit\u00e0 nel periodo._")
    for game in data["games"]:
        out += [f"## {game['title']}", "",
                f"{game['n_total']} voci ({game['n_rows']} raggruppate per token e commit), {game['n_new']} nuove, "
                f"punteggio max {game['max_score']}", ""]
        if game["builds"]:
            out.append("### Build e commit")
            for b in game["builds"]:
                link = f" [link]({b['url']})" if b.get("url") else ""
                buildid = f" (buildid {b['steam_buildid']})" if b.get("steam_buildid") else ""
                out.append(f"- {local(b['date'])} `{b['ref'][:12]}`{link} \u2014 "
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
                if f["dup_count"] > 1:
                    where += f" {f['dup_short']}"
                label = _cell(f.get("token") or f["text"][:120])
                out.append(f"| {f['score']} | {f['change_type']} | `{label}` | {where} | "
                           f"{', '.join(f['keywords'])} | {'s\u00ec' if f['is_new'] else ''} |")
            if game["n_hidden"]:
                out.append(f"| | | _\u2026 e altre {game['n_hidden']} righe a punteggio pi\u00f9 basso (vedi dashboard)_ | | | |")
            out.append("")
    return "\n".join(out)


def render_html(data: dict[str, Any]) -> str:
    return _html_template.render(data=data)


def summary_text(data: dict[str, Any]) -> str:
    """Riepilogo breve per Telegram/Discord: per ogni gioco le voci più rilevanti, senza ripetizioni."""
    t = data["totals"]
    lines = [f"{t['findings']} voci, {t['new']} mai viste, {t['high']} ad alta rilevanza, {t['builds']} build/commit."]
    if not data["games"]:
        lines.append("Nessuna novità nel periodo.")
    for game in data["games"][:8]:
        lines.append(f"\n{game['title']}: {game['n_total']} voci, {game['n_new']} nuove, {game['n_high']} alte, "
                     f"{game['n_med']} medie"
                     + (f", {len(game['builds'])} build/commit" if game["builds"] else ""))
        seen: set[str] = set()
        shown = 0
        for f in game["findings"]:
            label = (f.get("token") or f["text"])[:60]
            key = label.lower()
            if key in seen or f["score"] < 50:
                continue
            seen.add(key)
            where = f["file_path"].rsplit("/", 1)[-1][:50]
            extra = f" {f['dup_short']}" if f["dup_count"] > 1 else ""
            snippet = f" — {f['text'][:70]}" if f.get("token") and f["text"].strip() != f["token"] else ""
            lines.append(f"  [{f['score']}] {f['change_type']} {label}{snippet} · {where}{extra}")
            shown += 1
            if shown >= 5:
                break
        if shown == 0:
            lines.append("  (solo voci a bassa rilevanza)")
    return "\n".join(lines)


def generate_report(db: Database, cfg: Config, *, day: date | None = None, days: int = 1, hours: int | None = None,
                    fmt: str = "both", out_dir: Path | None = None) -> tuple[list[Path], dict[str, Any]]:
    """Report per giorno/i interi (day, days) oppure per le ultime `hours` ore fino ad adesso."""
    if hours:
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = end - timedelta(hours=hours)
    else:
        start, end = period_for(day, days)
    data = collect_report_data(db, cfg, start, end)
    if hours:
        data["label"] = f"ultime {hours} ore"
    out_dir = out_dir or cfg.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"report-{local_day(end - timedelta(seconds=1))}" + (f"-{hours}h" if hours else f"-{days}d" if days > 1 else "")
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
