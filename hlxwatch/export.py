"""Esportazione dell'archivio: copia coerente del database (compressa) e CSV delle voci.

Usata dal workflow per salvare l'archivio nel ramo `data` del repository: una copia permanente
che sopravvive alla cache di GitHub Actions e che chiunque può scaricare per le proprie analisi.
"""
from __future__ import annotations

import csv
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

CSV_FIELDS = ["id", "data_modifica", "vista_il", "gioco", "sorgente", "tipo", "punteggio", "token", "testo",
              "testo_precedente", "file", "riga", "parole_chiave", "nuova", "commit"]

README = """# Archivio di HLX Watch

Copia aggiornata automaticamente dell'archivio di [HLX Watch]({repo}): riferimenti a HLX e ad altri
progetti Valve trovati nei dati pubblici di SteamTracking/GameTracking-* e Steam PICS.

Aggiornato: {updated} UTC

| File | Contenuto |
|---|---|
| `hlxwatch.sqlite.gz` | database SQLite completo (gzip): voci, build, stringhe viste, app Steam, analisi AI |
| `findings.csv` | tutte le voci tranne le baseline Steam, una per riga (UTF-8) |
| `stats.json` | contatori riassuntivi |

Metodologia e significato dei campi: {site}metodologia.html

Questo ramo contiene un solo commit, riscritto a ogni aggiornamento: la cronologia delle versioni non viene
conservata qui, ma ogni voce ha la propria data di modifica e il link al commit di origine.
"""


def backup_db(db_path: Path, out_path: Path) -> None:
    """Copia coerente anche con la modalità WAL (API di backup di SQLite), compressa con gzip."""
    tmp = out_path.with_suffix(".tmp.sqlite")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with tmp.open("rb") as fin, gzip.open(out_path, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)
    tmp.unlink()


def restore_db(gz_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gz_path, "rb") as fin, db_path.open("wb") as fout:
        shutil.copyfileobj(fin, fout)


def write_csv(db: Database, out_path: Path) -> int:
    rows = db.search_findings(limit=1_000_000, order="created_at", exclude_change_type="baseline")
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_FIELDS)
        for r in rows:
            writer.writerow([
                r["id"], (r["build_date"] or r["created_at"] or "")[:10], (r["created_at"] or "")[:10], r["game"],
                r["source"], r["change_type"], r["score"], r["token"] or "", r["text"], r["old_text"] or "",
                r["file_path"], r["line_no"] or "", ", ".join(json.loads(r["keywords"] or "[]")),
                int(r["is_new"]), r["build_url"] or "",
            ])
    return len(rows)


def export_all(cfg: Config, db: Database, out_dir: Path, repo_url: str, site_url: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    backup_db(cfg.db_path, out_dir / "hlxwatch.sqlite.gz")
    n = write_csv(db, out_dir / "findings.csv")
    stats = db.stats()
    (out_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "README.md").write_text(README.format(repo=repo_url, site=site_url,
                                                     updated=utcnow().strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    return {"findings": n, "size_gz": (out_dir / "hlxwatch.sqlite.gz").stat().st_size}
