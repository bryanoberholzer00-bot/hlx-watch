"""Archeologia: cerca in TUTTA la storia dei repository SteamTracking (dal 2016) le righe HLX aggiunte o tolte.

La scansione normale legge i commit degli ultimi mesi e il contenuto attuale dei file: una riga comparsa nel
2019 e cancellata nel 2021 le sfugge. Qui si clona il repository (bare, una volta, poi si cancella) e
`git log -G` elenca solo i commit le cui modifiche contengono le parole chiave principali. Ogni riga aggiunta
o tolta passa dallo stesso KeywordMatcher della scansione normale, quindi punteggi e filtri anti-rumore sono
gli stessi. I commit già nell'archivio si saltano.

Le voci storiche prendono la data del loro commit (non quella di oggi): non compaiono tra le ultime
scoperte, nei report del giorno, nelle notifiche o nel riepilogo settimanale, ma nella ricerca per data e
nella Cronologia HLX. Nessuna notifica viene inviata.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import parse_iso

log = logging.getLogger(__name__)

# solo le parole chiave forti: git cerca questa espressione in ogni modifica della storia
PICKAXE = r"hlx|hl3|half.?life.?(3|three|iii)|l4d3|left.?4.?dead.?3"
QUICK = re.compile(PICKAXE, re.IGNORECASE)


def git_log_lines(git_dir: Path, branch: str) -> Iterator[str]:
    cmd = ["git", "-C", str(git_dir), "log", "--reverse", "-i", "-E", "-G", PICKAXE,
           "--format=@@@%H|%cI|%s", "-p", "--unified=0", "--no-color", "--no-renames", "--no-ext-diff", branch]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    yield from proc.stdout
    proc.wait()
    if proc.returncode:
        raise RuntimeError(f"git log fallito: {proc.stderr.read()[-500:] if proc.stderr else ''}")


def parse_history(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Da `git log -p` a commit con le righe aggiunte/tolte che contengono le parole chiave."""
    commit: dict[str, Any] | None = None
    path = ""
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("@@@"):
            if commit and commit["changes"]:
                yield commit
            sha, date, subject = (line[3:].split("|", 2) + ["", ""])[:3]
            commit = {"sha": sha, "date": date, "subject": subject, "changes": []}
            path = ""
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else path
        elif line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else path
        elif commit is not None and line[:1] in "+-" and not line.startswith(("+++", "---")) and QUICK.search(line):
            commit["changes"].append({"path": path, "sign": line[0], "text": line[1:][:2000]})
    if commit and commit["changes"]:
        yield commit


def gather_repo(cfg: Config, db: Database, repo_cfg: dict[str, Any], git_dir: Path,
                stats: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Clona (se serve) e legge la storia di un repository: (repo_cfg, commit) per ogni commit con righe HLX."""
    name, repo = repo_cfg["name"], repo_cfg["repo"]
    if not git_dir.exists():
        log.info("[%s] archeologia: scarico tutta la storia di %s", name, repo)
        subprocess.run(["git", "clone", "--quiet", "--bare", f"https://github.com/{repo}.git", str(git_dir)],
                       check=True)
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for commit in parse_history(git_log_lines(git_dir, repo_cfg.get("branch", "master"))):
        if db.build_exists(name, "github", commit["sha"]):
            stats["commit_gia_noti"] += 1
            continue
        out.append((repo_cfg, commit))
    return out


def insert_commits(cfg: Config, db: Database, items: list[tuple[dict[str, Any], dict[str, Any]]],
                   stats: dict[str, dict[str, Any]], ignored: Any = None) -> None:
    """Salva i commit in ordine di data, qualunque sia il repository: così la prima comparsa di una stringa
    (seen_strings, "mai vista") è davvero la più vecchia, non la prima incontrata nell'ordine dei repository."""
    matchers: dict[str, KeywordMatcher] = {}
    for repo_cfg, commit in sorted(items, key=lambda it: it[1]["date"]):
        name, repo = repo_cfg["name"], repo_cfg["repo"]
        matcher = matchers.get(name)
        if matcher is None:
            matcher = matchers[name] = KeywordMatcher(cfg.keywords, ignore_ids=repo_cfg.get("ignore_keywords", []) or [])
        date = parse_iso(commit["date"]) or datetime.fromisoformat(commit["date"])
        findings: list[Finding] = []
        for ch in commit["changes"]:
            if not ch["path"] or (ignored and ignored(ch["path"])):
                continue
            change_type = "added" if ch["sign"] == "+" else "removed"
            res = matcher.analyze_line(ch["text"], file_path=ch["path"], change_type=change_type)
            if res:
                findings.append(Finding(
                    game=name, source="github", file_path=ch["path"], change_type=change_type,
                    text=ch["text"].strip()[:1000], score=res.score, keywords=res.keywords, token=res.token,
                    context="archeologia: dalla storia completa del repository",
                ))
        if not findings:
            continue
        build = BuildInfo(game=name, source="github", ref=commit["sha"], date=date,
                          url=f"https://github.com/{repo}/commit/{commit['sha']}",
                          message=f"[archeologia] {commit['subject']}"[:500], files_changed=None)
        build_id = db.upsert_build(build)
        st = stats[name]
        for f in findings:
            db.add_finding(f, build_id, created_at=date)
            st["nuove"] += int(f.is_new)
        st["voci"] += len(findings)
        st["commit_con_righe"] += 1


def run_archaeology(cfg: Config, db: Database, *, names: list[str] | None = None,
                    workdir: str | None = None, keep: bool = False) -> dict[str, Any]:
    from hlxwatch.sources.github_tracking import GitHubTrackingSource
    helper = GitHubTrackingSource(cfg, db)            # stessi file ignorati della scansione normale
    base = Path(workdir or tempfile.mkdtemp(prefix="hlx-archeologia-"))
    base.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    # oltre ai repository seguiti, i giochi Source 2 dismessi (archaeology.extra_repos): storia utile, niente controlli
    extra = [dict(r) for r in (cfg.get("archaeology.extra_repos", []) or []) if r.get("name") and r.get("repo")]
    for repo_cfg in [*tracked_repos(cfg, db), *extra]:
        name = repo_cfg["name"]
        if names and name not in names:
            continue
        started = time.time()
        stats = summary[name] = {"commit_con_righe": 0, "commit_gia_noti": 0, "voci": 0, "nuove": 0, "errore": ""}
        git_dir = base / f"{name}.git"
        try:
            items += gather_repo(cfg, db, repo_cfg, git_dir, stats)
        except Exception as exc:
            # un clone fallito non deve fermare gli altri repository: si continua e si riporta l'errore
            log.exception("[%s] archeologia fallita, continuo con gli altri repository", name)
            stats["errore"] = f"{type(exc).__name__}: {exc}"[:300]
        if not keep:
            shutil.rmtree(git_dir, ignore_errors=True)
        stats["secondi"] = round(time.time() - started)
    insert_commits(cfg, db, items, summary, ignored=helper._ignored)
    for name, stats in summary.items():
        log.info("[%s] archeologia: %d commit con voci nuove nell'archivio (%d voci, %d stringhe mai viste), "
                 "%d commit già noti, %ds%s", name, stats["commit_con_righe"], stats["voci"], stats["nuove"],
                 stats["commit_gia_noti"], stats["secondi"], f" | ERRORE: {stats['errore']}" if stats["errore"] else "")
    if not keep and not workdir:
        shutil.rmtree(base, ignore_errors=True)
    return summary
