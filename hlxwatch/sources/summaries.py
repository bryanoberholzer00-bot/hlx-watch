"""Riassunti automatici di SteamTracking: un commento su ogni commit di un gioco, scritto da agenti Claude.

Dal 4 ottobre 2026 SteamTracking fa leggere ogni aggiornamento a una squadra di agenti (azione `commit-summary`
del repository GameTracking) e pubblica il riassunto come commento sul commit, firmato `github-actions[bot]`.
Le loro regole dicono che gli identificatori di altri giochi o di progetti non annunciati sono sempre una
scoperta e vanno in una sezione a parte. Qui:
  - si leggono i commenti nuovi di ogni repository seguito (una o due chiamate API per repository);
  - di ogni riassunto si salvano solo titolo e link (stato `summary:<gioco>:<sha>`), mostrati sul sito accanto alla build;
  - una riga che le parole chiave valutano almeno `min_score` (HLX, Half-Life 3...) o che sta in una sezione su
    altri giochi o progetti non annunciati diventa una voce "summary", con avviso. Il primo giro registra e basta.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import parse_iso, utcnow

log = logging.getLogger(__name__)

PER_PAGE = 100
HEADING = re.compile(r"^#{1,4}\s+(?P<title>.+?)\s*$")
# titoli delle sezioni su altri giochi o progetti (le regole di SteamTracking chiedono una sezione a parte)
OTHER_PROJECTS = re.compile(r"\bunannounced\b|\bother (valve )?(games?|projects?)\b|\banother (game|project)\b"
                            r"|not used by this game|\bunreleased\b|non annunciat", re.IGNORECASE)   # "Other gameplay" no
BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


def summary_title(body: str) -> str:
    """La prima riga di testo del riassunto: è il sommario in una frase."""
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:300]
    return ""


def flagged_lines(body: str, matcher: KeywordMatcher, min_score: int) -> list[tuple[str, list[str], int]]:
    """(riga, parole chiave, punteggio) per le righe su HLX o nella sezione degli altri progetti."""
    out: list[tuple[str, list[str], int]] = []
    in_other = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = HEADING.match(line)
        if m:
            in_other = bool(OTHER_PROJECTS.search(m.group("title")))
            continue
        text = BULLET.sub("", line)
        res = matcher.analyze_line(text, file_path="", change_type="added")
        if res and res.score >= min_score:
            out.append((text, res.keywords, res.score))
        elif in_other:
            out.append((text, [], 0))
    return out


class CommitSummaries:
    name = "summaries"

    def __init__(self, cfg: Config, db: Database, client: Any):
        self.cfg = cfg
        self.db = db
        self.client = client
        scfg: dict[str, Any] = cfg.get("summaries", {}) or {}
        self.enabled = bool(scfg.get("enabled", True))
        self.author = str(scfg.get("author", "github-actions[bot]"))
        self.score = int(scfg.get("score", 110))
        self.min_score = int(scfg.get("min_keyword_score", 90))
        self.max_lines = int(scfg.get("max_lines", 10))
        self.matcher = KeywordMatcher(cfg.keywords)

    def run(self) -> list[Finding]:
        if not self.enabled:
            return []
        findings: list[Finding] = []
        for repo_cfg in tracked_repos(self.cfg, self.db):
            if repo_cfg["repo"].split("/")[-1].startswith("GameTracking-"):
                findings += self.check(repo_cfg)
        return findings

    def fetch(self, repo: str, seen_count: int) -> list[dict[str, Any]]:
        """I commenti del repository dalla pagina in cui sono finiti quelli già visti (GitHub li ordina per id)."""
        page = max(1, seen_count // PER_PAGE)       # una pagina prima: qualche commento può essere stato cancellato
        out: list[dict[str, Any]] = []
        while True:
            data = self.client.get_json(f"/repos/{repo}/comments", {"per_page": PER_PAGE, "page": page})
            if not data:
                break
            out += data
            if len(data) < PER_PAGE:
                break
            page += 1
        return out

    def check(self, repo_cfg: dict[str, Any]) -> list[Finding]:
        name, repo = repo_cfg["name"], repo_cfg["repo"]
        key_last, key_count = f"summaries:{name}:last_id", f"summaries:{name}:count"
        last_raw = self.db.get_state(key_last)
        first_run = last_raw is None
        last_id = int(last_raw or 0)
        comments = self.fetch(repo, int(self.db.get_state(key_count) or 0))
        new = sorted((c for c in comments if int(c.get("id", 0)) > last_id
                      and (c.get("user") or {}).get("login") == self.author), key=lambda c: int(c["id"]))
        findings: list[Finding] = []
        for c in new:
            sha, body = str(c.get("commit_id") or ""), str(c.get("body") or "")
            if not sha or not body.strip():
                continue
            self.db.set_state(f"summary:{name}:{sha}", json.dumps(
                {"title": summary_title(body), "url": c.get("html_url"), "created_at": c.get("created_at")},
                ensure_ascii=False))
            if first_run:
                continue
            findings += self._findings(repo_cfg, c, body)
        if comments:
            self.db.set_state(key_count, max(len(comments), int(self.db.get_state(key_count) or 0)))
        if new or first_run:
            self.db.set_state(key_last, max([last_id, *(int(c["id"]) for c in new)]))
        if first_run:
            log.info("[%s] riassunti di SteamTracking: %d registrati, senza avvisi", name, len(new))
        elif new:
            log.info("[%s] riassunti di SteamTracking: %d nuovi, %d righe segnalate", name, len(new), len(findings))
        return findings

    def _findings(self, repo_cfg: dict[str, Any], comment: dict[str, Any], body: str) -> list[Finding]:
        lines = flagged_lines(body, self.matcher, self.min_score)[: self.max_lines]
        if not lines:
            return []
        name, sha = repo_cfg["name"], str(comment["commit_id"])
        build = BuildInfo(game=name, source="github", ref=f"summary:{sha}",
                          date=parse_iso(comment.get("created_at")) or utcnow(), url=comment.get("html_url"),
                          message=f"riassunto di SteamTracking: {summary_title(body)[:200]}")
        build_id = self.db.upsert_build(build)
        out: list[Finding] = []
        for text, keywords, score in lines:
            f = Finding(game=name, source="github", file_path=f"commento di SteamTracking su {sha[:10]}",
                        change_type="summary", text=text[:500], score=max(self.score, score),
                        keywords=["steamtracking_summary", *keywords],
                        context="riga del riassunto automatico di SteamTracking"
                                + ("" if keywords else ", sezione su altri giochi o progetti non annunciati"))
            f.build = build
            self.db.add_finding(f, build_id)
            log.warning("[%s] RIASSUNTO DI STEAMTRACKING: %s", name, text[:160])
            out.append(f)
        return out
