"""Nomi in codice dell'hardware Valve nel codice di SDL e del kernel Linux.

Perché un controller o un visore funzioni su PC, il suo identificativo USB va aggiunto alla libreria SDL e al kernel
Linux, spesso con il nome in codice: a fine ottobre 2026 ci sono già Ibex, Proteus e Nereid
(USB_PRODUCT_VALVE_STEAM_PROTEUS_DONGLE, USB_DEVICE_ID_STEAM_CONTROLLER_NEREID). Ogni `interval_hours` si rilegge il
file con l'API di GitHub (con il token del workflow: il download diretto da raw.githubusercontent.com, per un
repository grande come Linux, su Actions risponde spesso 429) e ogni nome mai visto diventa una voce. Il primo giro
registra.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import timedelta
from typing import Any

from hlxwatch import announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

GAME = "valvehw"
LAST_KEY = "hwids:last_run"
DEFAULT_FILES = [
    {"name": "SDL", "repo": "libsdl-org/SDL", "branch": "main", "path": "src/joystick/usb_ids.h",
     "pattern": r"#define\s+USB_PRODUCT_VALVE_(\w+)\s+(0x[0-9a-fA-F]+)"},
    {"name": "Linux", "repo": "torvalds/linux", "branch": "master", "path": "drivers/hid/hid-ids.h",
     "pattern": r"#define\s+USB_DEVICE_ID_STEAM_(\w+)\s+(0x[0-9a-fA-F]+)"},
]


class HardwareIdsSource:
    name = "hardware_ids"

    def __init__(self, cfg: Config, db: Database, client: Any):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.hcfg: dict[str, Any] = cfg.get("hardware_ids", {}) or {}
        self.files: list[dict[str, Any]] = list(self.hcfg.get("files") or DEFAULT_FILES)
        self.interval = timedelta(hours=float(self.hcfg.get("interval_hours", 6)))
        self.score = int(self.hcfg.get("score_new", 60))
        self.matcher = KeywordMatcher(cfg.keywords)
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.hcfg.get("enabled", True))

    def due(self) -> bool:
        last = parse_iso(self.db.get_state(LAST_KEY))
        return last is None or utcnow() - last >= self.interval

    def run(self, force: bool = False) -> list[Finding]:
        self.errors = []
        if not self.enabled or (not force and not self.due()):
            return []
        findings: list[Finding] = []
        for spec in self.files:
            try:
                findings += self.check(spec)
            except Exception as exc:
                log.warning("[%s] identificativi hardware non letti: %s", spec.get("name"), exc)
                self.errors.append(f"{spec.get('name')}: {exc}")
        if not self.errors:
            self.db.set_state(LAST_KEY, iso(utcnow()))
        return findings

    def read(self, spec: dict[str, Any]) -> str:
        data = self.client.get_json(f"/repos/{spec['repo']}/contents/{spec['path']}", {"ref": spec.get("branch", "main")})
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return ""
        return base64.b64decode(data.get("content") or "").decode("utf-8", "replace")

    def check(self, spec: dict[str, Any]) -> list[Finding]:
        text = self.read(spec)
        if not text:
            raise RuntimeError(f"file {spec['path']} non letto")
        ids = {m.group(1): m.group(2).lower() for m in re.finditer(spec["pattern"], text)}
        if not ids:
            raise RuntimeError(f"nessun identificativo Valve in {spec['path']}: formato cambiato?")
        key = f"hwids:{spec['repo']}:{spec['path']}"
        raw = self.db.get_state(key)
        self.db.set_state(key, json.dumps(ids, sort_keys=True))
        if raw is None:
            log.info("[%s] %d identificativi hardware Valve registrati", spec.get("name"), len(ids))
            return []
        old = json.loads(raw)
        return [self._finding(spec, name, value) for name, value in sorted(ids.items()) if name not in old]

    def _finding(self, spec: dict[str, Any], name: str, value: str) -> Finding:
        label = spec.get("name") or spec["repo"]
        text = f"Hardware Valve: nome nuovo in {label}: {name} ({value})"
        score, keywords = self.score, ["hardware_id"]
        res = self.matcher.analyze_line(f"{name} {name.replace('_', ' ')}", file_path=spec["path"], change_type="added")
        if res:
            score = max(score, res.score)
            keywords += res.keywords
        url = f"https://github.com/{spec['repo']}/blob/{spec.get('branch', 'main')}/{spec['path']}"
        build = BuildInfo(game=GAME, source="hardware_ids", ref=f"hwid:{label}:{name}", date=utcnow(), url=url,
                          message=text[:500])
        finding = Finding(game=GAME, source="hardware_ids", file_path=f"{spec['repo']}/{spec['path']}",
                          change_type="hardware_id", text=text, score=min(score, 250), keywords=keywords,
                          token=f"hwid:{name.lower()}",
                          context=json.dumps({"nome": name, "id_usb": value, "file": url}, ensure_ascii=False))
        finding.build = build
        if announcement.enabled(self.cfg) and announcement.looks_like_hlx(name):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("[%s] %s", label, text)
        return finding
