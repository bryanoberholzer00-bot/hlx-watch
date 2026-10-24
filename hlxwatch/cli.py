"""Interfaccia a riga di comando: python -m hlxwatch <comando>."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hlxwatch import __version__
from hlxwatch.config import PACKAGE_ROOT, Config, ConfigError, load_config
from hlxwatch.logging_setup import setup_logging
from hlxwatch.timeutil import local_day, now_local

log = logging.getLogger("hlxwatch")


def _open(args: argparse.Namespace) -> tuple[Config, "Database"]:
    from hlxwatch.storage import Database
    cfg = load_config(args.config)
    setup_logging(args.log_level or cfg.get("general.log_level", "INFO"), cfg.log_dir)
    return cfg, Database(cfg.db_path)


# ----------------------------------------------------------------- comandi
def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir or ".").resolve()
    target.mkdir(parents=True, exist_ok=True)
    created = []
    for src, dst in (("config.example.yaml", "config.yaml"), (".env.example", ".env")):
        dst_path = target / dst
        if dst_path.exists():
            print(f"  {dst}: già presente, non tocco")
            continue
        shutil.copyfile(PACKAGE_ROOT / src, dst_path)
        created.append(dst)
    (target / "data").mkdir(exist_ok=True)
    print(f"Inizializzato in {target}: creati {', '.join(created) or 'nessun file nuovo'}.")
    print("Prossimi passi: metti GITHUB_TOKEN (e webhook/bot) in .env, poi `python -m hlxwatch backfill --months 6`.")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.core import Pipeline
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    result = Pipeline(cfg, db).run_scan(sources=sources, notify=not args.no_notify, repos=args.repo or None,
                                        max_commits=args.max_commits)
    return 1 if result.all_failed else 0


def cmd_backfill(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.core import Pipeline
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=int(round(args.months * 30.44)))
    log.info("Backfill storico dei commit GameTracking dal %s (notifiche: %s)", since.date(),
             "sì" if args.notify else "no, solo archivio")
    result = Pipeline(cfg, db).run_scan(sources=("github",), notify=args.notify, github_since=since,
                                        repos=args.repo or None, max_commits=0,
                                        heading="HLX Watch: backfill storico")
    stats = db.stats()
    log.info("Archivio: %d build/commit, %d voci, %d stringhe distinte", stats["builds"], stats["findings"],
             stats["seen_strings"])
    return 1 if result.all_failed else 0


def cmd_export(args: argparse.Namespace) -> int:
    """Esporta l'archivio (database compresso + CSV) in una cartella, ad esempio per il ramo `data`."""
    cfg, db = _open(args)
    from hlxwatch.export import export_all
    info = export_all(cfg, db, Path(args.out), args.repo_url, args.site_url)
    print(f"Esportate {info['findings']} voci, database compresso {info['size_gz'] / 1e6:.1f} MB in {args.out}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Ripristina il database da un file .sqlite.gz (solo se manca o con --force)."""
    cfg = load_config(args.config)
    setup_logging(args.log_level or cfg.get("general.log_level", "INFO"), cfg.log_dir)
    from hlxwatch.export import restore_db
    if cfg.db_path.exists() and not args.force:
        print(f"Database già presente in {cfg.db_path}: niente da fare (usa --force per sovrascrivere)")
        return 0
    restore_db(Path(args.file), cfg.db_path)
    print(f"Database ripristinato in {cfg.db_path}")
    return 0


def cmd_fullscan(args: argparse.Namespace) -> int:
    """Scansione completa del contenuto attuale dei repository (anche se non è ancora dovuta)."""
    cfg, db = _open(args)
    from hlxwatch.core import Pipeline
    result = Pipeline(cfg, db).run_scan(sources=("fullscan",), notify=not args.no_notify, repos=args.repo or None,
                                        force_full=True, heading="HLX Watch: scansione completa")
    return 1 if result.all_failed else 0


def cmd_cron(args: argparse.Namespace) -> int:
    """Un giro del daemon senza restare in esecuzione: scansione + eventuale report giornaliero.
    Pensato per GitHub Actions o per l'Utilità di pianificazione di Windows."""
    cfg, db = _open(args)
    from hlxwatch.core import Pipeline
    from hlxwatch.scheduler import maybe_daily_report
    pipeline = Pipeline(cfg, db)
    result = pipeline.run_scan(notify=not args.no_notify)
    report_time = str(cfg.get("general.daily_report_time", "09:00"))
    try:
        if maybe_daily_report(cfg, db, pipeline, report_time):
            log.info("Report giornaliero inviato")
    except Exception:
        log.exception("Report giornaliero fallito")
    # analisi AI: una volta al giorno, nella stessa finestra del report, solo se ci sono novità
    if now_local().strftime("%H:%M") >= report_time:
        try:
            _run_analysis(cfg, db, pipeline.notifiers, force=False, notify=not args.no_notify)
            ai_error = None
        except Exception as exc:
            log.exception("Analisi AI fallita")
            ai_error = f"{type(exc).__name__}: {exc}"
        if not args.no_notify:
            # credito esaurito o chiave non valida: avviso subito, senza aspettare tre run
            urgent = bool(ai_error) and any(w in ai_error.lower() for w in ("credit", "authentication", "api key"))
            pipeline.health.record("ai", ai_error is None, ai_error or "", immediate=urgent)
    return 1 if result.all_failed else 0


def _run_analysis(cfg: Config, db: "Database", notifiers: list, *, force: bool, notify: bool,
                  days: int | None = None) -> dict | None:
    from hlxwatch.analysis import Analyzer, telegram_text
    from hlxwatch.notifiers import notify_text
    content = Analyzer(cfg, db).run(force=force, days=days)
    if content and notify and notifiers and content.get("novita_rilevanti"):
        notify_text(notifiers, f"HLX Watch AI: {content.get('titolo', 'analisi')}", telegram_text(content))
    return content


def cmd_analyze(args: argparse.Namespace) -> int:
    """Analisi AI delle scoperte con Claude Opus 5.5 (richiede ANTHROPIC_API_KEY)."""
    cfg, db = _open(args)
    from hlxwatch.notifiers import load_notifiers
    content = _run_analysis(cfg, db, load_notifiers(cfg), force=True, notify=args.send, days=args.days)
    if not content:
        print("Nessuna analisi prodotta: vedi log (chiave mancante, limite di spesa o rifiuto).")
        return 1
    print(f"{content.get('titolo')}\n\n{content.get('sintesi')}")
    for h in content.get("ipotesi", []):
        print(f"- [{h.get('fiducia')}] {h.get('titolo')}")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.core import Pipeline
    from hlxwatch.scheduler import run_daemon
    try:
        run_daemon(cfg, db, Pipeline(cfg, db))
    except KeyboardInterrupt:
        log.info("Daemon fermato")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.reports import generate_report, summary_text
    day = date.fromisoformat(args.date) if args.date else None
    paths, data = generate_report(db, cfg, day=day, days=args.days, hours=args.hours, fmt=args.format)
    print(summary_text(data))
    for p in paths:
        print(f"Report scritto: {p}")
    if args.send:
        from hlxwatch.notifiers import load_notifiers, notify_text
        notifiers = load_notifiers(cfg)
        ok = notify_text(notifiers, f"HLX Watch: report {data['label']}", summary_text(data))
        print("Riepilogo inviato" if ok else "Invio fallito o nessun notificatore configurato")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    import uvicorn
    from hlxwatch.dashboard import create_app
    host = args.host or cfg.get("dashboard.host", "127.0.0.1")
    port = args.port or int(cfg.get("dashboard.port", 8765))
    log.info("Dashboard su http://%s:%d", host, port)
    uvicorn.run(create_app(cfg, db), host=host, port=port, log_level="warning")
    return 0


def cmd_reevaluate(args: argparse.Namespace) -> int:
    """Riapplica le regole attuali a tutto l'archivio: utile dopo aver cambiato keyword o filtri."""
    cfg, db = _open(args)
    from hlxwatch.analyzers import KeywordMatcher
    ignore = {r["name"]: r.get("ignore_keywords", []) or [] for r in cfg.get("github.repos", []) or []}
    ignore.update({a["name"]: a.get("ignore_keywords", []) or [] for a in cfg.steam_watch_apps})
    matchers = {g: KeywordMatcher(cfg.keywords, ignore_ids=ids) for g, ids in ignore.items()}
    default = KeywordMatcher(cfg.keywords)
    rows = db.conn.execute(
        "SELECT id, game, text, file_path, change_type, score, keywords FROM findings "
        "WHERE change_type NOT IN ('build', 'new_app')").fetchall()
    drop: list[int] = []
    rescored = 0
    for r in rows:
        change_type = "added" if r["change_type"] in ("baseline", "present") else r["change_type"]
        res = matchers.get(r["game"], default).analyze_line(r["text"], file_path=r["file_path"], change_type=change_type)
        if res is None:
            drop.append(r["id"])
        elif res.score != r["score"]:
            db.conn.execute("UPDATE findings SET score = ?, keywords = ?, token = ? WHERE id = ?",
                            (res.score, json.dumps(res.keywords), res.token, r["id"]))
            rescored += 1
    db.conn.executemany("DELETE FROM findings WHERE id = ?", [(i,) for i in drop])
    # app private senza altri segnali, registrate come voci dalle versioni precedenti
    private = db.conn.execute(
        "SELECT id, build_id FROM findings WHERE change_type = 'new_app' AND keywords = '[\"private_app\"]'").fetchall()
    db.conn.executemany("DELETE FROM findings WHERE id = ?", [(r["id"],) for r in private])
    db.conn.executemany("DELETE FROM builds WHERE id = ? AND NOT EXISTS (SELECT 1 FROM findings WHERE build_id = ?)",
                        [(r["build_id"], r["build_id"]) for r in private if r["build_id"]])
    db.conn.commit()
    log.info("Rivalutazione: %d voci esaminate, %d eliminate, %d con punteggio aggiornato, %d app private rimosse",
             len(rows), len(drop), rescored, len(private))
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.reports.site import build_site
    out = Path(args.out) if args.out else cfg.data_dir / "site"
    build_site(db, cfg, out, timeline_days=args.days)
    print(f"Sito generato in {out}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    rows = db.search_findings(q=args.query, game=args.game, min_score=args.min_score, limit=args.limit, order="score")
    if not rows:
        print("Nessun risultato.")
        return 0
    for r in rows:
        print(f"[{r['score']:3d}] {local_day(r['created_at'])} {r['game']:<9} {r['change_type']:<12} "
              f"{(r['token'] or r['text'])[:60]:<60} {r['file_path'][:50]}")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.models import BuildInfo, Finding
    from hlxwatch.notifiers import load_notifiers, notify_findings
    notifiers = load_notifiers(cfg)
    if not notifiers:
        print("Nessun notificatore abilitato: controlla notifiers.* in config.yaml e i valori in .env")
        return 1
    build = BuildInfo(game="cs2", source="github", ref="0000000deadbeef", date=datetime.now(timezone.utc),
                      url="https://github.com/SteamTracking/GameTracking-CS2/commits/master", message="test")
    finding = Finding(game="cs2", source="github", file_path="game/csgo/pak01_dir.txt", change_type="added",
                      text="maps/hlx_test_notifica.vpk", score=130, keywords=["hlx_path"],
                      token="maps/hlx_test_notifica.vpk", line_no=42, is_new=True)
    finding.build = build
    ok = notify_findings(notifiers, [finding], cfg, "HLX Watch: notifica di prova")
    print("Notifica di prova inviata" if ok else "Invio fallito: vedi log")
    return 0 if ok else 1


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hlxwatch", description=f"HLX Watch {__version__}")
    parser.add_argument("--config", "-c", help="percorso di config.yaml")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING...")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="crea config.yaml, .env e la cartella data/")
    p.add_argument("--dir", help="cartella di destinazione (default: corrente)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan", help="una scansione completa di tutte le sorgenti")
    p.add_argument("--sources", default="github,steam,local,fullscan",
                   help="elenco separato da virgola: github,steam,local,fullscan (la scansione completa parte solo se è dovuta)")
    p.add_argument("--repo", action="append", help="limita a un repo per nome (cs2, dota2, deadlock); ripetibile")
    p.add_argument("--max-commits", type=int, help="commit massimi per repo (0 = tutti)")
    p.add_argument("--no-notify", action="store_true", help="non inviare notifiche")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("backfill", help="scansione storica iniziale dei commit GameTracking")
    p.add_argument("--months", type=float, default=6, help="mesi indietro (default 6)")
    p.add_argument("--since", help="data di inizio YYYY-MM-DD (alternativa a --months)")
    p.add_argument("--repo", action="append", help="limita a un repo per nome; ripetibile")
    p.add_argument("--notify", action="store_true", help="invia notifiche anche per il backfill (default no)")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("daemon", help="scansione periodica + report giornaliero")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("export", help="esporta database compresso e CSV delle voci in una cartella")
    p.add_argument("--out", default="export")
    p.add_argument("--repo-url", default="https://github.com/bryanoberholzer00-bot/hlx-watch")
    p.add_argument("--site-url", default="https://bryanoberholzer00-bot.github.io/hlx-watch/")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("restore", help="ripristina il database da un file .sqlite.gz")
    p.add_argument("file")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("analyze", help="analisi AI delle scoperte con Claude Opus 5.5 (ANTHROPIC_API_KEY)")
    p.add_argument("--days", type=int, help="analizza le novità degli ultimi N giorni (default: dall'ultima analisi)")
    p.add_argument("--send", action="store_true", help="invia il riepilogo ai notificatori se ci sono novità")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("fullscan", help="cerca le parole chiave in tutto il contenuto attuale dei repository")
    p.add_argument("--repo", action="append", help="limita a un repo per nome; ripetibile")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_fullscan)

    p = sub.add_parser("cron", help="un giro solo: scansione + report giornaliero se è ora (per Actions o Task Scheduler)")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_cron)

    p = sub.add_parser("report", help="genera il report Markdown/HTML")
    p.add_argument("--date", help="giorno YYYY-MM-DD (default oggi)")
    p.add_argument("--days", type=int, default=1, help="giorni coperti, a ritroso dalla data (default 1)")
    p.add_argument("--hours", type=int, help="in alternativa: le ultime N ore fino ad adesso")
    p.add_argument("--format", choices=["md", "html", "both"], default="both")
    p.add_argument("--send", action="store_true", help="invia il riepilogo ai notificatori")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("serve", help="avvia la dashboard web locale")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("reevaluate", help="riapplica le regole attuali a tutto l'archivio (dopo modifiche al config)")
    p.set_defaults(func=cmd_reevaluate)

    p = sub.add_parser("site", help="genera la dashboard statica (HTML + JSON) per GitHub Pages")
    p.add_argument("--out", help="cartella di destinazione (default data/site)")
    p.add_argument("--days", type=int, default=180, help="giorni della timeline (default 180)")
    p.set_defaults(func=cmd_site)

    p = sub.add_parser("search", help="cerca nello storico da terminale")
    p.add_argument("query")
    p.add_argument("--game")
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("test-notify", help="invia una notifica di prova a Discord/Telegram")
    p.set_defaults(func=cmd_test_notify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrotto.", file=sys.stderr)
        return 130
