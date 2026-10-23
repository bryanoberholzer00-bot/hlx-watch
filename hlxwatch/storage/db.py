from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from hlxwatch.models import BuildInfo, Finding

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS builds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game          TEXT NOT NULL,
    source        TEXT NOT NULL,
    ref           TEXT NOT NULL,
    url           TEXT,
    date          TEXT NOT NULL,
    message       TEXT,
    steam_buildid TEXT,
    files_changed INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE(game, source, ref)
);
CREATE INDEX IF NOT EXISTS idx_builds_date ON builds(date);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id    INTEGER REFERENCES builds(id),
    game        TEXT NOT NULL,
    source      TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line_no     INTEGER,
    change_type TEXT NOT NULL,
    text        TEXT NOT NULL,
    old_text    TEXT,
    token       TEXT,
    context     TEXT,
    score       INTEGER NOT NULL,
    keywords    TEXT NOT NULL,
    dedup_hash  TEXT NOT NULL,
    is_new      INTEGER NOT NULL DEFAULT 0,
    notified    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_game ON findings(game);
CREATE INDEX IF NOT EXISTS idx_findings_score ON findings(score);
CREATE INDEX IF NOT EXISTS idx_findings_created ON findings(created_at);
CREATE INDEX IF NOT EXISTS idx_findings_token ON findings(token);
CREATE INDEX IF NOT EXISTS idx_findings_build ON findings(build_id);

CREATE TABLE IF NOT EXISTS seen_strings (
    hash             TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    value            TEXT NOT NULL,
    first_seen       TEXT NOT NULL,
    first_finding_id INTEGER,
    hits             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS steam_apps (
    appid           INTEGER PRIMARY KEY,
    name            TEXT,
    type            TEXT,
    developer       TEXT,
    publisher       TEXT,
    first_seen      TEXT NOT NULL,
    last_change     INTEGER,
    flagged         INTEGER NOT NULL DEFAULT 0,
    flag_reason     TEXT,
    details_checked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_steam_apps_flagged ON steam_apps(flagged);

CREATE TABLE IF NOT EXISTS steam_builds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    appid        INTEGER NOT NULL,
    game         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    buildid      TEXT NOT NULL,
    time_updated TEXT,
    first_seen   TEXT NOT NULL,
    UNIQUE(appid, branch, buildid)
);

CREATE TABLE IF NOT EXISTS snapshots (
    key        TEXT PRIMARY KEY,
    hash       TEXT NOT NULL,
    content    TEXT,
    updated_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def dedup_hash(kind: str, value: str) -> str:
    return hashlib.sha1(f"{kind}:{value}".encode("utf-8", "replace")).hexdigest()


class Database:
    """Wrapper minimale su SQLite, sicuro per uso da thread diversi (dashboard + scheduler)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ------------------------------------------------------------------ state
    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, None if value is None else str(value)),
            )
            self.conn.commit()

    # ----------------------------------------------------------------- builds
    def build_exists(self, game: str, source: str, ref: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM builds WHERE game = ? AND source = ? AND ref = ?", (game, source, ref)
            ).fetchone()
        return row is not None

    def upsert_build(self, build: BuildInfo) -> int:
        with self._lock:
            self.conn.execute(
                "INSERT INTO builds(game, source, ref, url, date, message, steam_buildid, files_changed, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(game, source, ref) DO UPDATE SET url = excluded.url, message = excluded.message, "
                "steam_buildid = COALESCE(excluded.steam_buildid, builds.steam_buildid), "
                "files_changed = COALESCE(excluded.files_changed, builds.files_changed)",
                (build.game, build.source, build.ref, build.url, iso(build.date), build.message,
                 build.steam_buildid, build.files_changed, iso(utcnow())),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM builds WHERE game = ? AND source = ? AND ref = ?",
                (build.game, build.source, build.ref),
            ).fetchone()
        build.id = int(row["id"])
        return build.id

    def recent_builds(self, limit: int = 50, game: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT b.*, (SELECT COUNT(*) FROM findings f WHERE f.build_id = b.id) AS n_findings FROM builds b"
        params: list[Any] = []
        if game:
            sql += " WHERE b.game = ?"
            params.append(game)
        sql += " ORDER BY b.date DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def get_build(self, build_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute("SELECT * FROM builds WHERE id = ?", (build_id,)).fetchone()

    def builds_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT b.*, (SELECT COUNT(*) FROM findings f WHERE f.build_id = b.id) AS n_findings, "
                "(SELECT MAX(score) FROM findings f WHERE f.build_id = b.id) AS max_score "
                "FROM builds b WHERE b.created_at >= ? AND b.created_at < ? ORDER BY b.game, b.date",
                (iso(start), iso(end)),
            ).fetchall()

    # --------------------------------------------------------------- findings
    def add_finding(self, finding: Finding, build_id: int | None = None) -> Finding:
        """Salva il finding, determina se la stringa è nuova (deduplica) e aggiorna seen_strings."""
        kind, value = finding.dedup_key
        h = dedup_hash(kind, value)
        now = iso(utcnow())
        with self._lock:
            seen = self.conn.execute("SELECT hits FROM seen_strings WHERE hash = ?", (h,)).fetchone()
            finding.is_new = seen is None
            cur = self.conn.execute(
                "INSERT INTO findings(build_id, game, source, file_path, line_no, change_type, text, old_text, token, "
                "context, score, keywords, dedup_hash, is_new, notified, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                (build_id, finding.game, finding.source, finding.file_path, finding.line_no, finding.change_type,
                 finding.text, finding.old_text, finding.token, finding.context, finding.score,
                 json.dumps(finding.keywords), h, int(finding.is_new), now),
            )
            finding.id = int(cur.lastrowid)
            finding.build_id = build_id
            if seen is None:
                self.conn.execute(
                    "INSERT INTO seen_strings(hash, kind, value, first_seen, first_finding_id, hits) VALUES(?,?,?,?,?,1)",
                    (h, kind, value[:2000], now, finding.id),
                )
            else:
                self.conn.execute("UPDATE seen_strings SET hits = hits + 1 WHERE hash = ?", (h,))
            self.conn.commit()
        return finding

    def mark_notified(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self.conn.executemany("UPDATE findings SET notified = 1 WHERE id = ?", [(i,) for i in ids])
            self.conn.commit()

    def get_finding(self, finding_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT f.*, b.ref AS build_ref, b.url AS build_url, b.date AS build_date, b.message AS build_message "
                "FROM findings f LEFT JOIN builds b ON b.id = f.build_id WHERE f.id = ?",
                (finding_id,),
            ).fetchone()

    def search_findings(
        self,
        q: str | None = None,
        game: str | None = None,
        source: str | None = None,
        change_type: str | None = None,
        exclude_change_type: str | None = None,
        min_score: int | None = None,
        only_new: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "created_at",
    ) -> list[sqlite3.Row]:
        sql = ("SELECT f.*, b.ref AS build_ref, b.url AS build_url, b.date AS build_date "
               "FROM findings f LEFT JOIN builds b ON b.id = f.build_id WHERE 1=1")
        params: list[Any] = []
        if q:
            like = f"%{q}%"
            sql += " AND (f.text LIKE ? OR f.token LIKE ? OR f.file_path LIKE ? OR f.context LIKE ?)"
            params += [like, like, like, like]
        if game:
            sql += " AND f.game = ?"
            params.append(game)
        if source:
            sql += " AND f.source = ?"
            params.append(source)
        if change_type:
            sql += " AND f.change_type = ?"
            params.append(change_type)
        if exclude_change_type:
            sql += " AND f.change_type != ?"
            params.append(exclude_change_type)
        if min_score is not None:
            sql += " AND f.score >= ?"
            params.append(min_score)
        if only_new:
            sql += " AND f.is_new = 1"
        # le date si riferiscono alla modifica di Valve (data del commit/build), non al momento della scoperta
        if since is not None:
            sql += " AND COALESCE(b.date, f.created_at) >= ?"
            params.append(iso(since))
        if until is not None:
            sql += " AND COALESCE(b.date, f.created_at) < ?"
            params.append(iso(until))
        order_sql = {"score": "f.score DESC, f.created_at DESC", "created_at": "f.created_at DESC, f.id DESC"}
        sql += " ORDER BY " + order_sql.get(order, order_sql["created_at"]) + " LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def count_findings(self, **kwargs: Any) -> int:
        rows = self.search_findings(limit=1_000_000, **kwargs)
        return len(rows)

    def timeline(self, days: int = 90, min_score: int = 0) -> list[dict[str, Any]]:
        since = utcnow() - timedelta(days=days)
        with self._lock:
            rows = self.conn.execute(
                "SELECT substr(COALESCE(b.date, f.created_at), 1, 10) AS day, f.game, COUNT(*) AS n, "
                "MAX(f.score) AS max_score, SUM(f.is_new) AS n_new "
                "FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
                "WHERE COALESCE(b.date, f.created_at) >= ? AND f.score >= ? "
                "GROUP BY day, f.game ORDER BY day",
                (iso(since), min_score),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            c = self.conn
            out = {
                "findings": c.execute("SELECT COUNT(*) FROM findings").fetchone()[0],
                "findings_new": c.execute("SELECT COUNT(*) FROM findings WHERE is_new = 1").fetchone()[0],
                "findings_high": c.execute("SELECT COUNT(*) FROM findings WHERE score >= 100").fetchone()[0],
                "builds": c.execute("SELECT COUNT(*) FROM builds").fetchone()[0],
                "seen_strings": c.execute("SELECT COUNT(*) FROM seen_strings").fetchone()[0],
                "steam_apps": c.execute("SELECT COUNT(*) FROM steam_apps").fetchone()[0],
                "steam_apps_flagged": c.execute("SELECT COUNT(*) FROM steam_apps WHERE flagged = 1").fetchone()[0],
                "steam_builds": c.execute("SELECT COUNT(*) FROM steam_builds").fetchone()[0],
                "last_scan": self.get_state("last_scan_at"),
                "games": [r[0] for r in c.execute("SELECT DISTINCT game FROM findings ORDER BY game").fetchall()],
                "per_game": {r[0]: r[1] for r in c.execute(
                    "SELECT game, COUNT(*) FROM findings GROUP BY game").fetchall()},
            }
        return out

    def top_tokens(self, limit: int = 30, min_score: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT token, COUNT(*) AS n, MAX(score) AS max_score, MIN(created_at) AS first_seen, "
                "GROUP_CONCAT(DISTINCT game) AS games FROM findings WHERE token IS NOT NULL AND score >= ? "
                "GROUP BY token ORDER BY max_score DESC, n DESC LIMIT ?",
                (min_score, limit),
            ).fetchall()

    # -------------------------------------------------------------- steam apps
    def known_appids(self) -> set[int]:
        with self._lock:
            return {int(r[0]) for r in self.conn.execute("SELECT appid FROM steam_apps").fetchall()}

    def app_known(self, appid: int) -> bool:
        with self._lock:
            return self.conn.execute("SELECT 1 FROM steam_apps WHERE appid = ?", (appid,)).fetchone() is not None

    def insert_apps(self, apps: Iterable[tuple[int, str | None]], checked: bool = False) -> int:
        now = iso(utcnow())
        rows = [(appid, name, now, int(checked)) for appid, name in apps]
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO steam_apps(appid, name, first_seen, details_checked) VALUES(?,?,?,?)", rows
            )
            self.conn.commit()
        return len(rows)

    def reset_private_apps(self, appids: Iterable[int]) -> int:
        """Le app viste come private (senza nome) che cambiano di nuovo vanno rivalutate: potrebbero essere diventate pubbliche."""
        ids = list(appids)
        changed = 0
        with self._lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                cur = self.conn.execute(
                    f"UPDATE steam_apps SET details_checked = 0 WHERE details_checked = 1 AND name IS NULL "
                    f"AND flag_reason LIKE '%richiede token%' AND appid IN ({marks})", chunk)
                changed += cur.rowcount
            self.conn.commit()
        return changed

    def apps_without_details(self, limit: int) -> list[int]:
        """App registrate ma mai verificate (anche quelle rimaste indietro per un errore PICS)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT appid FROM steam_apps WHERE details_checked = 0 ORDER BY appid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [int(r[0]) for r in rows]

    def update_app_details(self, appid: int, name: str | None, app_type: str | None, developer: str | None,
                           publisher: str | None, last_change: int | None, flagged: bool, flag_reason: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO steam_apps(appid, name, type, developer, publisher, first_seen, last_change, flagged, "
                "flag_reason, details_checked) VALUES(?,?,?,?,?,?,?,?,?,1) "
                "ON CONFLICT(appid) DO UPDATE SET name = COALESCE(excluded.name, steam_apps.name), "
                "type = excluded.type, developer = excluded.developer, publisher = excluded.publisher, "
                "last_change = excluded.last_change, flagged = excluded.flagged, flag_reason = excluded.flag_reason, "
                "details_checked = 1",
                (appid, name, app_type, developer, publisher, iso(utcnow()), last_change, int(flagged), flag_reason),
            )
            self.conn.commit()

    def flagged_apps(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM steam_apps WHERE flagged = 1 ORDER BY first_seen DESC LIMIT ?", (limit,)
            ).fetchall()

    def recent_apps(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM steam_apps WHERE details_checked = 1 ORDER BY first_seen DESC LIMIT ?", (limit,)
            ).fetchall()

    # ------------------------------------------------------------ steam builds
    def upsert_steam_build(self, appid: int, game: str, branch: str, buildid: str, time_updated: str | None) -> bool:
        """Ritorna True se la build non era mai stata vista."""
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO steam_builds(appid, game, branch, buildid, time_updated, first_seen) "
                "VALUES(?,?,?,?,?,?)",
                (appid, game, branch, str(buildid), time_updated, iso(utcnow())),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def steam_builds(self, limit: int = 100, game: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM steam_builds"
        params: list[Any] = []
        if game:
            sql += " WHERE game = ?"
            params.append(game)
        sql += " ORDER BY first_seen DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # --------------------------------------------------------------- snapshots
    def get_snapshot(self, key: str) -> tuple[str, str | None] | None:
        with self._lock:
            row = self.conn.execute("SELECT hash, content FROM snapshots WHERE key = ?", (key,)).fetchone()
        return (row["hash"], row["content"]) if row else None

    def set_snapshot(self, key: str, content_hash: str, content: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO snapshots(key, hash, content, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET hash = excluded.hash, content = excluded.content, "
                "updated_at = excluded.updated_at",
                (key, content_hash, content, iso(utcnow())),
            )
            self.conn.commit()
