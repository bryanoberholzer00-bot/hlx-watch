"""Sorgente opzionale: file locali dei giochi installati, tramite Source2Viewer-CLI (ValveResourceFormat).

- lista dei file in ogni VPK (nuovi percorsi tipo maps/hlx_*)
- estrazione dei file di testo (vdf, txt, fgd, kv3...) e confronto con lo snapshot precedente
- stringhe ASCII/UTF-16 estratte dai binari (engine2.dll, client.dll, server.dll...)
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hlxwatch.analyzers import KeywordMatcher, analyze_line_sets
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

log = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_strings(path: Path, min_len: int = 6) -> list[str]:
    """Stringhe ASCII stampabili e UTF-16LE di lunghezza >= min_len, come il comando `strings`."""
    data = path.read_bytes()
    ascii_re = re.compile(rb"[\x20-\x7e]{%d,}" % min_len)
    utf16_re = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len)
    out = [m.group().decode("ascii") for m in ascii_re.finditer(data)]
    out += [m.group().decode("utf-16le", "ignore") for m in utf16_re.finditer(data)]
    return out


class LocalFilesSource:
    name = "local"

    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.context_lines = int(cfg.get("general.context_lines", 2))
        self.max_findings = int(cfg.get("general.max_findings_per_file", 400))
        self.snap_root = cfg.data_dir / "local_snapshots"
        self.extract_root = cfg.data_dir / "local_extract"

    # ------------------------------------------------------------------ run
    def run(self) -> list[Finding]:
        if not self.cfg.get("local.enabled", False):
            return []
        cli = Path(str(self.cfg.get("local.source2viewer_cli", "")))
        findings: list[Finding] = []
        for game in self.cfg.get("local.games", []) or []:
            name = game["name"]
            matcher = KeywordMatcher(self.cfg.keywords, ignore_ids=game.get("ignore_keywords", []) or [])
            for vpk in game.get("vpks", []) or []:
                try:
                    findings += self._scan_vpk(name, Path(vpk), cli, matcher)
                except Exception:
                    log.exception("[%s] errore sul VPK %s", name, vpk)
            for binary in game.get("binaries", []) or []:
                try:
                    findings += self._scan_binary(name, Path(binary), matcher)
                except Exception:
                    log.exception("[%s] errore sul binario %s", name, binary)
        return findings

    # -------------------------------------------------------------- helper
    def _persist(self, game: str, ref: str, message: str, findings: list[Finding], baseline: bool) -> list[Finding]:
        if not findings:
            return []
        build = BuildInfo(game=game, source="local", ref=ref, date=utcnow(), message=message)
        build_id = self.db.upsert_build(build)
        for finding in findings:
            if baseline:
                finding.change_type = "baseline"
            finding.build = build
            self.db.add_finding(finding, build_id)
        return findings

    def _run_cli(self, cli: Path, args: list[str], timeout: int = 3600) -> str:
        proc = subprocess.run([str(cli), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"Source2Viewer-CLI exit {proc.returncode}: {proc.stderr[-500:] or proc.stdout[-500:]}")
        return proc.stdout

    def _path_findings(self, game: str, paths: list[str], change_type: str, matcher: KeywordMatcher) -> list[Finding]:
        out: list[Finding] = []
        for path in paths:
            res = matcher.analyze_line(path, file_path=path, change_type=change_type)
            if res:
                out.append(Finding(game=game, source="local", file_path=path, change_type=change_type, text=path,
                                   score=res.score, keywords=res.keywords, token=res.token))
        return out

    # ----------------------------------------------------------------- VPK
    def _scan_vpk(self, game: str, vpk: Path, cli: Path, matcher: KeywordMatcher) -> list[Finding]:
        if not vpk.exists():
            log.warning("[%s] VPK non trovato: %s", game, vpk)
            return []
        if not cli.exists():
            log.error("Source2Viewer-CLI non trovato in %s (local.source2viewer_cli)", cli)
            return []
        digest = file_hash(vpk)
        key_hash = f"local:vpkhash:{game}:{vpk}"
        previous_hash = self.db.get_snapshot(key_hash)
        if previous_hash and previous_hash[0] == digest:
            log.info("[%s] %s invariato", game, vpk.name)
            return []
        first_run = previous_hash is None
        ref = f"vpk:{vpk.stem}:{digest[:12]}"
        findings: list[Finding] = []

        # 1) elenco dei file
        listing = self._run_cli(cli, ["-i", str(vpk), "--vpk_list"])
        paths = sorted({ln.strip() for ln in listing.splitlines() if ln.strip() and not ln.startswith("-")})
        key_list = f"local:vpklist:{game}:{vpk}"
        previous = self.db.get_snapshot(key_list)
        old_paths = set((previous[1] or "").splitlines()) if previous else set()
        new_set = set(paths)
        added, removed = sorted(new_set - old_paths), sorted(old_paths - new_set)
        path_findings = self._path_findings(game, added, "file_added", matcher)
        path_findings += self._path_findings(game, removed, "file_removed", matcher)
        self.db.set_snapshot(key_list, hashlib.sha1("\n".join(paths).encode()).hexdigest(), "\n".join(paths))
        log.info("[%s] %s: %d file (+%d / -%d)", game, vpk.name, len(paths), len(added), len(removed))
        findings += self._persist(game, ref, f"VPK {vpk.name}: +{len(added)} / -{len(removed)} file",
                                  path_findings, baseline=first_run)

        # 2) file di testo estratti
        if self.cfg.get("local.extract_text_files", True):
            findings += self._scan_vpk_text(game, vpk, cli, matcher, ref, first_run)

        self.db.set_snapshot(key_hash, digest, None)
        return findings

    def _scan_vpk_text(self, game: str, vpk: Path, cli: Path, matcher: KeywordMatcher, ref: str,
                       first_run: bool) -> list[Finding]:
        exts = str(self.cfg.get("local.text_extensions", "vdf,txt,fgd,kv3,cfg,res"))
        out_dir = self.extract_root / game / vpk.stem
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._run_cli(cli, ["-i", str(vpk), "-o", str(out_dir), "-e", exts, "-d"])
        snap_dir = self.snap_root / game / vpk.stem
        findings: list[Finding] = []
        n_files = n_changed = 0
        for file in out_dir.rglob("*"):
            if not file.is_file():
                continue
            n_files += 1
            rel = file.relative_to(out_dir).as_posix()
            try:
                new_text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "\x00" in new_text[:4096]:
                continue
            prev_file = snap_dir / rel
            old_text = prev_file.read_text(encoding="utf-8", errors="replace") if prev_file.exists() else ""
            if old_text == new_text:
                continue
            n_changed += 1
            findings += analyze_line_sets(old_text.splitlines(), new_text.splitlines(), game=game, source="local",
                                          file_path=f"{vpk.name}:{rel}", matcher=matcher,
                                          context_lines=self.context_lines, max_findings=self.max_findings)
            prev_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, prev_file)
        log.info("[%s] %s: %d file di testo, %d cambiati, %d match", game, vpk.name, n_files, n_changed, len(findings))
        shutil.rmtree(out_dir, ignore_errors=True)
        return self._persist(game, ref, f"File di testo in {vpk.name}", findings, baseline=first_run)

    # ------------------------------------------------------------- binari
    def _scan_binary(self, game: str, path: Path, matcher: KeywordMatcher) -> list[Finding]:
        if not path.exists():
            log.warning("[%s] binario non trovato: %s", game, path)
            return []
        digest = file_hash(path)
        key = f"local:bin:{game}:{path}"
        previous = self.db.get_snapshot(key)
        if previous and previous[0] == digest:
            log.info("[%s] %s invariato", game, path.name)
            return []
        min_len = int(self.cfg.get("local.min_string_length", 6))
        strings = sorted(set(extract_strings(path, min_len)))
        snap_file = self.snap_root / game / "strings" / f"{path.name}.txt.gz"
        old: list[str] = []
        first_run = not snap_file.exists()
        if not first_run:
            with gzip.open(snap_file, "rt", encoding="utf-8") as fh:
                old = fh.read().splitlines()
        findings = analyze_line_sets(old, strings, game=game, source="local", file_path=f"{path.name} (strings)",
                                     matcher=matcher, context_lines=self.context_lines,
                                     max_findings=self.max_findings)
        snap_file.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(snap_file, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(strings))
        self.db.set_snapshot(key, digest, None)
        log.info("[%s] %s: %d stringhe, %d match", game, path.name, len(strings), len(findings))
        return self._persist(game, f"bin:{path.name}:{digest[:12]}", f"Stringhe in {path.name}", findings,
                             baseline=first_run)
