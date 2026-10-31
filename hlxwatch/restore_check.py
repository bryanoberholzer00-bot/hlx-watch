"""Prova di ripristino: una volta a settimana si verifica che l'ultima copia dell'archivio si possa davvero riaprire.

Il workflow salva ogni settimana una copia (release "archivio", file hlxwatch-AAAA-Www.sqlite.gz). Una copia mai
provata può essere rotta senza che nessuno se ne accorga. Qui, al massimo ogni `interval_days`:
  - si scarica l'ultima copia dalla release (API di GitHub, con il token del workflow);
  - si decomprime e si apre come database: controllo di integrità di SQLite, tabelle presenti, conteggi;
  - le voci, le build e le stringhe viste devono essere almeno `min_percent` (90%) di quelle dell'archivio attuale
    (la copia ha al massimo una settimana) e non molte di più.
Il risultato resta nello stato (archive:restore_test) e compare sul sito; se fallisce arriva un avviso, e un altro
quando torna a riuscire.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

STATE = "archive:restore_test"
RELEASE_TAG = "archivio"
ASSET = re.compile(r"^hlxwatch-\d{4}-W\d{2}\.sqlite\.gz$")
TABLES = ("findings", "builds", "seen_strings", "state")
COUNTED = ("findings", "builds", "seen_strings")
LABELS = {"findings": "voci", "builds": "build e commit", "seen_strings": "stringhe viste"}
TITLE_FAIL = "HLX Watch: la copia settimanale dell'archivio NON si ripristina"
TITLE_OK = "HLX Watch: la copia settimanale dell'archivio si ripristina di nuovo"


def state(db: Database) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(STATE) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def check_file(gz_path: Path, current: dict[str, Any], min_percent: float) -> tuple[dict[str, int], list[str]]:
    """Conteggi della copia e problemi trovati (vuoto = la copia è buona)."""
    problems: list[str] = []
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "prova.sqlite"
        try:
            with gzip.open(gz_path, "rb") as fin, db_path.open("wb") as fout:
                shutil.copyfileobj(fin, fout)
        except (OSError, EOFError) as exc:
            return counts, [f"il file non si decomprime: {exc}"]
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                problems.append(f"controllo di integrità di SQLite: {result}")
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in TABLES if t not in names]
            if missing:
                problems.append("tabelle mancanti: " + ", ".join(missing))
            for key in COUNTED:
                if key in names:
                    counts[key] = int(conn.execute(f"SELECT COUNT(*) FROM {key}").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            problems.append(f"il file non è un database leggibile: {exc}")
        finally:
            conn.close()
    if not counts.get("findings"):
        problems.append("la copia non contiene nessuna voce")
    for key in COUNTED:
        now, saved = int(current.get(key) or 0), counts.get(key, 0)
        if now and saved < now * min_percent / 100:
            problems.append(f"{LABELS[key]}: {saved} nella copia contro {now} nell'archivio attuale")
        elif now and saved > now * 1.05:
            problems.append(f"{LABELS[key]}: {saved} nella copia, più delle {now} dell'archivio attuale")
    return counts, problems


class RestoreCheck:
    def __init__(self, cfg: Config, db: Database, repo: str | None = None, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        rcfg = cfg.get("restore_check", {}) or {}
        self.enabled = bool(rcfg.get("enabled", True))
        self.interval = timedelta(days=float(rcfg.get("interval_days", 7)))
        self.min_percent = float(rcfg.get("min_percent", 90))
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY") or ""
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json", "User-Agent": f"hlx-watch/{__version__}"})
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def due(self) -> bool:
        last = parse_iso((state(self.db) or {}).get("at"))
        return last is None or utcnow() - last >= self.interval

    def latest_asset(self) -> dict[str, Any] | None:
        r = self.session.get(f"https://api.github.com/repos/{self.repo}/releases/tags/{RELEASE_TAG}", timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        assets = [a for a in (r.json() or {}).get("assets") or [] if ASSET.match(str(a.get("name") or ""))]
        return max(assets, key=lambda a: a["name"]) if assets else None

    def download(self, asset: dict[str, Any], dest: Path) -> None:
        # l'API restituisce il file con Accept octet-stream (funziona anche per release private)
        with self.session.get(asset["url"], headers={"Accept": "application/octet-stream"}, timeout=300,
                              stream=True) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)

    def run(self, notifiers: list, force: bool = False) -> dict[str, Any] | None:
        if not self.enabled or not self.repo or (not force and not self.due()):
            return None
        from hlxwatch.notifiers import notify_text
        before = state(self.db)
        asset = self.latest_asset()
        if asset is None:
            log.info("Prova di ripristino: ancora nessuna copia settimanale nella release %s", RELEASE_TAG)
            return None
        with tempfile.TemporaryDirectory() as tmp:
            gz = Path(tmp) / asset["name"]
            self.download(asset, gz)
            size = gz.stat().st_size
            counts, problems = check_file(gz, self.db.stats(), self.min_percent)
        result = {"at": iso(utcnow()), "ok": not problems, "file": asset["name"], "mb": round(size / 1e6, 1),
                  "conteggi": counts, "problemi": problems}
        self.db.set_state(STATE, json.dumps(result, ensure_ascii=False))
        if problems:
            log.error("Prova di ripristino NON riuscita (%s): %s", asset["name"], "; ".join(problems))
            notify_text(notifiers, TITLE_FAIL,
                        f"Ho scaricato e riaperto l'ultima copia settimanale ({asset['name']}), ma:\n- " +
                        "\n- ".join(problems) + "\nL'archivio attuale va bene e continua a essere salvato nel ramo "
                        "data; la copia della prossima settimana verrà provata di nuovo.")
        else:
            log.info("Prova di ripristino riuscita: %s (%.1f MB, %d voci)", asset["name"], size / 1e6,
                     counts.get("findings", 0))
            if before and before.get("ok") is False:
                notify_text(notifiers, TITLE_OK, f"La copia {asset['name']} si apre ed è completa "
                                                 f"({counts.get('findings', 0)} voci).")
        return result
