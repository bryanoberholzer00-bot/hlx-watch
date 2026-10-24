"""Modalità daemon: scansione periodica + report giornaliero."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from hlxwatch.config import Config
from hlxwatch.core import Pipeline
from hlxwatch.notifiers import notify_text
from hlxwatch.reports import generate_report, summary_text
from hlxwatch.storage import Database

log = logging.getLogger(__name__)


def maybe_daily_report(cfg: Config, db: Database, pipeline: Pipeline, report_time: str) -> bool:
    """Manda il report delle ultime 24 ore una volta al giorno, alla prima occasione dopo `report_time`.

    Non dipende da un orario preciso: funziona sia nel daemon sia su GitHub Actions, dove i run
    pianificati possono partire con ore di ritardo."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if db.get_state("daemon:last_report_day") == today or now.strftime("%H:%M") < report_time:
        return False
    paths, data = generate_report(db, cfg, hours=24, fmt="both")
    log.info("Report giornaliero generato: %s", ", ".join(str(p) for p in paths))
    if pipeline.notifiers:
        notify_text(pipeline.notifiers, f"HLX Watch: report del {today} (ultime 24 ore)", summary_text(data))
    db.set_state("daemon:last_report_day", today)
    return True


def run_daemon(cfg: Config, db: Database, pipeline: Pipeline) -> None:
    interval = max(5, int(cfg.get("general.scan_interval_minutes", 45))) * 60
    report_time = str(cfg.get("general.daily_report_time", "09:00"))
    log.info("Daemon avviato: scansione ogni %d minuti, report giornaliero alle %s. Ctrl+C per uscire.",
             interval // 60, report_time)
    while True:
        try:
            pipeline.run_scan()
        except Exception:
            log.exception("Scansione fallita, riprovo al prossimo giro")
        next_run = time.time() + interval
        log.info("Prossima scansione alle %s", datetime.fromtimestamp(next_run).strftime("%H:%M:%S"))
        while time.time() < next_run:
            try:
                maybe_daily_report(cfg, db, pipeline, report_time)
            except Exception:
                log.exception("Report giornaliero fallito")
            time.sleep(min(30.0, max(0.0, next_run - time.time())))
