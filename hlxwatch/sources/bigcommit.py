"""Commit con più file di quanti l'API di GitHub ne mostri: il resto si legge con git.

L'API dei commit espone al massimo 3000 file (10 pagine da 300). SteamTracking a volte riformatta i dump
e cambia 5.000-8.000 file in un commit solo: senza questo modulo i file oltre il 3000° non venivano letti
(fra settembre e ottobre 2026 cinque commit, circa 20.000 file).

Il commit e il suo genitore si scaricano senza contenuti (`--depth 2 --filter=blob:none`, solo gli alberi),
poi `git diff` sui soli file mancanti scarica i contenuti che servono in un colpo solo. Ogni file diventa una
voce come quelle dell'API (`filename`, `status`, `patch`), così passa dagli stessi controlli.
La cartella di lavoro si cancella a fine commit: il runner non accumula gigabyte.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

API_FILE_CAP = 3000                       # file per commit esposti dall'API di GitHub
STATUS = {"A": "added", "D": "removed", "M": "modified", "T": "modified"}


def _strip_prefix(raw: str) -> str:
    """'a/percorso' o '"a/perc\\303\\262rso"' (git mette tra virgolette i percorsi con caratteri speciali)."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape") \
            .encode("latin-1", "replace").decode("utf-8", "replace")
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def _remove(path: Path) -> None:
    """Cancella la cartella di lavoro anche su Windows, dove git lascia gli oggetti in sola lettura."""
    def retry(func, target, *_):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _header_path(line: str) -> str:
    """'diff --git a/X b/X' -> X (senza rinomine i due percorsi sono uguali). Serve per i file binari,
    che non hanno le righe ---/+++; per i percorsi tra virgolette valgono poi quelle righe."""
    rest = line[len("diff --git a/"):]
    if rest.startswith('"') or (len(rest) - 3) % 2:
        return ""
    half = (len(rest) - 3) // 2
    return rest[:half] if rest[half:half + 3] == " b/" else ""


def _batches(paths: list[str], max_chars: int = 6000) -> Iterator[list[str]]:
    """Percorsi a blocchi per la riga di comando (Windows accetta circa 32.000 caratteri in tutto)."""
    batch: list[str] = []
    size = 0
    for p in paths:
        if batch and size + len(p) + 12 > max_chars:
            yield batch
            batch, size = [], 0
        batch.append(p)
        size += len(p) + 12
    if batch:
        yield batch


class LocalDiff:
    def __init__(self, root: Path, *, timeout: int = 1800, max_patch_bytes: int = 25 * 1024 * 1024):
        self.root = Path(root)
        self.timeout = timeout
        self.max_patch_bytes = max_patch_bytes
        self.files_total = 0              # file cambiati davvero nell'ultimo commit letto

    def _git(self, gitdir: Path, *args: str) -> bytes:
        proc = subprocess.run(["git", "-C", str(gitdir), "-c", "core.quotePath=false", *args],
                              capture_output=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} fallito: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
        return proc.stdout

    def entries(self, repo: str, sha: str, parent: str, skip: set[str], *,
                clone_url: str | None = None) -> Iterator[dict]:
        """Le voci (come quelle dell'API) dei file cambiati nel commit che non sono in `skip`."""
        self.root.mkdir(parents=True, exist_ok=True)
        gitdir = Path(tempfile.mkdtemp(prefix="commit-", dir=self.root))
        try:
            subprocess.run(["git", "init", "-q", "--bare", str(gitdir)], check=True, capture_output=True)
            self._git(gitdir, "fetch", "-q", "--depth", "2", "--filter=blob:none",
                      clone_url or f"https://github.com/{repo}.git", sha)
            parts = self._git(gitdir, "diff", "--name-status", "--no-renames", "-z", parent, sha) \
                .decode("utf-8", "replace").split("\0")
            statuses = {parts[i + 1]: STATUS.get(parts[i][:1], "modified")
                        for i in range(0, len(parts) - 1, 2) if parts[i]}
            self.files_total = len(statuses)
            missing = [p for p in statuses if p not in skip]
            log.info("%s %s: %d file cambiati, %d non esposti dall'API: li leggo con git", repo, sha[:10],
                     len(statuses), len(missing))
            if not missing:
                return
            for batch in _batches(missing):
                yield from self._diff(gitdir, parent, sha, batch, statuses)
        finally:
            _remove(gitdir)

    def _diff(self, gitdir: Path, parent: str, sha: str, paths: list[str],
              statuses: dict[str, str]) -> Iterator[dict]:
        """`git diff` di un blocco di file, letto riga per riga: una voce per file."""
        proc = subprocess.Popen(
            ["git", "-C", str(gitdir), "-c", "core.quotePath=false", "diff", "--unified=3", "--no-color",
             "--no-renames", "--no-ext-diff", parent, sha, "--", *(f":(literal){p}" for p in paths)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None
        path: str | None = None
        binary, too_big, hunks, size = False, False, [], 0
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("diff --git "):
                if path is not None:
                    yield self._entry(path, statuses, binary, too_big, hunks)
                path, binary, too_big, hunks, size = _header_path(line), False, False, [], 0
                continue
            if path is None:
                continue
            if not hunks:
                if line.startswith(("--- ", "+++ ")):
                    if line[4:] != "/dev/null":
                        path = _strip_prefix(line[4:])
                    continue
                if line.startswith("Binary files "):
                    binary = True
                    continue
                if not line.startswith("@@"):
                    continue                          # intestazioni: index, new file mode...
            if size > self.max_patch_bytes:
                too_big = True
                continue
            hunks.append(line)
            size += len(line) + 1
        if path is not None:
            yield self._entry(path, statuses, binary, too_big, hunks)
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait(timeout=self.timeout) != 0:
            raise RuntimeError(f"git diff fallito: {err[-300:]}")

    def _entry(self, path: str, statuses: dict[str, str], binary: bool, too_big: bool, hunks: list[str]) -> dict:
        if too_big:
            log.info("%s: patch oltre %d byte, saltata", path, self.max_patch_bytes)
        return {"filename": path, "status": statuses.get(path, "modified"), "local": True,
                "patch": None if binary or too_big or not hunks else "\n".join(hunks)}
