"""Archivio completo delle righe nuove: tutte le righe aggiunte e tolte nei file che contano, commit per commit.

Fino a novembre 2026 HLX Watch conservava solo le righe con una parola sorvegliata. Il 18/11/2026 Dota 2 ha ricevuto
lo streaming dei livelli di Source 2 (salvataggi .hl1/.hl2, difficoltà skill 0-3): nessuna parola sorvegliata, nessun
avviso, e per provare le regole nuove si sono dovute riscaricare 32.000 righe da GitHub (65 file senza diff).

Qui si tiene tutto quello che serve per rifare i conti in casa:
  - comandi e variabili del motore (DumpSource2/convars.txt, commands.txt), entità e schemi, file FGD, Protobuf,
    gameinfo.gi e i dump di stringhe dei programmi (*_strings.txt);
  - righe compresse (zlib), una riga di tabella per file e commit: pochi KB per commit, ~210 KB per l'aggiornamento
    enorme del 18/11/2026;
  - le righe arrivano da _extra_findings in github_tracking.py (patch di GitHub, diff manuale dei file grandi, git per
    i commit oltre 3000 file): nessuna richiesta in più;
  - i commit già nell'archivio prima di oggi si riempiono un po' alla volta (fill_step), all'indietro fino a
    archivio_righe.dal.

Con l'archivio: `python -m hlxwatch archivio-righe cerca REGEX` e `... prova` (le parole chiave di oggi sulle righe
già viste: cosa avrebbero segnalato).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import zlib
from collections.abc import Iterator
from typing import Any

from hlxwatch.storage import Database
from datetime import timedelta

from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

DEFAULT_FILES = [
    r"^DumpSource2/(convars|commands)\.txt$",
    r"^DumpSource2/entities/",
    r"^DumpSource2/schemas/",
    r"\.fgd$",
    r"\.proto$",                       # Protobufs/ nei giochi, dota2/ csgo/ ... nel repository Protobufs
    r"(^|/)gameinfo\.gi$",
    r"_strings\.txt$",
]
MAX_LINES_PER_FILE = 60000
# schemi e dump di stringhe: tanti e quasi sempre riformattati, si tengono solo le righe aggiunte e, oltre il tetto
# (archivio_righe.max_mb), si tolgono per primi i più vecchi. Comandi, entità, FGD e Protobuf (pochi KB) restano sempre.
BULKY = re.compile(r"_strings\.txt$|^DumpSource2/schemas/")
DEFAULT_MAX_MB = 20
DEFAULT_GAMES = ["cs2", "dota2", "deadlock", "alyx", "steamvr", "protobufs"]
STATE_FAILS = "archivio_righe:errori:"
MAX_FAILS = 3
KEEP_DAYS = 7                    # il tetto non tocca i commit degli ultimi giorni: servono ancora alla seconda rete
SCHEMA = """
CREATE TABLE IF NOT EXISTS line_archive (
    build_id  INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    added     INTEGER NOT NULL,
    removed   INTEGER NOT NULL,
    data      BLOB NOT NULL,
    PRIMARY KEY (build_id, file_path)
);
CREATE TABLE IF NOT EXISTS line_archive_builds (
    build_id INTEGER PRIMARY KEY,
    files    INTEGER NOT NULL,
    lines    INTEGER NOT NULL,
    at       TEXT NOT NULL
);
"""


def ensure_schema(db: Database) -> None:
    db.conn.executescript(SCHEMA)


def patterns(cfg: Any) -> list[re.Pattern[str]]:
    files = ((cfg.get("archivio_righe", {}) or {}).get("file") if cfg is not None else None) or DEFAULT_FILES
    return [re.compile(p) for p in files]


def enabled(cfg: Any) -> bool:
    return bool((cfg.get("archivio_righe", {}) or {}).get("enabled", True)) if cfg is not None else True


def wanted(pats: list[re.Pattern[str]], path: str) -> bool:
    return not path.endswith(".stringsignore") and any(p.search(path) for p in pats)


def pack(lines: list[tuple[str, str]]) -> bytes:
    return zlib.compress("\n".join(op + text.replace("\n", " ") for op, text in lines).encode("utf-8"), 9)


def unpack(data: bytes) -> list[tuple[str, str]]:
    text = zlib.decompress(data).decode("utf-8")
    return [(ln[:1], ln[1:]) for ln in text.split("\n") if ln]


class Collector:
    """Raccoglie le righe di un commit mentre l'analisi lo legge (anche da più thread), poi le salva tutte insieme."""

    def __init__(self, cfg: Any):
        self.on = enabled(cfg)
        self.pats = patterns(cfg)
        self._lock = threading.Lock()
        self.files: dict[str, list[tuple[str, str]]] = {}

    def reset(self) -> None:
        with self._lock:
            self.files = {}

    def add(self, path: str, lines: list[tuple[str, str]]) -> None:
        if not self.on or not lines or not wanted(self.pats, path):
            return
        keep = [(op, t) for op, t in lines if (op == "+" or (op == "-" and not BULKY.search(path)))
                and t.strip()][:MAX_LINES_PER_FILE]
        if keep:
            with self._lock:
                self.files.setdefault(path, []).extend(keep)

    def save(self, db: Database, build_id: int) -> int:
        with self._lock:
            files, self.files = self.files, {}
        if not self.on:
            return 0
        return store(db, build_id, files)


def store(db: Database, build_id: int, files: dict[str, list[tuple[str, str]]]) -> int:
    """Salva le righe di un commit (sostituisce quelle che c'erano) e lo segna come archiviato. Restituisce le righe."""
    ensure_schema(db)
    total = 0
    with db._lock:
        for path, lines in files.items():
            added = sum(1 for op, _ in lines if op == "+")
            db.conn.execute("INSERT OR REPLACE INTO line_archive (build_id, file_path, added, removed, data) "
                            "VALUES (?, ?, ?, ?, ?)", (build_id, path, added, len(lines) - added, pack(lines)))
            total += len(lines)
        db.conn.execute("INSERT OR REPLACE INTO line_archive_builds (build_id, files, lines, at) VALUES (?, ?, ?, ?)",
                        (build_id, len(files), total, iso(utcnow())))
        db.conn.commit()
    return total


def iter_lines(db: Database, *, game: str | None = None, path_re: str | None = None, since: str | None = None,
               op: str | None = "+") -> Iterator[dict[str, Any]]:
    """Le righe archiviate, dalla più vecchia: gioco, data e commit, file, segno (+/-) e testo."""
    ensure_schema(db)
    sql = ("SELECT a.build_id, a.file_path, a.data, b.game, b.date, b.ref, b.url FROM line_archive a "
           "JOIN builds b ON b.id = a.build_id WHERE 1=1")
    params: list[Any] = []
    if game:
        sql += " AND b.game = ?"
        params.append(game)
    if since:
        sql += " AND b.date >= ?"
        params.append(since)
    sql += " ORDER BY b.date, a.file_path"
    rx = re.compile(path_re) if path_re else None
    for row in db.conn.execute(sql, params).fetchall():
        if rx and not rx.search(row["file_path"]):
            continue
        for sign, text in unpack(row["data"]):
            if op and sign != op:
                continue
            yield {"build_id": row["build_id"], "game": row["game"], "date": row["date"], "sha": row["ref"],
                   "url": row["url"], "file": row["file_path"], "op": sign, "text": text}


def search(db: Database, regex: str, **kw: Any) -> list[dict[str, Any]]:
    rx = re.compile(regex, re.IGNORECASE)
    return [r for r in iter_lines(db, **kw) if rx.search(r["text"])]


def try_rules(cfg: Any, db: Database, *, keywords: list[str] | None = None, min_score: int = 50,
              **kw: Any) -> list[dict[str, Any]]:
    """Le parole chiave di oggi sulle righe già archiviate: che cosa avrebbero segnalato, commit per commit.
    `keywords` limita il risultato alle righe che contengono almeno una di quelle parole chiave."""
    from hlxwatch.analyzers import KeywordMatcher
    matcher = KeywordMatcher(cfg.keywords)
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for r in iter_lines(db, **kw):
        res = matcher.analyze_line(r["text"], file_path=r["file"], change_type="added")
        if not res or res.score < min_score or (keywords and not set(keywords) & set(res.keywords)):
            continue
        key = (r["build_id"], r["game"])
        item = out.setdefault(key, {"game": r["game"], "date": r["date"], "sha": r["sha"], "url": r["url"],
                                    "righe": 0, "max": 0, "parole": set(), "esempi": []})
        item["righe"] += 1
        item["max"] = max(item["max"], res.score)
        item["parole"].update(res.keywords)
        if len(item["esempi"]) < 3:
            item["esempi"].append(f"{r['file'].split('/')[-1]}: {r['text'].strip()[:120]}")
    rows = sorted(out.values(), key=lambda x: x["date"])
    for x in rows:
        x["parole"] = sorted(x["parole"])
    return rows


def stats(db: Database) -> dict[str, Any]:
    ensure_schema(db)
    row = db.conn.execute("SELECT COUNT(*), COALESCE(SUM(lines), 0) FROM line_archive_builds").fetchone()
    size = db.conn.execute("SELECT COALESCE(SUM(length(data)), 0) FROM line_archive").fetchone()[0]
    first = db.conn.execute("SELECT MIN(b.date) FROM line_archive_builds a JOIN builds b ON b.id = a.build_id").fetchone()[0]
    return {"commit": row[0], "righe": row[1], "kb": round(size / 1024), "dal": (first or "")[:10]}


# ------------------------------------------------------------------ riempimento dei commit già visti
def fill_candidates(cfg: Any, db: Database, limit: int) -> list[Any]:
    """Commit di GitHub già nell'archivio, non ancora archiviati, dal più recente all'indietro fino a `dal`."""
    ensure_schema(db)
    acfg = cfg.get("archivio_righe", {}) or {}
    since = str(acfg.get("dal", "2026-09-01"))
    # solo i repository con quei file: il client di Steam e la documentazione non ne hanno (richieste sprecate)
    games = [str(g) for g in (acfg.get("giochi") or DEFAULT_GAMES)]
    marks = ",".join("?" * len(games))
    return db.conn.execute(
        "SELECT b.id, b.game, b.ref FROM builds b LEFT JOIN line_archive_builds a ON a.build_id = b.id "
        f"WHERE b.source = 'github' AND length(b.ref) = 40 AND a.build_id IS NULL AND b.date >= ? AND b.game IN ({marks}) "
        "ORDER BY b.date DESC LIMIT ?", (since, *games, limit)).fetchall()


def fill_build(cfg: Any, db: Database, row: Any, client: Any, repos: dict[str, Any] | None = None) -> int:
    """Archivia un commit già visto: legge dall'API le modifiche dei soli file da archiviare e, se GitHub non le dà,
    confronta le due versioni del file. `row` ha id, game e ref. Restituisce le righe salvate."""
    from hlxwatch.analyzers.diff_analyzer import parse_patch
    if repos is None:
        from hlxwatch.repos import tracked_repos
        repos = {r["name"]: r for r in tracked_repos(cfg, db)}
    repo_cfg = repos.get(row["game"])
    if not repo_cfg:
        return store(db, row["id"], {})                    # repository non più seguito: niente da archiviare
    detail = client.commit_detail(repo_cfg["repo"], row["ref"], int(cfg.get("github.max_file_pages", 10)))
    if detail is None:
        return store(db, row["id"], {})
    pats = patterns(cfg)
    max_bytes = int(cfg.get("github.fallback_max_bytes", 25 * 1024 * 1024))
    parent = ((detail.get("parents") or [{}])[0] or {}).get("sha")
    files: dict[str, list[tuple[str, str]]] = {}
    for entry in detail.get("files") or []:
        path = entry.get("filename") or ""
        if not wanted(pats, path):
            continue
        if entry.get("patch"):
            lines = [(dl.op, dl.text) for hunk in parse_patch(entry["patch"]) for dl in hunk if dl.op in "+-"]
        else:
            status = entry.get("status")
            new = "" if status == "removed" else client.raw_file(repo_cfg["repo"], row["ref"], path, max_bytes)
            old = "" if status == "added" or not parent else client.raw_file(
                repo_cfg["repo"], parent, entry.get("previous_filename") or path, max_bytes)
            if new is None or old is None:
                continue                                # più grande del limite: resta fuori per sempre (voluto)
            old_l, new_l = old.splitlines(), new.splitlines()
            old_s, new_s = set(old_l), set(new_l)
            lines = [("+", ln) for ln in new_l if ln not in old_s] + [("-", ln) for ln in old_l if ln not in new_s]
        keep = [(op, t) for op, t in lines if (op == "+" or not BULKY.search(path)) and t.strip()][:MAX_LINES_PER_FILE]
        if keep:
            files[path] = keep
    n = store(db, row["id"], files)
    log.info("Archivio delle righe: %s %s riempito, %d file, %d righe", row["game"], row["ref"][:10], len(files), n)
    return n


def fill_step(cfg: Any, db: Database, client: Any = None, limit: int | None = None) -> int:
    """Archivia alcuni commit vecchi a ogni giro, dal più recente all'indietro. Restituisce i commit archiviati."""
    if not enabled(cfg):
        return 0
    limit = int(limit if limit is not None else (cfg.get("archivio_righe", {}) or {}).get("commit_per_giro", 6))
    prune(cfg, db)                                     # il tetto vale sempre, anche quando non c'è più niente da riempire
    rows = fill_candidates(cfg, db, limit)
    if not rows:
        return 0
    from hlxwatch.repos import tracked_repos
    repos = {r["name"]: r for r in tracked_repos(cfg, db)}
    if client is None:
        from hlxwatch.sources.github_tracking import GitHubClient
        client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                              cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
    done = 0
    for row in rows:
        key = f"{STATE_FAILS}{row['id']}"
        try:
            fill_build(cfg, db, row, client, repos)
            done += 1
        except Exception as exc:                       # noqa: BLE001 - un commit difettoso non ferma gli altri
            fails = int(db.get_state(key) or 0) + 1
            db.set_state(key, str(fails))
            log.warning("Archivio delle righe: %s %s non archiviato (%s), tentativo %d", row["game"], row["ref"][:10],
                        exc, fails)
            if fails >= MAX_FAILS:                     # dopo 3 tentativi si segna vuoto, per non bloccare la coda
                store(db, row["id"], {})
    return done


def prune(cfg: Any, db: Database) -> int:
    """Oltre il tetto toglie le righe di schemi e dump dei commit più vecchi (il commit resta segnato come archiviato,
    con i suoi comandi, entità e FGD). Restituisce le righe di tabella tolte."""
    ensure_schema(db)
    max_bytes = float((cfg.get("archivio_righe", {}) or {}).get("max_mb", DEFAULT_MAX_MB)) * 1024 * 1024
    total = db.conn.execute("SELECT COALESCE(SUM(length(data)), 0) FROM line_archive").fetchone()[0]
    if total <= max_bytes:
        return 0
    rows = db.conn.execute("SELECT a.build_id, a.file_path, length(a.data) FROM line_archive a JOIN builds b "
                           "ON b.id = a.build_id WHERE b.date < ? ORDER BY b.date",
                           (iso(utcnow() - timedelta(days=KEEP_DAYS)),)).fetchall()
    removed = 0
    with db._lock:
        for build_id, path, size in rows:
            if total <= max_bytes * 0.9:
                break
            if not BULKY.search(path):
                continue
            db.conn.execute("DELETE FROM line_archive WHERE build_id = ? AND file_path = ?", (build_id, path))
            total -= size
            removed += 1
        db.conn.commit()
    log.info("Archivio delle righe: oltre %.0f MB, tolti %d file vecchi di schemi e dump", max_bytes / 1048576, removed)
    return removed
