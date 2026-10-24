"""Elenco dei repository seguiti: quelli del config più quelli scoperti automaticamente.

I repository scoperti (vedi sources/discovery.py) sono salvati nello stato del database, così
sopravvivono tra i run e finiscono anche nell'archivio permanente.
"""
from __future__ import annotations

import json
import re
from typing import Any

from hlxwatch.config import Config
from hlxwatch.storage import Database

STATE_KEY = "discovery:followed"


def followed_repos(db: Database) -> list[dict[str, Any]]:
    try:
        return json.loads(db.get_state(STATE_KEY) or "[]")
    except json.JSONDecodeError:
        return []


def add_followed(db: Database, repo_cfg: dict[str, Any]) -> None:
    current = followed_repos(db)
    if any(r["repo"].lower() == repo_cfg["repo"].lower() for r in current):
        return
    current.append(repo_cfg)
    db.set_state(STATE_KEY, json.dumps(current, ensure_ascii=False))


def tracked_repos(cfg: Config, db: Database | None) -> list[dict[str, Any]]:
    """Repository del config (con la precedenza) più quelli seguiti automaticamente."""
    repos = list(cfg.github_repos)
    if db is None:
        return repos
    known_repos = {r["repo"].lower() for r in repos}
    known_names = {r["name"] for r in repos}
    for r in followed_repos(db):
        if r["repo"].lower() in known_repos or r["name"] in known_names:
            continue
        repos.append(r)
        known_names.add(r["name"])
    return repos


def game_key(repo_name: str) -> str:
    """'GameTracking-HalfLifeAlyx' -> 'halflifealyx': nome breve usato come 'gioco' nell'archivio."""
    suffix = repo_name.split("GameTracking-", 1)[-1] if repo_name.startswith("GameTracking-") else repo_name
    return re.sub(r"[^a-z0-9]+", "", suffix.lower()) or repo_name.lower()
