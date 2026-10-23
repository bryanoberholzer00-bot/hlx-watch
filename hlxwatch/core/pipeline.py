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
from hlxwatch.sources import GitHubTrackingSource, LocalFilesSource, SteamNewAppsSource, SteamPicsSource
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)
ALL_SOURCES = ("github", "steam", "local")


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    alerts: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.github = GitHubTrackingSource(cfg, db)
        self.steam = SteamPicsSource(cfg, db)
        self.steam_apps = SteamNewAppsSource(cfg, db, self.steam)
        self.local = LocalFilesSource(cfg, db)
        self.notifiers = load_notifiers(cfg)

    def select_alerts(self, findings: Iterable[Finding]) -> list[Finding]:
        min_score = int(self.cfg.get("general.notify_min_score", 50))
        return [f for f in findings if f.is_new and f.score >= min_score and f.change_type != "baseline"]

    def run_scan(self, *, sources: Iterable[str] = ALL_SOURCES, notify: bool = True,
                 github_since: datetime | None = None, repos: list[str] | None = None,
                 max_commits: int | None = None, heading: str | None = None) -> ScanResult:
        sources = tuple(sources)
        started = time.time()
        result = ScanResult()
        log.info("=== Scansione avviata (sorgenti: %s) ===", ", ".join(sources))

        if "github" in sources:
            try:
                result.findings += self.github.run(since=github_since, repos=repos, max_commits=max_commits)
            except Exception as exc:
                log.exception("Sorgente GitHub fallita")
                result.errors.append(f"github: {exc}")

        if "steam" in sources and self.cfg.get("steam.enabled", True):
            changed: list[int] = []
            try:
                steam_findings, changed = self.steam.run()
                result.findings += steam_findings
            except Exception as exc:
                log.exception("Sorgente Steam PICS fallita")
                result.errors.append(f"steam_pics: {exc}")
            try:
                result.findings += self.steam_apps.run(changed)
            except Exception as exc:
                log.exception("Rilevamento nuove app Steam fallito")
                result.errors.append(f"steam_apps: {exc}")

        if "local" in sources:
            try:
                result.findings += self.local.run()
            except Exception as exc:
                log.exception("Sorgente file locali fallita")
                result.errors.append(f"local: {exc}")

        result.alerts = self.select_alerts(result.findings)
        if result.alerts:
            if notify and self.notifiers:
                sent = notify_findings(self.notifiers, result.alerts, self.cfg,
                                       heading or f"HLX Watch: {len(result.alerts)} novità da controllare")
                if sent:
                    self.db.mark_notified(f.id for f in result.alerts if f.id is not None)
            elif notify:
                log.info("%d voci supererebbero la soglia ma nessun notificatore è configurato", len(result.alerts))
            for finding in sorted(result.alerts, key=lambda f: f.score, reverse=True)[:15]:
                log.info("  [%3d] %-9s %-12s %s  (%s)", finding.score, finding.game, finding.change_type,
                         (finding.token or finding.text)[:70], finding.file_path[:60])

        self.db.set_state("last_scan_at", iso(utcnow()))
        log.info("=== Scansione completata in %.0fs: %d voci, %d nuove sopra soglia, %d errori ===",
                 time.time() - started, len(result.findings), len(result.alerts), len(result.errors))
        return result
