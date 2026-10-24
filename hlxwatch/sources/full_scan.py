"""Scansione completa del contenuto attuale dei repository GameTracking.

Il monitoraggio dei commit vede solo ciò che cambia. Questa sorgente, una volta al giorno, prende
l'ultima versione di ogni repository (clone superficiale, senza checkout) e cerca le parole chiave
ad alto valore in TUTTI i file, anche quelli troppo grandi per la ricerca di GitHub o esclusi dai
commit giganti (oltre 3000 file l'API non li mostra).

- primo giro per repository: le occorrenze trovate diventano voci "present" (c'erano già), senza avvisi;
- giri successivi: confronto con l'elenco precedente, occorrenze nuove "added" e sparite "removed",
  tralasciando quelle già registrate dal monitoraggio dei commit.

La ricerca usa `git grep` direttamente sugli oggetti git: niente working tree, niente problemi con
percorsi lunghi o nomi di file non validi su Windows.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

DEFAULT_PREFILTER = r"hlx|hl3|half[ _-]?life|left[ _-]?4[ _-]?dead|l4d3"
MAX_LINE = 2000
SEP = "\t"


class FullScanSource:
    name = "fullscan"

    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.fcfg: dict[str, Any] = cfg.get("full_scan", {}) or {}
        self.mirror_root = cfg.data_dir / "mirrors"
        self.min_score = int(self.fcfg.get("min_score", 50))
        self.interval = float(self.fcfg.get("interval_hours", 24))
        self.timeout = int(self.fcfg.get("git_timeout_seconds", 1800))
        self.exclude = {str(x) for x in (self.fcfg.get("exclude_repos") or [])}
        codenames = [re.escape(str(c)) for c in (cfg.keywords.get("codenames") or []) if str(c).strip()]
        self.prefilter = "|".join([self.fcfg.get("prefilter") or DEFAULT_PREFILTER, *codenames])
        self.ignore_patterns = [re.compile(p) for p in (cfg.get("github.ignore_path_patterns", []) or [])]
        self.max_per_repo = int(self.fcfg.get("max_findings_per_repo", 2000))

    # ------------------------------------------------------------------ run
    def due(self) -> bool:
        last = parse_iso(self.db.get_state("fullscan:last_at"))
        return last is None or utcnow() - last >= timedelta(hours=self.interval)

    def run(self, *, force: bool = False, repos: list[str] | None = None) -> list[Finding]:
        if not self.fcfg.get("enabled", True):
            return []
        if not force and not self.due():
            log.debug("Scansione completa: non ancora dovuta")
            return []
        if shutil.which("git") is None:
            log.error("Scansione completa: git non trovato nel PATH")
            return []
        findings: list[Finding] = []
        for repo_cfg in tracked_repos(self.cfg, self.db):
            name = repo_cfg["name"]
            if (repos and name not in repos) or name in self.exclude:
                continue
            try:
                findings += self.scan_repo(repo_cfg)
            except Exception:
                log.exception("Scansione completa: errore su %s, continuo con gli altri", name)
        if not repos:
            self.db.set_state("fullscan:last_at", iso(utcnow()))
        return findings

    # ------------------------------------------------------------------ git
    def _git(self, args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True,
                              timeout=self.timeout)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:3])} fallito: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
        return proc

    def update_mirror(self, repo_cfg: dict[str, Any]) -> tuple[Path, str]:
        """Clone superficiale senza checkout, oppure aggiornamento all'ultimo commit. Ritorna (cartella, sha)."""
        name, branch = repo_cfg["name"], repo_cfg.get("branch", "master")
        url = repo_cfg.get("clone_url") or f"https://github.com/{repo_cfg['repo']}.git"
        path = self.mirror_root / name
        if (path / ".git").exists() or (path / "HEAD").exists():
            self._git(["fetch", "--quiet", "--depth", "1", "origin", branch], cwd=path)
            self._git(["update-ref", "HEAD", "FETCH_HEAD"], cwd=path)
            # gli oggetti dei giri precedenti non servono più
            self._git(["reflog", "expire", "--expire=now", "--all"], cwd=path, check=False)
            self._git(["gc", "--quiet", "--prune=now"], cwd=path, check=False)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(path, ignore_errors=True)
            self._git(["clone", "--quiet", "--depth", "1", "--no-checkout", "--single-branch",
                       "--branch", branch, url, str(path)])
        sha = self._git(["rev-parse", "HEAD"], cwd=path).stdout.decode().strip()
        return path, sha

    def grep(self, path: Path) -> list[tuple[str, int, str]]:
        """(file, riga, testo) per ogni riga che contiene un termine del prefiltro."""
        proc = self._git(["-c", "grep.threads=4", "grep", "-I", "-n", "-z", "-i", "-E", "-e", self.prefilter,
                          "HEAD", "--", "."], cwd=path, check=False)
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git grep fallito: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
        out: list[tuple[str, int, str]] = []
        for raw in proc.stdout.split(b"\n"):
            # formato con -z: HEAD:percorso\0riga\0testo
            parts = raw.split(b"\0", 2)
            if len(parts) != 3:
                continue
            ref_path, line_no, text = parts
            file_path = ref_path.decode("utf-8", "replace").split(":", 1)[-1]
            try:
                out.append((file_path, int(line_no), text.decode("utf-8", "replace")[:MAX_LINE]))
            except ValueError:
                continue
        return out

    # ----------------------------------------------------------------- scan
    def scan_repo(self, repo_cfg: dict[str, Any]) -> list[Finding]:
        name, repo = repo_cfg["name"], repo_cfg["repo"]
        path, sha = self.update_mirror(repo_cfg)
        matcher = KeywordMatcher(self.cfg.keywords, ignore_ids=repo_cfg.get("ignore_keywords", []) or [])

        current: dict[str, tuple[str, int, str, Any]] = {}
        for file_path, line_no, text in self.grep(path):
            if any(p.search(file_path) for p in self.ignore_patterns):
                continue
            res = matcher.analyze_line(text, file_path=file_path, change_type="added")
            if res is None or res.score < self.min_score:
                continue
            key = f"{file_path}{SEP}{' '.join(text.split())}"
            current.setdefault(key, (file_path, line_no, text.strip(), res))

        snap_key = f"fullscan:{name}"
        previous = self.db.get_snapshot(snap_key)
        old_keys = set((previous[1] or "").splitlines()) if previous else set()
        new_keys = set(current)
        content = "\n".join(sorted(new_keys))
        self.db.set_snapshot(snap_key, hashlib.sha1(content.encode("utf-8", "replace")).hexdigest(), content)

        build = BuildInfo(game=name, source="fullscan", ref=f"tree:{sha[:12]}", date=utcnow(),
                          url=f"https://github.com/{repo}/tree/{sha}",
                          message=f"Scansione completa del contenuto ({len(new_keys)} occorrenze)")
        findings: list[Finding] = []

        def make(file_path: str, line_no: int | None, text: str, res: Any, change_type: str) -> Finding:
            return Finding(game=name, source="fullscan", file_path=file_path, change_type=change_type, text=text,
                           score=res.score, keywords=res.keywords, token=res.token, line_no=line_no,
                           context=f"contenuto attuale di {repo}@{sha[:10]}")

        if previous is None:
            for key in sorted(new_keys)[: self.max_per_repo]:
                file_path, line_no, text, res = current[key]
                findings.append(make(file_path, line_no, text, res, "present"))
            log.info("[%s] scansione completa iniziale: %d occorrenze registrate come già presenti", name, len(findings))
        else:
            for key in sorted(new_keys - old_keys):
                file_path, line_no, text, res = current[key]
                if self._already_recorded(name, file_path, text, "added"):
                    continue
                findings.append(make(file_path, line_no, text, res, "added"))
            for key in sorted(old_keys - new_keys):
                file_path, _, text = key.partition(SEP)
                res = matcher.analyze_line(text, file_path=file_path, change_type="removed")
                if res is None or self._already_recorded(name, file_path, text, "removed"):
                    continue
                findings.append(make(file_path, None, text, res, "removed"))
            log.info("[%s] scansione completa: %d occorrenze, %d nuove, %d sparite (non già viste nei commit)",
                     name, len(new_keys), sum(f.change_type == "added" for f in findings),
                     sum(f.change_type == "removed" for f in findings))

        if findings:
            build_id = self.db.upsert_build(build)
            for finding in findings:
                finding.build = build
                self.db.add_finding(finding, build_id)
        return findings

    def _already_recorded(self, game: str, file_path: str, text: str, change_type: str) -> bool:
        """La stessa riga è già stata registrata dal monitoraggio dei commit: non duplicarla."""
        row = self.db.conn.execute(
            "SELECT 1 FROM findings WHERE game = ? AND file_path = ? AND text = ? AND change_type IN (?, 'changed') LIMIT 1",
            (game, file_path, text.strip(), change_type),
        ).fetchone()
        return row is not None
