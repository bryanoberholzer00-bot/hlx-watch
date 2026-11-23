"""Nomi in codice dell'hardware Valve nel codice di SDL e del kernel Linux.

Perché un controller o un visore funzioni su PC, il suo identificativo USB va aggiunto alla libreria SDL e al kernel
Linux, spesso con il nome in codice: a fine ottobre 2026 ci sono già Ibex, Proteus e Nereid
(USB_PRODUCT_VALVE_STEAM_PROTEUS_DONGLE, USB_DEVICE_ID_STEAM_CONTROLLER_NEREID). Ogni `interval_hours` si rilegge il
file con l'API di GitHub (con il token del workflow: il download diretto da raw.githubusercontent.com, per un
repository grande come Linux, su Actions risponde spesso 429) e ogni nome mai visto diventa una voce. Il primo giro
registra.

Un nome nuovo nel file non è per forza un nome in codice nuovo: l'11/11/2026 SDL ha aggiunto STEAM_TRITON a usb_ids.h,
ma Triton (il nuovo Steam Controller) aveva già un suo driver in SDL da mesi. Per ogni nome nuovo si cerca quindi il
nome in codice (TRITON, NEREID...: senza parole generiche come STEAM, CONTROLLER, DONGLE) negli altri nomi già letti,
nell'archivio e, con la ricerca di GitHub, negli altri file dello stesso repository. Se è già noto la voce lo dice e
vale `score_known` invece di `score_new`. I nomi con parole HLX restano sempre al punteggio pieno.
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
# parole che non sono un nome in codice: restano i nomi come TRITON, PROTEUS, NEREID, IBEX, GALILEO
GENERIC = {"STEAM", "VALVE", "CONTROLLER", "DONGLE", "RECEIVER", "BLE", "BT", "USB", "WIRED", "WIRELESS", "DECK",
           "HID", "DEVICE", "ID", "GAMEPAD", "HEADSET", "LEGACY", "PRO", "MINI", "V2", "V3", "REV"}
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
        self.score_known = int(self.hcfg.get("score_known", 35))
        self.search_code = bool(self.hcfg.get("search_code", True))
        self.matcher = KeywordMatcher(cfg.keywords)
        self.errors: list[str] = []
        self.new_names: set[str] = set()          # nomi nuovi della lettura in corso: non sono prove "già noto"

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
        self.new_names = {name for name in ids if name not in old}
        try:
            return [self._finding(spec, name, value) for name, value in sorted(ids.items()) if name not in old]
        finally:
            self.new_names = set()

    # ------------------------------------------------------------------ nome già noto?
    @staticmethod
    def codenames(name: str) -> list[str]:
        """STEAM_TRITON_BLE -> ["triton"], CONTROLLER_NEREID -> ["nereid"]: le parole che non sono generiche."""
        return [w.lower() for w in name.upper().split("_")
                if len(w) >= 3 and w not in GENERIC and not w.isdigit() and not re.fullmatch(r"V\d+", w)]

    # voci dell'archivio che possono far da prova: hardware, nomi in codice, SteamOS, FCC. Le righe dei giochi no:
    # "atlas" o "frame" ci sono in mille file che non c'entrano con l'hardware ("texture atlas", "frame time")
    HW_EVIDENCE = ("hardware", "codename", "valve_device", "controller_type", "steamos", "fcc")
    # i file dei giochi: lì una parola comune non prova niente (le voci con le parole qui sopra contano lo stesso)
    GAME_FILES = ("cs2", "dota2", "deadlock", "alyx", "tf2", "underlords", "artifact", "artifact_beta", "dota2_test")

    def known(self, spec: dict[str, Any], name: str) -> str:
        """Dove il nome in codice si vedeva già (testo per la voce), oppure "" se è davvero nuovo. Già noto solo se lo
        sono TUTTE le sue parole: in STEAM_FRAME_ORION "frame" (lo Steam Frame) non deve nascondere "orion"."""
        words = self.codenames(name)
        if not words:
            return ""
        reasons = []
        for word in words:
            seen = self._word_known(spec, name, word)
            if not seen:
                return ""
            reasons.append(seen if len(words) == 1 else f"«{word}» {seen}")
        return "; ".join(reasons)

    def _word_known(self, spec: dict[str, Any], name: str, word: str) -> str:
        rx = re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", re.I)
        # 0. tra i nomi dell'hardware Valve già noti nel config (Deckard, Fremont, Ibex...)
        res = self.matcher.analyze_line(word, file_path="hardware", change_type="added")
        if res and "valve_hardware" in res.keywords:
            return "è tra i nomi dell'hardware Valve già noti"
        # 1. negli altri nomi già letti (lo stesso file o l'altro repository), non in quelli arrivati adesso
        for other in self.files:
            raw = self.db.get_state(f"hwids:{other['repo']}:{other['path']}")
            for old in sorted(json.loads(raw) if raw else {}):
                if old != name and old not in self.new_names and rx.search(old):
                    return f"compare in {other.get('name') or other['repo']} come {old}"
        # 2. nell'archivio: client Steam, SteamVR, SteamOS, codice di Valve, FCC; dei giochi solo le voci di hardware
        like = f"%{word}%"
        evidence = " OR ".join("f.keywords LIKE ?" for _ in self.HW_EVIDENCE)
        games = ",".join("?" * len(self.GAME_FILES))
        for r in self.db.conn.execute(
                "SELECT f.game, f.text, f.token, COALESCE(b.date, f.created_at) AS d FROM findings f "
                "LEFT JOIN builds b ON b.id = f.build_id WHERE f.game != ? AND (lower(f.text) LIKE ? "
                f"OR lower(COALESCE(f.token, '')) LIKE ?) AND (f.game NOT IN ({games}) OR {evidence}) "
                "ORDER BY d LIMIT 200",
                (GAME, like, like, *self.GAME_FILES, *(f"%{k}%" for k in self.HW_EVIDENCE))).fetchall():
            if rx.search(f"{r[1] or ''} {r[2] or ''}"):
                return f"compare nell'archivio dal {str(r[3])[:10]} ({self.cfg.game_title(r[0])})"
        # 3. un file del repository con il nome nel NOME del file (SDL_hidapi_steam_triton.c): un file qualunque che
        #    contiene la parola non basta ("frame" c'è in mezzo SDL)
        if self.search_code:
            try:
                data = self.client.get_json("/search/code", {"q": f"{word} repo:{spec['repo']}", "per_page": 30})
                paths = sorted(i.get("path", "") for i in (data or {}).get("items") or []
                               if word in i.get("path", "").rsplit("/", 1)[-1].lower() and i.get("path") != spec["path"])
            except Exception as exc:
                log.info("[%s] ricerca di %s su GitHub non riuscita: %s", spec.get("name"), word, exc)
                paths = []
            if paths:
                more = f" e altri {len(paths) - 1} file" if len(paths) > 1 else ""
                return f"compare in {spec['repo']}: {paths[0]}{more}"
        return ""

    def rescore_existing(self) -> int:
        """Le voci registrate prima del controllo "già noto" (11/11/2026: STEAM_TRITON e STEAM_TRITON_BLE a 60):
        si riapplica il controllo senza la ricerca su GitHub (niente rete durante la rivalutazione)."""
        search, self.search_code = self.search_code, False
        changed = 0
        try:
            for r in self.db.conn.execute("SELECT id, file_path, text, context FROM findings WHERE change_type = 'hardware_id' "
                                          "AND keywords = '[\"hardware_id\"]' AND text NOT LIKE '%nome in codice%'").fetchall():
                try:
                    name = json.loads(r["context"] or "{}").get("nome") or ""
                except ValueError:
                    continue
                spec = next((s for s in self.files if f"{s['repo']}/{s['path']}" == r["file_path"]), None)
                if not name or spec is None or not self.codenames(name):
                    continue
                seen = self.known(spec, name)
                if seen:
                    self.db.conn.execute("UPDATE findings SET score = ?, text = ? WHERE id = ?",
                                         (self.score_known, f"{r['text']}: nome in codice già noto, {seen}", r["id"]))
                    changed += 1
        finally:
            self.search_code = search
        return changed

    def _finding(self, spec: dict[str, Any], name: str, value: str) -> Finding:
        label = spec.get("name") or spec["repo"]
        text = f"Hardware Valve: nome nuovo in {label}: {name} ({value})"
        score, keywords = self.score, ["hardware_id"]
        res = self.matcher.analyze_line(f"{name} {name.replace('_', ' ')}", file_path=spec["path"], change_type="added")
        seen = ""
        if res:
            keywords += res.keywords
        if res and res.score >= 100:
            score = max(score, res.score)                    # parole HLX: sempre al punteggio pieno, senza controlli
        elif self.codenames(name):
            seen = self.known(spec, name)                    # tutte le parole già note (config, nomi letti, archivio)
            if seen:
                score = max(self.score_known, res.score if res else 0)
                text += f": nome in codice già noto, {seen}"
            else:
                score = max(score, res.score if res else 0)
                text += ": nome in codice mai visto prima"
        elif res:
            score = max(score, res.score)
        url = f"https://github.com/{spec['repo']}/blob/{spec.get('branch', 'main')}/{spec['path']}"
        build = BuildInfo(game=GAME, source="hardware_ids", ref=f"hwid:{label}:{name}", date=utcnow(), url=url,
                          message=text[:500])
        finding = Finding(game=GAME, source="hardware_ids", file_path=f"{spec['repo']}/{spec['path']}",
                          change_type="hardware_id", text=text, score=min(score, 250), keywords=keywords,
                          token=f"hwid:{name.lower()}",
                          context=json.dumps({"nome": name, "id_usb": value, "file": url, "gia_noto": seen},
                                             ensure_ascii=False))
        finding.build = build
        if announcement.enabled(self.cfg) and announcement.looks_like_hlx(name):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("[%s] %s", label, text)
        return finding
