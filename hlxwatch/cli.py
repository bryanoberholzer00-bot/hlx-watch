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


def cmd_bluesky_test(args: argparse.Namespace) -> int:
    """Prova a vuoto di Bluesky: i post che si farebbero con gli avvisi da 100 punti degli ultimi giorni e con la
    serata in corso. Non pubblica e non scrive niente nell'archivio."""
    cfg, db = _open(args)
    from datetime import timedelta
    from hlxwatch import bluesky
    from hlxwatch.revisione import _row_finding
    from hlxwatch.storage.db import iso, utcnow
    s = bluesky.settings(cfg)
    since = iso(utcnow() - timedelta(days=args.giorni))
    rows = db.conn.execute(
        "SELECT f.*, b.date AS build_date FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        "WHERE f.notified = 1 AND f.score >= ? AND f.created_at >= ? ORDER BY f.id", (s["min_score"], since)).fetchall()
    alerts = []
    for r in rows:
        f = _row_finding(r)
        f.build_id = r["build_id"]            # un post per build, come nel giro vero
        alerts.append(f)
    try:                                       # il link alla scheda della traccia, come nel giro vero
        from hlxwatch.reports.schede import annotate_cards
        annotate_cards(cfg, db, alerts)
    except Exception:
        log.exception("Bluesky, prova: schede delle tracce non collegate")
    cfg.raw.setdefault("bluesky", {})["max_age_days"] = args.giorni + 1      # nella prova contano anche i giorni passati
    now, bot_alerts = None, None
    if args.simula_serata:
        # la serata dei Game Awards in corso, con tre annunci finti del bot (pagina, prodotto, video): solo per vedere
        # i post, niente viene pubblicato né scritto
        from datetime import datetime
        from hlxwatch import serata
        real = [x for x in serata.load() if not x.get("prova")]
        if real:
            start = datetime.fromisoformat(real[-1]["inizio"].replace("Z", "+00:00"))
            now = start + timedelta(hours=8)
            c = (start + timedelta(hours=7, minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
            bot_alerts = [
                {"t": "web", "g": "half-life.com", "x": "POSSIBILE ANNUNCIO: pagina nuova su half-life.com: /en/hlx",
                 "u": "https://www.half-life.com/en/hlx", "c": c},
                {"t": "store", "g": "Valve su Steam", "x": "POSSIBILE ANNUNCIO: nuovo prodotto di Valve su Steam: Half-Life X",
                 "u": "https://store.steampowered.com/app/9999990/", "c": c},
                {"t": "video", "g": "YouTube: The Game Awards", "x": "POSSIBILE ANNUNCIO: video nuovo: Half-Life X - Reveal Trailer",
                 "u": "https://www.youtube.com/watch?v=esempio", "c": c}]
            alerts = []                        # nella serata simulata contano solo apertura e annunci del bot
            print(f"Serata simulata: {real[-1].get('nome')}, ore {now.isoformat()} (annunci finti del bot).")
    posts = bluesky.plan(cfg, db, alerts, now, bot_alerts)
    print(f"Bluesky, prova a vuoto: {len(alerts)} avvisi da {s['min_score']} punti negli ultimi {args.giorni} giorni, "
          f"{len(posts)} post (pubblicazione {'accesa' if s['enabled'] else 'spenta'}"
          f"{', prova a vuoto nel config' if s['dry_run'] else ''}; segreti {'presenti' if cfg.env('BLUESKY_HANDLE') and cfg.env('BLUESKY_APP_PASSWORD') else 'assenti'}).")
    for p in posts:
        print(f"\n--- {p['chiave']} ({len(p['testo'])} caratteri)\n{p['testo']}")
    return 0


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
    # cronologia HLX: classifica con l'AI solo i gruppi di righe mai visti (di solito nessuno, costo zero)
    try:
        from hlxwatch.timeline import update_timeline
        update_timeline(cfg, db)
    except Exception:
        log.exception("Cronologia HLX: aggiornamento fallito")
    # riepilogo settimanale su Telegram: il lunedì (o al primo giro dopo), la settimana appena conclusa
    try:
        from hlxwatch.weekly import maybe_weekly
        maybe_weekly(cfg, db, pipeline.notifiers if not args.no_notify else [], report_time, send=not args.no_notify)
    except Exception:
        log.exception("Riepilogo settimanale fallito")
    # il bot su Cloudflare avvia i giri e li sorveglia: qui si controlla che sia vivo (hlxwatch/stato.py)
    fast = None
    try:
        from hlxwatch.reports.site_extras import fast_status
        from hlxwatch.stato import watch_bot
        fast = fast_status(cfg)
        watch_bot(db, pipeline.notifiers if not args.no_notify else [], fast)
    except Exception:
        log.exception("Controllo del bot su Cloudflare non riuscito")
    # mappa dei collegamenti: avviso quando un pezzo di HLX compare in un posto nuovo o due gruppi si uniscono
    try:
        from hlxwatch.mappa import watch as watch_map
        watch_map(cfg, db, pipeline.notifiers if not args.no_notify else [])
    except Exception:
        log.exception("Mappa dei collegamenti: controllo non riuscito")
    # una volta sola: lo storico dei file dei personaggi dal 2024 (hlxwatch/sources/storia_file.py), senza avvisi
    try:
        from hlxwatch.sources import storia_file
        storia_file.run(cfg, db)
    except Exception:
        log.exception("Storia dei file dei personaggi: recupero non riuscito")
    # le immagini degli strumenti di Valve: icone nuove con il giorno del disegno (hlxwatch/sources/immagini.py)
    try:
        from hlxwatch.sources import immagini
        immagini.scan(cfg, db, notifiers=pipeline.notifiers if not args.no_notify else [])
    except Exception:
        log.exception("Immagini degli strumenti: controllo non riuscito")
    # la pagina di Valve su Steam: prodotti contati e mostrati, tutti e in arrivo (hlxwatch/sources/steam_valve.py)
    try:
        from hlxwatch.sources import steam_valve
        steam_valve.check(cfg, db, pipeline.notifiers if not args.no_notify else [])
    except Exception:
        log.exception("Pagina di Valve su Steam: controllo non riuscito")
    # il registro delle previsioni: avviso quando una scadenza passa o una traccia HLX corrisponde (hlxwatch/previsioni.py)
    try:
        from hlxwatch import previsioni
        previsioni.watch(cfg, db, pipeline.notifiers if not args.no_notify else [])
    except Exception:
        log.exception("Registro delle previsioni: controllo non riuscito")
    # il silenzio: avviso se HLX supera la sua pausa più lunga, e in modalità annuncio quando ricompaiono nomi nuovi
    try:
        from hlxwatch.silenzio import watch as watch_silence
        watch_silence(cfg, db, pipeline.notifiers if not args.no_notify else [],
                      fast if fast and not fast.get("irraggiungibile") else None)
    except Exception:
        log.exception("Silenzio: controllo non riuscito")
    # revisione settimanale: dopo il riepilogo, l'AI propone correzioni agli errori della settimana
    try:
        from hlxwatch.revisione import run_review
        run_review(cfg, db, pipeline.notifiers if not args.no_notify else [], send_it=not args.no_notify)
    except Exception:
        log.exception("Revisione settimanale fallita")
    # falsi allarmi: i tipi di voce che mandano molti avvisi senza tracce HLX, proposti subito da silenziare
    try:
        from hlxwatch.revisione import check_noise
        check_noise(cfg, db, pipeline.notifiers if not args.no_notify else [], send_it=not args.no_notify)
    except Exception:
        log.exception("Controllo dei falsi allarmi fallito")
    # archivio delle righe: alcuni commit già visti a ogni giro, all'indietro fino a archivio_righe.dal
    try:
        from hlxwatch.archivio_righe import fill_step
        filled = fill_step(cfg, db, pipeline.github.client)
        if filled:
            log.info("Archivio delle righe: %d commit vecchi archiviati", filled)
    except Exception:
        log.exception("Archivio delle righe: riempimento non riuscito")
    # seconda rete: Claude guarda le righe nuove del motore che le parole chiave non vedono
    try:
        from hlxwatch.rete_ai import run as run_second_net
        net = run_second_net(cfg, db, pipeline.notifiers if not args.no_notify else [], send=not args.no_notify)
        if net["chiamate"]:
            log.info("Seconda rete: %d commit guardati, %d chiamate, %d voci, %.4f $", net["guardati"],
                     net["chiamate"], net["voci"], net["costo"])
    except Exception:
        log.exception("Seconda rete: non riuscita")
    # contatore di Valve: il numero delle modifiche (Perforce) nei steam.inf di oggi, e l'avviso sulle ondate
    try:
        from hlxwatch.contatore import run as run_counter
        run_counter(cfg, db, pipeline.notifiers, send=not args.no_notify)
    except Exception:
        log.exception("Contatore di Valve: aggiornamento fallito")
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
        print(f"- [fiducia {h.get('fiducia')}] {h.get('titolo')}")
    return 0


def cmd_weekly(args: argparse.Namespace) -> int:
    """Riepilogo della settimana precedente: anteprima a schermo, oppure invio su Telegram con --send."""
    cfg, db = _open(args)
    from hlxwatch.notifiers import load_notifiers
    from hlxwatch.weekly import last_week, maybe_weekly, weekly_title
    report_time = str(cfg.get("general.daily_report_time", "09:00"))
    text = maybe_weekly(cfg, db, load_notifiers(cfg), report_time, force=True, send=args.send, image_path=args.immagine)
    print(weekly_title(*last_week()))
    print()
    print(text or "")
    return 0


def cmd_archeologia(args: argparse.Namespace) -> int:
    """Cerca in tutta la storia dei repository le righe HLX aggiunte o tolte (una volta sola; nessuna notifica)."""
    cfg, db = _open(args)
    from hlxwatch.sources.archaeology import run_archaeology
    summary = run_archaeology(cfg, db, names=args.repo, workdir=args.workdir, keep=args.keep)
    for name, st in summary.items():
        print(f"{name}: {st['commit_con_righe']} commit con voci, {st['voci']} voci "
              f"({st['nuove']} stringhe mai viste), {st['commit_gia_noti']} già noti riletti, {st['secondi']} s"
              + (f" | ERRORE: {st['errore']}" if st.get("errore") else ""))
    return 1 if summary and all(st.get("errore") for st in summary.values()) else 0


def cmd_alyx_storia(args: argparse.Namespace) -> int:
    """Prima comparsa dei nomi hlvr (Half-Life: Alyx) negli archivi pubblici, fino all'uscita del gioco."""
    from hlxwatch import alyx
    data = alyx.run(Path(args.out), workdir=args.workdir, only=args.repo)
    for name, r in data["repos"].items():
        print(f"{name}: " + (f"ERRORE {r['error']}" if r.get("error") else f"archivio dal {r['start']}, {r['names']} nomi"))
    pre = sum(1 for n in data["names"] if n["day"] < data["announced"])
    print(f"{len(data['names'])} nomi hlvr, {pre} prima dell'annuncio -> {args.out}")
    return 1 if data["repos"] and all(r.get("error") for r in data["repos"].values()) else 0


def cmd_progetti_storia(args: argparse.Namespace) -> int:
    """Prima comparsa dei nomi in codice di altri progetti Valve, fino al loro annuncio."""
    from hlxwatch import progetti
    data = progetti.run(Path(args.out), workdir=args.workdir, only=args.progetto)
    for name, r in data["repos"].items():
        print(f"{name}: " + (f"ERRORE {r['error']}" if r.get("error") else f"archivio dal {r['start']}"))
    for key, p in data["projects"].items():
        print(f"{key}: {len(p['names'])} nomi" + (" (troncati)" if p["truncated"] else ""))
    return 1 if data["repos"] and all(r.get("error") for r in data["repos"].values()) else 0


def cmd_prova_passato(args: argparse.Namespace) -> int:
    """Prova sul passato: i rilevatori di oggi sui commit di progetti Valve già annunciati."""
    from hlxwatch import backtest
    cfg = load_config(args.config)
    setup_logging(args.log_level or cfg.get("general.log_level", "INFO"), cfg.log_dir)
    workdir = Path(args.workdir) if args.workdir else cfg.data_dir / "backtest"
    data = backtest.run(cfg, args.prova, workdir, Path(args.out))
    for key, r in data["tests"].items():
        if args.prova and key not in args.prova:
            continue
        if r.get("error"):
            print(f"{key}: ERRORE {r['error']}")
            continue
        first = r["first_related"]
        print(f"{key}: {r['commits']} commit, {r['alerts_total']} avvisi ({r['alerts_high']} alti); "
              + (f"primo avviso sul progetto il {first['day']} ({first['anticipo_giorni']} giorni prima, "
                 f"{first['type']}, {first['score']}): {first['text'][:80]}" if first else "nessun avviso sul progetto")
              + (f"; già presenti all'inizio: {', '.join(r['learned_at_start'])}" if r["learned_at_start"] else ""))
    return 1 if data["tests"] and all(r.get("error") for r in data["tests"].values()) else 0


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
    from hlxwatch.reevaluate import reevaluate
    reevaluate(cfg, db)
    return 0


def cmd_second_net(args: argparse.Namespace) -> int:
    """Anteprima della seconda rete su un commit: righe mandate e risposta di Claude, solo nel log."""
    cfg, db = _open(args)
    from hlxwatch.rete_ai import preview
    out = preview(cfg, db, args.commit, all_lines=args.tutte)
    if out.get("errore"):
        print(out["errore"])
        return 1
    print(f"{out['game']} {out['ref']} del {out['date']}: {out['righe']} righe nuove del motore da guardare")
    for item in out["esempi"]:
        print(f"   {item['file'].split('/')[-1]}: {item['riga'][:140]}")
    answer = out.get("risposta")
    if answer is None:
        print("nessuna risposta (chiave dell'AI assente, nessuna riga o risposta non valida)")
        return 0
    print(f"costo {out.get('costo')} $ - {answer['riassunto']}")
    for v in answer["voci"]:
        print(f"[{v['fiducia']}] {v['sistema']}: {v['riga'][:140]}")
        print(f"      {v['perche']}")
    return 0


def cmd_line_archive(args: argparse.Namespace) -> int:
    """Archivio completo delle righe nuove (archivio_righe.py)."""
    cfg, db = _open(args)
    from hlxwatch import archivio_righe as ar
    kw = {"game": args.gioco, "path_re": args.file, "since": args.dal, "op": "-" if args.tolte else "+"}
    if args.azione == "stato":
        st = ar.stats(db)
        print(f"{st['commit']} commit archiviati dal {st['dal'] or '-'}, {st['righe']} righe, {st['kb']} KB compressi")
    elif args.azione == "riempi":
        print(f"{ar.fill_step(cfg, db, limit=args.quanti)} commit archiviati")
    elif args.azione == "cerca":
        if not args.testo:
            print("serve un'espressione da cercare")
            return 2
        rows = ar.search(db, args.testo, **kw)
        for r in rows[:args.quanti]:
            print(f"{r['date'][:10]} {r['game']:9} {r['sha'][:10]} {r['file']}: {r['op']}{r['text'].strip()[:150]}")
        print(f"{len(rows)} righe")
    else:
        keywords = [k for k in args.testo.split(",") if k] or None
        rows = ar.try_rules(cfg, db, keywords=keywords, **kw)
        for x in rows[:args.quanti]:
            print(f"{x['date'][:10]} {x['game']:9} {x['sha'][:10]} {x['righe']:4} righe, max {x['max']}, "
                  f"{', '.join(x['parole'])}")
            for e in x["esempi"]:
                print(f"      {e}")
        print(f"{len(rows)} commit con righe da avviso")
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    cfg, db = _open(args)
    from hlxwatch.reports.site import build_site
    out = Path(args.out) if args.out else cfg.data_dir / "site"
    from hlxwatch.reports.schede import fill_neighbours
    try:                         # righe vicine delle schede: pochi file per giro, scaricati una volta sola
        n = fill_neighbours(cfg, db)
        if n:
            log.info("Schede delle tracce: righe vicine scaricate per %d tracce", n)
    except Exception as exc:     # noqa: BLE001 - le schede escono lo stesso, senza quelle righe
        log.warning("Schede delle tracce: righe vicine non scaricate (%s)", exc)
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


def cmd_drill(args: argparse.Namespace) -> int:
    """Prova generale del piano annuncio: un finto annuncio, segnato come prova, su ogni canale."""
    cfg, db = _open(args)
    from hlxwatch.announcement import DRILL_HOURS, run_drill
    from hlxwatch.notifiers import load_notifiers
    notifiers = load_notifiers(cfg)
    if not notifiers:
        print("Nessun notificatore abilitato: controlla notifiers.* in config.yaml e i valori in .env")
    ok = run_drill(cfg, db, notifiers)
    print(("Prova generale inviata a: " + ", ".join(n.name for n in notifiers)) if ok else "Prova generale: invio fallito")
    print(f"L'app Android la trova nel sito per {DRILL_HOURS} ore, dopo che il sito è stato rigenerato")
    return 0 if ok else 1


def cmd_guard(args: argparse.Namespace) -> int:
    """Protezione dell'archivio: dice al workflow se l'archivio si può salvare (ok=true/false in GITHUB_OUTPUT)."""
    import os
    cfg, db = _open(args)
    from hlxwatch.archive_guard import run_guard
    from hlxwatch.notifiers import load_notifiers
    ok = run_guard(cfg, db, args.old, args.accetta, load_notifiers(cfg) if not args.no_notify else [])
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("ok=true\n" if ok else "ok=false\n")
    print("archivio da salvare" if ok else "archivio NON salvato")
    return 0                         # il run continua: decide il workflow con l'output ok


def cmd_site_check(args: argparse.Namespace) -> int:
    """Controllo del sito generato: dice al workflow se pubblicarlo (pubblica=true/false in GITHUB_OUTPUT)."""
    import os
    cfg, db = _open(args)
    from hlxwatch.notifiers import load_notifiers
    from hlxwatch.site_check import run_site_check
    try:
        result = run_site_check(db, Path(args.site), load_notifiers(cfg) if not args.no_notify else [])
        publish = not result["grave"]
    except Exception:                        # un errore del controllo stesso non ferma la pubblicazione
        log.exception("Controllo del sito non eseguito")
        result, publish = None, True
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("pubblica=true\n" if publish else "pubblica=false\n")
    if result is not None:
        print("sito a posto" if result["ok"] else "problemi nel sito:\n- " + "\n- ".join(result["problemi"]))
    print("sito da pubblicare" if publish else "sito NON pubblicato: resta online la versione precedente")
    return 0                                 # il run continua: decide il workflow con l'output pubblica


def cmd_lessons(args: argparse.Namespace) -> int:
    """Registro degli errori: gli errori già corretti devono restare corretti."""
    cfg, db = _open(args)
    from hlxwatch.lezioni import run_check
    from hlxwatch.notifiers import load_notifiers
    try:
        result = run_check(cfg, db, load_notifiers(cfg) if not args.no_notify else [])
    except Exception:                        # il controllo stesso non deve fermare il giro
        log.exception("Registro degli errori non controllato")
        return 0
    print(f"{result['n']} esempi, " + ("tutti rispettati" if result["ok"] else f"{len(result['falliti'])} non rispettati:"))
    for f in result["falliti"]:
        print(f"- {f['id']}: {f['problema']}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Revisione settimanale con l'AI: proposte di correzione (con --invia su Telegram)."""
    cfg, db = _open(args)
    from hlxwatch.notifiers import load_notifiers
    from hlxwatch.revisione import describe_proposal, run_review
    found = run_review(cfg, db, load_notifiers(cfg) if args.invia else [], force=True, send_it=args.invia, save=args.invia)
    if found is None:
        print("Revisione non eseguita (AI disattivata, chiave assente o limite di spesa)")
        return 0
    print(f"{len(found)} proposte" + ("" if args.invia else " (anteprima: non salvate né inviate)"))
    for n, p in enumerate(found, 1):
        print(f"{n}. {describe_proposal(p)}")
    return 0


def cmd_noise_check(args: argparse.Namespace) -> int:
    """Controllo dei falsi allarmi: i tipi di voce che mandano molti avvisi senza tracce HLX (anteprima)."""
    cfg, db = _open(args)
    from hlxwatch.revisione import describe_proposal, noisy_groups
    found = noisy_groups(cfg, db)
    print(f"{len(found)} tipi di voce da silenziare (anteprima: niente viene inviato)")
    for n, p in enumerate(found, 1):
        print(f"{n}. {describe_proposal(p)}")
    return 0


def cmd_restore_check(args: argparse.Namespace) -> int:
    """Prova di ripristino dell'ultima copia settimanale (una volta a settimana, salvo --forza)."""
    cfg, db = _open(args)
    from hlxwatch.notifiers import load_notifiers
    from hlxwatch.restore_check import RestoreCheck
    try:
        result = RestoreCheck(cfg, db).run(load_notifiers(cfg), force=args.forza)
    except Exception as exc:                 # GitHub non risponde: si riprova al prossimo giro, il run continua
        log.warning("Prova di ripristino non eseguita: %s", exc)
        return 0
    if result is None:
        print("prova di ripristino non dovuta (o nessuna copia da provare)")
    else:
        print(("copia ripristinabile: " if result["ok"] else "copia NON ripristinabile: ") + result["file"])
    return 0                                 # il risultato va nello stato e sul sito; il run continua


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
                   help="elenco separato da virgola: discovery,valve,certs,domains,hardware,achievements,github,summaries,news,steam,local,fullscan (la scansione completa parte solo se è dovuta)")
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

    p = sub.add_parser("archeologia", help="cerca in tutta la storia dei repository le righe HLX aggiunte o tolte")
    p.add_argument("--repo", action="append", help="limita a un repo per nome; ripetibile")
    p.add_argument("--workdir", help="cartella per le copie dei repository (default: temporanea)")
    p.add_argument("--keep", action="store_true", help="non cancellare le copie alla fine")
    p.set_defaults(func=cmd_archeologia)

    p = sub.add_parser("alyx-storia", help="prima comparsa dei nomi hlvr (Half-Life: Alyx) negli archivi, fino al 2020")
    p.add_argument("--out", default=str(Path(__file__).with_name("alyx_seed.json")), help="file JSON da scrivere")
    p.add_argument("--repo", action="append", help="limita a un repo per nome (steamvr, dota2, ...); ripetibile")
    p.add_argument("--workdir", help="cartella per le copie dei repository (default: temporanea)")
    p.set_defaults(func=cmd_alyx_storia)

    p = sub.add_parser("progetti-storia", help="prima comparsa dei nomi in codice di altri progetti Valve (Deadlock, Deck...)")
    p.add_argument("--out", default=str(Path(__file__).with_name("progetti_seed.json")), help="file JSON da scrivere")
    p.add_argument("--progetto", action="append", help="limita a un progetto (deadlock, cs2, deck, frame); ripetibile")
    p.add_argument("--workdir", help="cartella per le copie dei repository (default: temporanea)")
    p.set_defaults(func=cmd_progetti_storia)

    p = sub.add_parser("prova-passato", help="prova sul passato: gli avvisi che HLX Watch avrebbe dato prima di annunci veri")
    p.add_argument("--out", default=str(Path(__file__).with_name("backtest_seed.json")), help="file JSON da scrivere")
    p.add_argument("--prova", action="append", help="limita a una prova (frame_2022, deadlock_2022...); ripetibile")
    p.add_argument("--workdir", help="cartella di lavoro (default: data/backtest, cancellata alla fine)")
    p.set_defaults(func=cmd_prova_passato)

    p = sub.add_parser("weekly", help="riepilogo della settimana precedente (anteprima; --send per Telegram)")
    p.add_argument("--send", action="store_true", help="invia il riepilogo su Telegram")
    p.add_argument("--immagine", help="salva anche l'immagine della settimana in questo file (anteprima)")
    p.set_defaults(func=cmd_weekly)

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

    p = sub.add_parser("controlla-archivio", help="prima di salvare: l'archivio non deve essersi svuotato")
    p.add_argument("--old", help="stats.json dell'ultimo salvataggio (ramo data)")
    p.add_argument("--accetta", action="store_true", help="accetta un calo voluto (dopo una modifica al config)")
    p.add_argument("--no-notify", action="store_true", help="non inviare l'avviso")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("controlla-sito", help="dopo la generazione: link, file, dati dell'app e numeri del sito")
    p.add_argument("--site", default="site", help="cartella del sito generato")
    p.add_argument("--no-notify", action="store_true", help="non inviare l'avviso")
    p.set_defaults(func=cmd_site_check)

    p = sub.add_parser("rete-ai", help="seconda rete con l'AI: anteprima su un commit (niente salvato né inviato)")
    p.add_argument("commit", help="sha del commit (anche solo l'inizio)")
    p.add_argument("--tutte", action="store_true", help="anche le righe che oggi sono già avvisi delle parole chiave")
    p.set_defaults(func=cmd_second_net)

    p = sub.add_parser("archivio-righe", help="archivio completo delle righe nuove: stato, ricerca, prova delle regole")
    p.add_argument("azione", choices=["stato", "cerca", "prova", "riempi"])
    p.add_argument("testo", nargs="?", default="", help="per cerca: espressione regolare; per prova: parole chiave (a,b)")
    p.add_argument("--gioco", default=None)
    p.add_argument("--file", default=None, help="espressione regolare sul percorso del file")
    p.add_argument("--dal", default=None, help="data minima del commit (AAAA-MM-GG)")
    p.add_argument("--tolte", action="store_true", help="cerca tra le righe tolte invece che tra le aggiunte")
    p.add_argument("--quanti", type=int, default=40, help="risultati mostrati / commit da riempire")
    p.set_defaults(func=cmd_line_archive)

    p = sub.add_parser("controlla-errori", help="registro degli errori: gli errori già corretti non devono tornare")
    p.add_argument("--no-notify", action="store_true", help="non inviare l'avviso")
    p.set_defaults(func=cmd_lessons)

    p = sub.add_parser("revisione", help="revisione con l'AI degli avvisi della settimana: proposte di correzione")
    p.add_argument("--invia", action="store_true", help="manda le proposte su Telegram con i pulsanti")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("falsi-allarmi", help="tipi di voce con molti avvisi senza tracce HLX (anteprima)")
    p.set_defaults(func=cmd_noise_check)

    p = sub.add_parser("bluesky-prova", help="i post che HLX Watch farebbe su Bluesky, senza pubblicarli")
    p.add_argument("--giorni", type=int, default=30, help="avvisi degli ultimi N giorni (predefinito 30)")
    p.add_argument("--simula-serata", action="store_true",
                   help="mostra i post della serata dei Game Awards con tre annunci finti del bot")
    p.set_defaults(func=cmd_bluesky_test)

    p = sub.add_parser("prova-ripristino", help="scarica l'ultima copia settimanale dell'archivio e verifica che si apra")
    p.add_argument("--forza", action="store_true", help="anche se non è passata una settimana dall'ultima prova")
    p.set_defaults(func=cmd_restore_check)

    p = sub.add_parser("prova-annuncio", help="prova generale: un finto annuncio, segnato come prova, su ogni canale")
    p.set_defaults(func=cmd_drill)

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
