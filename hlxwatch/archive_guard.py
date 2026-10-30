"""Protezione dell'archivio: prima di salvarlo, il run controlla che non si sia svuotato.

A ogni run il ramo `data` viene ricreato da zero con l'archivio appena prodotto, e la cache di Actions viene
sovrascritta: se un run producesse un archivio rotto o mezzo vuoto, al run dopo la versione buona sarebbe persa.
Qui si confrontano i conteggi dell'archivio attuale con lo `stats.json` dell'ultimo salvataggio nel ramo data:
se voci, build o stringhe viste calano più di `archive_guard.max_drop_percent` (5%), il run non salva niente
(né ramo data né cache, così il run dopo riparte dall'ultima copia buona) e avvisa. Un calo voluto, per esempio
dopo aver tolto una parola chiave dal config, si accetta con l'opzione "accetta_calo" del workflow.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from hlxwatch.config import Config
from hlxwatch.storage import Database

log = logging.getLogger(__name__)
CHECKED = ("findings", "builds", "seen_strings")
LABELS = {"findings": "voci", "builds": "build e commit", "seen_strings": "stringhe viste"}
TITLE = "HLX Watch: archivio NON salvato"


def read_old(path: str | Path | None) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check(old: dict[str, Any] | None, new: dict[str, Any], max_drop_percent: float) -> list[str]:
    """I problemi trovati (vuoto = si può salvare)."""
    if not new.get("findings"):
        return ["l'archivio attuale non contiene nessuna voce"]
    problems = []
    for key in CHECKED:
        before, after = int((old or {}).get(key) or 0), int(new.get(key) or 0)
        if before > 0 and (before - after) * 100 / before > max_drop_percent:
            problems.append(f"{LABELS[key]}: da {before} a {after} (-{(before - after) * 100 / before:.1f}%)")
    return problems


def run_guard(cfg: Config, db: Database, old_path: str | None, accept: bool, notifiers: list) -> bool:
    from hlxwatch.notifiers import notify_text
    max_drop = float((cfg.get("archive_guard", {}) or {}).get("max_drop_percent", 5))
    old = read_old(old_path)
    new = db.stats()
    problems = check(old, new, max_drop)
    if not old:
        log.info("Archivio: nessun salvataggio precedente da confrontare, si salva")
    if not problems:
        log.info("Archivio: controllo superato (%d voci, prima %s)", new["findings"], (old or {}).get("findings", "?"))
        return True
    if accept:
        log.warning("Archivio: calo accettato a mano: %s", "; ".join(problems))
        return True
    text = ("L'archivio prodotto da questo run è più piccolo dell'ultimo salvato:\n- " + "\n- ".join(problems) +
            "\nNon l'ho salvato: il ramo data e la cache restano all'ultima copia buona, e il prossimo run riparte "
            "da lì. Se il calo è voluto (per esempio hai tolto una parola chiave), rilancia il workflow con "
            "l'opzione \"accetta_calo\".")
    log.error("Archivio NON salvato: %s", "; ".join(problems))
    notify_text(notifiers, TITLE, text)
    return False
