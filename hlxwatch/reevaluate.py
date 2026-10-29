"""Riapplica le regole attuali a tutto l'archivio: utile dopo aver cambiato parole chiave o filtri.

Le voci che non nascono da una parola chiave (build, app e repository nuovi, nomi di progetto, file
sorvegliati) non vengono toccate: rianalizzarle con il KeywordMatcher le cancellerebbe.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import NON_KEYWORD_KEYWORDS, NON_KEYWORD_TYPES
from hlxwatch.storage import Database

log = logging.getLogger(__name__)


def reevaluate(cfg: Config, db: Database) -> dict[str, int]:
    ignore = {r["name"]: r.get("ignore_keywords", []) or [] for r in cfg.get("github.repos", []) or []}
    ignore.update({a["name"]: a.get("ignore_keywords", []) or [] for a in cfg.steam_watch_apps})
    matchers = {g: KeywordMatcher(cfg.keywords, ignore_ids=ids) for g, ids in ignore.items()}
    default = KeywordMatcher(cfg.keywords)
    types = ", ".join("?" for _ in NON_KEYWORD_TYPES)
    kw_cond = " AND ".join("keywords NOT LIKE ?" for _ in NON_KEYWORD_KEYWORDS)
    params: list[Any] = [*NON_KEYWORD_TYPES, *(f'%"{k}"%' for k in NON_KEYWORD_KEYWORDS)]
    rows = db.conn.execute(
        "SELECT id, game, text, file_path, change_type, score, keywords FROM findings "
        f"WHERE change_type NOT IN ({types}) AND {kw_cond}", params).fetchall()
    drop: list[int] = []
    rescored = 0
    for r in rows:
        change_type = "added" if r["change_type"] in ("baseline", "present") else r["change_type"]
        res = matchers.get(r["game"], default).analyze_line(r["text"], file_path=r["file_path"], change_type=change_type)
        if res is None:
            drop.append(r["id"])
        elif res.score != r["score"]:
            db.conn.execute("UPDATE findings SET score = ?, keywords = ?, token = ? WHERE id = ?",
                            (res.score, json.dumps(res.keywords), res.token, r["id"]))
            rescored += 1
    db.conn.executemany("DELETE FROM findings WHERE id = ?", [(i,) for i in drop])
    # app private senza altri segnali, registrate come voci dalle versioni precedenti
    private = db.conn.execute(
        "SELECT id, build_id FROM findings WHERE change_type = 'new_app' AND keywords = '[\"private_app\"]'").fetchall()
    db.conn.executemany("DELETE FROM findings WHERE id = ?", [(r["id"],) for r in private])
    db.conn.executemany("DELETE FROM builds WHERE id = ? AND NOT EXISTS (SELECT 1 FROM findings WHERE build_id = ?)",
                        [(r["build_id"], r["build_id"]) for r in private if r["build_id"]])
    # build Steam registrate con il vecchio link alla pagina del negozio
    db.conn.execute("UPDATE builds SET url = 'https://steamdb.info/patchnotes/' || steam_buildid || '/' "
                    "WHERE source = 'steam' AND steam_buildid IS NOT NULL AND url LIKE 'https://store.steampowered.com/%'")
    db.conn.execute("UPDATE builds SET url = replace(url, 'https://store.steampowered.com/app/', "
                    "'https://steamdb.info/app/') || 'history/' "
                    "WHERE source = 'steam' AND ref LIKE 'pics:%' AND url LIKE 'https://store.steampowered.com/app/%'")
    db.conn.commit()
    stats = {"esaminate": len(rows), "eliminate": len(drop), "aggiornate": rescored, "private": len(private)}
    log.info("Rivalutazione: %d voci esaminate, %d eliminate, %d con punteggio aggiornato, %d app private rimosse",
             stats["esaminate"], stats["eliminate"], stats["aggiornate"], stats["private"])
    return stats
