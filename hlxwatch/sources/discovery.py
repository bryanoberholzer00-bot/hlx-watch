"""Scoperta dei nuovi repository di SteamTracking.

SteamTracking crea un repository GameTracking-<gioco> appena un gioco Valve ha dei file su Steam che
vale la pena seguire: un repository nuovo può essere il segnale più forte, prima di un annuncio.

- primo giro: registra i repository esistenti, senza avvisi;
- giri successivi: ogni repository nuovo diventa una voce (avviso su Telegram); se il nome o la
  descrizione richiamano le parole chiave (hlx, Half-Life 3...) il punteggio è alto;
- i repository GameTracking-* nuovi vengono seguiti automaticamente dalla data di creazione.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import add_followed, game_key, tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import parse_iso, utcnow

log = logging.getLogger(__name__)
KNOWN_KEY = "discovery:known"


class RepoDiscovery:
    name = "discovery"

    def __init__(self, cfg: Config, db: Database, client: Any):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.dcfg: dict[str, Any] = cfg.get("repo_discovery", {}) or {}
        self.org = str(self.dcfg.get("org", "SteamTracking"))
        self.matcher = KeywordMatcher(cfg.keywords)

    def list_repos(self) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for page in range(1, 6):
            data = self.client.get_json(f"/orgs/{self.org}/repos",
                                        {"per_page": 100, "page": page, "type": "public", "sort": "created"})
            if not data:
                break
            repos += data
            if len(data) < 100:
                break
        return repos

    def run(self) -> list[Finding]:
        if not self.dcfg.get("enabled", True):
            return []
        repos = self.list_repos()
        if not repos:
            raise RuntimeError(f"nessuna risposta da GitHub per l'organizzazione {self.org}")
        known = set(json.loads(self.db.get_state(KNOWN_KEY) or "[]"))
        names = {r["full_name"] for r in repos}
        if not known:
            self.db.set_state(KNOWN_KEY, json.dumps(sorted(names)))
            log.info("Scoperta repository: %d repository di %s registrati come già noti", len(names), self.org)
            return []

        findings: list[Finding] = []
        for repo in sorted((r for r in repos if r["full_name"] not in known), key=lambda r: r.get("created_at", "")):
            findings.append(self._new_repo(repo))
        if findings:
            self.db.set_state(KNOWN_KEY, json.dumps(sorted(known | names)))
        return findings

    def _new_repo(self, repo: dict[str, Any]) -> Finding:
        full_name, name = repo["full_name"], repo["name"]
        description = (repo.get("description") or "").strip()
        text = f"Nuovo repository {full_name}" + (f": {description}" if description else "")
        score = int(self.dcfg.get("base_score", 70))
        keywords = ["new_repo"]
        # il nome con i trattini resi spazi fa scattare anche le regole come "half-life 3"
        probe = f"{name.replace('-', ' ').replace('_', ' ')} {name} {description}"
        res = self.matcher.analyze_line(probe, file_path=full_name, change_type="added")
        if res:
            score = max(score, res.score + 20)
            keywords += res.keywords
        followed = False
        if name.startswith("GameTracking-") and self.dcfg.get("auto_follow", True):
            key = game_key(name)
            existing = {r["name"] for r in tracked_repos(self.cfg, self.db)}
            if key in existing:
                key = f"{key}{len(existing)}"
            add_followed(self.db, {
                "name": key, "title": name.split("GameTracking-", 1)[1] or name, "repo": full_name,
                "branch": repo.get("default_branch") or "master", "since": repo.get("created_at"),
                "auto": True,
            })
            followed = True
            text += " (ora seguito automaticamente)"
        build = BuildInfo(game="steamtracking", source="github", ref=f"repo:{full_name}",
                          date=parse_iso(repo.get("created_at")) or utcnow(), url=repo.get("html_url"),
                          message=text[:500])
        build_id = self.db.upsert_build(build)
        finding = Finding(game="steamtracking", source="discovery", file_path=full_name, change_type="new_repo",
                          text=text, score=min(score, 250), keywords=keywords, token=f"repo:{full_name.lower()}",
                          context=json.dumps({k: repo.get(k) for k in ("created_at", "description", "default_branch",
                                                                        "html_url")}, ensure_ascii=False))
        finding.build = build
        self.db.add_finding(finding, build_id)
        log.info("NUOVO repository %s (punteggio %d)%s", full_name, finding.score, " - seguito" if followed else "")
        return finding
