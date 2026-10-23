"""Interfaccia a riga di comando: python -m hlxwatch <comando>."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hlxwatch import __version__
from hlxwatch.config import PACKAGE_ROOT, Config, ConfigError, load_config
from hlxwatch.logging_setup import setup_logging

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
    return 1 if result.errors and not result.findings else 0


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
    return 1 if result.errors and not result.findings else 0


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
    paths, data = generate_report(db, cfg, day=day, days=args.days, fmt=args.format)
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


def cmd_search(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    rows = db.search_findings(q=args.query, game=args.game, min_score=args.min_score, limit=args.limit, order="score")
    if not rows:
        print("Nessun risultato.")
        return 0
    for r in rows:
        print(f"[{r['score']:3d}] {r['created_at'][:10]} {r['game']:<9} {r['change_type']:<12} "
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
                      url="https://github.com/SteamDatabase/GameTracking-CS2/commits/master", message="test")
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
    p.add_argument("--sources", default="github,steam,local", help="elenco separato da virgola: github,steam,local")
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

    p = sub.add_parser("report", help="genera il report Markdown/HTML")
    p.add_argument("--date", help="giorno YYYY-MM-DD (default oggi)")
    p.add_argument("--days", type=int, default=1, help="giorni coperti, a ritroso dalla data (default 1)")
    p.add_argument("--format", choices=["md", "html", "both"], default="both")
    p.add_argument("--send", action="store_true", help="invia il riepilogo ai notificatori")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("serve", help="avvia la dashboard web locale")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

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
