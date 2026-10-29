"""Orchestrazione: sorgenti -> analisi -> storage -> deduplica -> notifiche."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.notifiers import load_notifiers, notify_findings
from hlxwatch.sources import (FullScanSource, GitHubTrackingSource, LocalFilesSource, SteamNewAppsSource,
                              SteamPicsSource)
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)
ALL_SOURCES = ("discovery", "structure", "github", "summaries", "steam", "local", "fullscan")
SILENT_CHANGE_TYPES = ("baseline", "present")   # registrati senza avvisi (stato iniziale)


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    alerts: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: dict = field(default_factory=dict)       # fonte -> None se ok, altrimenti l'errore

    @property
    def all_failed(self) -> bool:
        """Vero solo se ogni fonte provata è fallita: è il caso in cui il run va segnato come fallito."""
        return bool(self.status) and all(v is not None for v in self.status.values())


class Pipeline:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.github = GitHubTrackingSource(cfg, db)
        self.steam = SteamPicsSource(cfg, db)
        self.steam_apps = SteamNewAppsSource(cfg, db, self.steam)
        self.local = LocalFilesSource(cfg, db)
        self.full = FullScanSource(cfg, db)
        from hlxwatch.sources.discovery import RepoDiscovery
        self.discovery = RepoDiscovery(cfg, db, self.github.client)
        from hlxwatch.sources.structure import StructureWatch
        self.structure = StructureWatch(cfg, db, self.github.client)
        from hlxwatch.sources.summaries import CommitSummaries
        self.summaries = CommitSummaries(cfg, db, self.github.client)
        self.notifiers = load_notifiers(cfg)
        from hlxwatch.health import Health
        self.health = Health(cfg, db, self.notifiers)

    def select_alerts(self, findings: Iterable[Finding]) -> list[Finding]:
        min_score = int(self.cfg.get("general.notify_min_score", 50))
        return [f for f in findings if f.is_new and f.score >= min_score and f.change_type not in SILENT_CHANGE_TYPES]

    def run_scan(self, *, sources: Iterable[str] = ALL_SOURCES, notify: bool = True,
                 github_since: datetime | None = None, repos: list[str] | None = None,
                 max_commits: int | None = None, heading: str | None = None,
                 force_full: bool = False) -> ScanResult:
        sources = tuple(sources)
        started = time.time()
        result = ScanResult()
        log.info("=== Scansione avviata (sorgenti: %s) ===", ", ".join(sources))

        status: dict[str, str | None] = result.status      # fonte -> None se ok, altrimenti il primo errore

        if "discovery" in sources:
            try:
                result.findings += self.discovery.run()
                status["discovery"] = None
            except Exception as exc:
                log.exception("Scoperta dei repository fallita")
                result.errors.append(f"discovery: {exc}")
                status["discovery"] = str(exc)

        if "structure" in sources:
            try:
                result.findings += self.structure.run()
                status["structure"] = None
            except Exception as exc:
                log.exception("Controllo della struttura dei repository fallito")
                result.errors.append(f"structure: {exc}")
                status["structure"] = str(exc)

        if "github" in sources:
            try:
                result.findings += self.github.run(since=github_since, repos=repos, max_commits=max_commits)
                try:
                    self.github.backfill_renames()      # una volta sola; le voci sono retrodatate, niente notifiche
                except Exception:
                    log.exception("Recupero delle rinomine passate fallito, si riprova al prossimo run")
                errs = getattr(self.github, "errors", [])
                result.errors += [f"github: {e}" for e in errs]
                status["github"] = errs[0] if errs else None
            except Exception as exc:
                log.exception("Sorgente GitHub fallita")
                result.errors.append(f"github: {exc}")
                status["github"] = str(exc)

        if "summaries" in sources:
            try:
                result.findings += self.summaries.run()
                status["summaries"] = None
            except Exception as exc:
                log.exception("Lettura dei riassunti di SteamTracking fallita")
                result.errors.append(f"summaries: {exc}")
                status["summaries"] = str(exc)

        if "steam" in sources and self.cfg.get("steam.enabled", True):
            changed: list[int] = []
            try:
                steam_findings, changed = self.steam.run()
                result.findings += steam_findings
                status["steam_pics"] = getattr(self.steam, "last_error", None)
                if status["steam_pics"]:
                    result.errors.append(f"steam_pics: {status['steam_pics']}")
            except Exception as exc:
                log.exception("Sorgente Steam PICS fallita")
                result.errors.append(f"steam_pics: {exc}")
                status["steam_pics"] = str(exc)
            try:
                result.findings += self.steam_apps.run(changed)
                status["steam_apps"] = None
            except Exception as exc:
                log.exception("Rilevamento nuove app Steam fallito")
                result.errors.append(f"steam_apps: {exc}")
                status["steam_apps"] = str(exc)

        if "local" in sources:
            try:
                result.findings += self.local.run()
            except Exception as exc:
                log.exception("Sorgente file locali fallita")
                result.errors.append(f"local: {exc}")

        if "fullscan" in sources:
            try:
                result.findings += self.full.run(force=force_full, repos=repos)
                errs = getattr(self.full, "errors", [])
                result.errors += [f"fullscan: {e}" for e in errs]
                status["fullscan"] = errs[0] if errs else None
            except Exception as exc:
                log.exception("Scansione completa fallita")
                result.errors.append(f"fullscan: {exc}")
                status["fullscan"] = str(exc)
            try:
                from hlxwatch.sources.first_seen import FirstSeenResolver
                FirstSeenResolver(self.cfg, self.db, client=self.github.client).run()
                status["first_seen"] = None
            except Exception as exc:
                log.exception("Ricerca della prima comparsa fallita")
                result.errors.append(f"first_seen: {exc}")
                status["first_seen"] = str(exc)

        result.alerts = self.select_alerts(result.findings)
        if result.alerts:
            if notify and self.notifiers:
                sent = notify_findings(self.notifiers, result.alerts, self.cfg,
                                       heading or f"HLX Watch: {len(result.alerts)} novità da controllare")
                if sent:
                    self.db.mark_notified(f.id for f in result.alerts if f.id is not None)
                status["notify"] = None if sent else "invio a Telegram/Discord non riuscito"
            elif notify:
                log.info("%d voci supererebbero la soglia ma nessun notificatore è configurato", len(result.alerts))
            for finding in sorted(result.alerts, key=lambda f: f.score, reverse=True)[:15]:
                log.info("  [%3d] %-9s %-12s %s  (%s)", finding.score, finding.game, finding.change_type,
                         (finding.token or finding.text)[:70], finding.file_path[:60])

        self.db.set_state("last_scan_at", iso(utcnow()))
        # controllo di salute: un avviso dopo più fallimenti di fila, uno quando la fonte riparte
        if notify:
            try:
                for source, error in status.items():
                    self.health.record(source, error is None, error or "")
                if "github" in sources:
                    self.health.check_upstream()
            except Exception:
                log.exception("Controllo di salute fallito")
        log.info("=== Scansione completata in %.0fs: %d voci, %d nuove sopra soglia, %d errori ===",
                 time.time() - started, len(result.findings), len(result.alerts), len(result.errors))
        return result
