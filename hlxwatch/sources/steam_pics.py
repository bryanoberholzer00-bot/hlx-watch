"""Sorgente Steam PICS (login anonimo): build id, branch, depot e metadati delle app Valve osservate."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from hlxwatch.analyzers import KeywordMatcher, analyze_line_sets
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)


def flatten_kv(node: Any, prefix: str = "") -> list[str]:
    """Appiattisce il dizionario PICS in righe 'a/b/c = valore', ordinate: facili da confrontare e cercare."""
    lines: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            lines += flatten_kv(value, f"{prefix}/{key}" if prefix else str(key))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            lines += flatten_kv(value, f"{prefix}[{idx}]")
    else:
        lines.append(f"{prefix} = {node}")
    return sorted(lines) if not prefix else lines


def content_hash(lines: list[str]) -> str:
    h = hashlib.sha1()
    for line in lines:
        h.update(line.encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()


class SteamPicsSource:
    name = "steam"

    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.timeout = int(cfg.get("steam.worker_timeout_seconds", 240))
        self.matcher = KeywordMatcher(cfg.keywords)
        self.matchers = {
            str(app.get("name")): KeywordMatcher(cfg.keywords, ignore_ids=app.get("ignore_keywords", []) or [])
            for app in cfg.steam_watch_apps
        }
        self.context_lines = int(cfg.get("general.context_lines", 2))
        self.max_findings = int(cfg.get("general.max_findings_per_file", 400))

    # --------------------------------------------------------------- worker
    def call_worker(self, apps: list[int], since: int = -1) -> dict[str, Any]:
        cmd = [sys.executable, "-m", "hlxwatch.sources.steam_pics_worker",
               "--apps", ",".join(str(a) for a in apps), "--since", str(since),
               "--timeout", str(max(30, min(self.timeout // 2, 120)))]
        try:
            proc = subprocess.run(cmd, cwd=str(PACKAGE_ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"worker PICS non ha risposto entro {self.timeout}s"}
        except OSError as exc:
            return {"ok": False, "error": f"impossibile avviare il worker PICS: {exc}"}
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        try:
            data = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            data = {}
        if not data:
            return {"ok": False, "error": f"output del worker non valido (exit {proc.returncode}): {proc.stderr[-600:]}"}
        if not data.get("ok") and not data.get("error"):
            data["error"] = proc.stderr[-600:] or f"exit {proc.returncode}"
        if data.get("trace"):
            log.debug("Traceback worker PICS:\n%s", data["trace"])
        return data

    # ------------------------------------------------------------------ run
    def run(self) -> tuple[list[Finding], list[int]]:
        """Ritorna (finding, appid cambiati dall'ultimo change number)."""
        if not self.cfg.get("steam.enabled", True):
            return [], []
        watch = self.cfg.steam_watch_apps
        appids = [int(a["appid"]) for a in watch]
        since = int(self.db.get_state("steam:changenumber") or 0)
        log.info("Steam PICS: login anonimo, info per %d app, modifiche dal change %s", len(appids), since or "n/d")
        data = self.call_worker(appids, since=since)
        self.last_error = None
        if not data.get("ok"):
            log.error("Sorgente Steam PICS non disponibile: %s", data.get("error"))
            self.last_error = str(data.get("error") or "errore sconosciuto")
            return [], []

        findings: list[Finding] = []
        for app in watch:
            info = data.get("apps", {}).get(str(app["appid"]))
            if not info:
                log.warning("PICS: nessuna informazione per app %s (%s)", app["appid"], app.get("name"))
                continue
            try:
                findings += self._process_app(app, info)
            except Exception:
                log.exception("PICS: errore sull'app %s", app.get("name"))

        changed: list[int] = []
        changes = data.get("changes")
        if changes:
            self.db.set_state("steam:changenumber", changes["current"])
            if changes.get("force_full") or since == 0:
                log.info("PICS: change number di partenza %s (prima esecuzione o troppo vecchio)", changes["current"])
            else:
                changed = [int(c["appid"]) for c in changes.get("apps", [])]
                log.info("PICS: %d app modificate tra il change %s e %s", len(changed), changes["since"], changes["current"])
        return findings, changed

    # ---------------------------------------------------------------- app
    def _process_app(self, app: dict[str, Any], info: dict[str, Any]) -> list[Finding]:
        appid, name = int(app["appid"]), app["name"]
        title = app.get("title", name)
        matcher = self.matchers.get(name, self.matcher)
        key = f"steam_app:{appid}"
        previous = self.db.get_snapshot(key)
        first_run = previous is None
        findings: list[Finding] = []
        if info.get("_missing_token"):
            log.warning("[%s] PICS in anonimo mostra solo info parziali (richiede token): depot e build non visibili", name)

        # 1) build id per branch (al primo avvio sono baseline: registrate ma senza allarme)
        branches = ((info.get("depots") or {}).get("branches") or {})
        for branch, bdata in branches.items():
            if not isinstance(bdata, dict) or not bdata.get("buildid"):
                continue
            buildid = str(bdata["buildid"])
            updated = str(bdata.get("timeupdated", ""))
            date = datetime.fromtimestamp(int(updated), tz=timezone.utc) if updated.isdigit() else utcnow()
            if not self.db.upsert_steam_build(appid, name, branch, buildid, iso(date)):
                continue
            build = BuildInfo(game=name, source="steam", ref=f"{branch}:{buildid}", date=date,
                              url=f"https://store.steampowered.com/app/{appid}/",
                              message=f"Build {buildid} sul branch '{branch}'", steam_buildid=buildid)
            build_id = self.db.upsert_build(build)
            finding = Finding(
                game=name, source="steam_pics", file_path=f"depots/branches/{branch}",
                change_type="baseline" if first_run else "build",
                text=f"{title}: nuova build {buildid} sul branch '{branch}'",
                score=int(self.cfg.get("steam.build_score", 55)), keywords=["steam_build"],
                token=f"build:{appid}:{branch}:{buildid}",
                context=json.dumps(bdata, ensure_ascii=False)[:1000],
            )
            finding.build = build
            self.db.add_finding(finding, build_id)
            findings.append(finding)
            log.info("[%s] %s build Steam %s (branch %s)", name, "registrata" if first_run else "NUOVA", buildid, branch)

        # 2) snapshot completo delle info app, confrontato riga per riga
        lines = flatten_kv(info)
        digest = content_hash(lines)
        file_path = f"pics/app/{appid}"
        if previous is None:
            baseline = analyze_line_sets([], lines, game=name, source="steam_pics", file_path=file_path,
                                         matcher=matcher, context_lines=self.context_lines,
                                         max_findings=self.max_findings)
            for finding in baseline:
                finding.change_type = "baseline"
                self.db.add_finding(finding, None)
            self.db.set_snapshot(key, digest, "\n".join(lines))
            log.info("[%s] snapshot PICS iniziale: %d righe, %d match di base", name, len(lines), len(baseline))
            findings += baseline
        elif previous[0] != digest:
            old_lines = (previous[1] or "").splitlines()
            changed = analyze_line_sets(old_lines, lines, game=name, source="steam_pics", file_path=file_path,
                                        matcher=matcher, context_lines=self.context_lines,
                                        max_findings=self.max_findings)
            if changed:
                build = BuildInfo(game=name, source="steam", ref=f"pics:{info.get('_change_number', digest[:12])}",
                                  date=utcnow(), url=f"https://store.steampowered.com/app/{appid}/",
                                  message="Modifica delle informazioni app (PICS)")
                build_id = self.db.upsert_build(build)
                for finding in changed:
                    finding.build = build
                    self.db.add_finding(finding, build_id)
            self.db.set_snapshot(key, digest, "\n".join(lines))
            log.info("[%s] info PICS cambiate: %d righe interessanti", name, len(changed))
            findings += changed
        return findings
