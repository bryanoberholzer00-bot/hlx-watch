"""Data di prima comparsa delle occorrenze "già presenti" trovate dalla scansione completa.

Per ogni file elenca, con l'API di GitHub, i commit che lo hanno toccato e cerca per bisezione il
primo in cui la riga compare: bastano 8-10 versioni scaricate anche per file con centinaia di commit.
Le versioni scaricate sono condivise tra le righe dello stesso file. Il lavoro è diviso tra i run
con un tetto di download per run (first_seen.max_downloads_per_run) e i risultati restano nel database.

Se la riga c'è già nella versione più vecchia tracciata, la data è "almeno dal" (at_least = 1):
il repository SteamTracking non ha storia precedente.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from hlxwatch.config import Config
from hlxwatch.sources.github_tracking import GitHubClient
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)


def norm(text: str) -> str:
    return " ".join(text.split())


def line_hash(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8", "replace")).hexdigest()


class FirstSeenResolver:
    def __init__(self, cfg: Config, db: Database, client: GitHubClient | None = None):
        self.cfg = cfg
        self.db = db
        self.fcfg: dict[str, Any] = cfg.get("first_seen", {}) or {}
        self.client = client or GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                                             cfg.get("github.raw_url", "https://raw.githubusercontent.com"),
                                             cfg.github_token)
        self.max_bytes = int(cfg.get("github.fallback_max_bytes", 25 * 1024 * 1024))
        self.max_pages = int(self.fcfg.get("max_commit_pages", 20))
        self.repos = {r["name"]: r for r in cfg.github_repos}
        self.downloads = 0

    # ------------------------------------------------------------ github
    def commits_for(self, repo: str, branch: str, path: str) -> list[tuple[str, str]]:
        """(sha, data) dei commit che toccano il file, dal più vecchio al più nuovo."""
        out: list[tuple[str, str]] = []
        for page in range(1, self.max_pages + 1):
            data = self.client.get_json(f"/repos/{repo}/commits",
                                        {"sha": branch, "path": path, "per_page": 100, "page": page}) or []
            out += [(c["sha"], ((c.get("commit") or {}).get("committer") or {}).get("date", "")) for c in data]
            if len(data) < 100:
                break
        out.reverse()
        return out

    def lines_at(self, cache: dict[str, set[str] | None], repo: str, sha: str, path: str) -> set[str] | None:
        if sha not in cache:
            text = self.client.raw_file(repo, sha, path, self.max_bytes)
            self.downloads += 1
            cache[sha] = None if text is None else {norm(l) for l in text.splitlines()}
        return cache[sha]

    # ------------------------------------------------------------- core
    def resolve_file(self, game: str, path: str, texts: list[str], budget: int) -> int:
        """Trova la prima comparsa di ogni riga del file; ritorna quante righe ha risolto."""
        repo_cfg = self.repos.get(game)
        if not repo_cfg:
            return 0
        repo, branch = repo_cfg["repo"], repo_cfg.get("branch", "master")
        commits = self.commits_for(repo, branch, path)
        if not commits:
            return 0
        cache: dict[str, set[str] | None] = {}
        solved = 0
        for text in texts:
            if self.downloads >= budget:
                break
            target = norm(text)
            lo, hi = 0, len(commits) - 1
            newest = self.lines_at(cache, repo, commits[hi][0], path)
            if newest is None or target not in newest:
                # riga non trovata nell'ultima versione del file (spostata o file troppo grande): segna e passa oltre
                self.db.set_first_seen(game, path, line_hash(text), text, None, None, at_least=False, unresolved=True)
                continue
            while lo < hi and self.downloads < budget:
                mid = (lo + hi) // 2
                version = self.lines_at(cache, repo, commits[mid][0], path)
                if version is not None and target in version:
                    hi = mid
                else:
                    lo = mid + 1
            if lo < hi:
                break     # budget finito a metà ricerca: riprende al prossimo run
            sha, date = commits[lo]
            self.db.set_first_seen(game, path, line_hash(text), text, sha, date, at_least=(lo == 0))
            solved += 1
        return solved

    def run(self) -> int:
        if not self.fcfg.get("enabled", True):
            return 0
        budget = int(self.fcfg.get("max_downloads_per_run", 60))
        pending = self.db.present_without_first_seen(limit=500)
        if not pending:
            return 0
        by_file: dict[tuple[str, str], list[str]] = {}
        for row in pending:
            by_file.setdefault((row["game"], row["file_path"]), []).append(row["text"])
        solved = 0
        for (game, path), texts in by_file.items():
            if self.downloads >= budget:
                break
            try:
                solved += self.resolve_file(game, path, texts, budget)
            except Exception:
                log.exception("Prima comparsa: errore su %s:%s", game, path)
        log.info("Prima comparsa: %d righe datate in questo run (%d versioni scaricate, %d in attesa)",
                 solved, self.downloads, max(0, len(pending) - solved))
        return solved
