"""Struttura dei repository: cartelle, depot e protocolli che prima non c'erano.

Le parole chiave e i nomi di progetto cercano *parole*. Qui si guarda la *forma* dei repository di
SteamTracking, che cambia anche se nessun file scrive mai il nome del progetto:
  - una cartella nuova sotto `game/` di un gioco (oggi CS2 ha bin, core, csgo, csgo_addons...): un
    contenuto che prima non esisteva;
  - un file nuovo in `manifests/`: ogni file è un depot Steam, un depot nuovo è un contenuto nuovo;
  - una cartella nuova nel repository dei Protobuf (artifact, csgo, deadlock, dota2...): un protocollo
    di rete nuovo vuol dire un gioco nuovo.
A ogni run si elenca ogni posto con l'API dei contenuti di GitHub (una chiamata per posto) e si confronta
con l'elenco salvato (stato `structure:<gioco>:<percorso>`). Il primo giro registra e basta; poi ogni
voce nuova è una voce "structure" sopra la soglia di notifica, e una voce sparita una voce più bassa.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

STATE_PREFIX = "structure:"
# posti di default: "games" = repository GameTracking dei giochi (quelli con appid o seguiti in automatico)
DEFAULT_PLACES: list[dict[str, Any]] = [
    {"path": "game", "kind": "dirs", "title": "cartella sotto game/", "repos": "games"},
    {"path": "manifests", "kind": "files", "title": "depot Steam (file manifest)", "repos": "games"},
    {"path": "", "kind": "dirs", "title": "protocollo di rete (cartella dei Protobuf)", "repos": ["protobufs"]},
]


def _is_game_repo(repo_cfg: dict[str, Any]) -> bool:
    short = repo_cfg["repo"].split("/")[-1]
    return short.startswith("GameTracking-") and bool(repo_cfg.get("appid") or repo_cfg.get("auto"))


class StructureWatch:
    name = "structure"

    def __init__(self, cfg: Config, db: Database, client: Any):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.scfg: dict[str, Any] = cfg.get("structure", {}) or {}
        self.enabled = bool(self.scfg.get("enabled", True))
        self.score = int(self.scfg.get("score", 100))
        self.score_removed = int(self.scfg.get("score_removed", 60))
        self.places: list[dict[str, Any]] = list(self.scfg.get("places") or DEFAULT_PLACES)
        self.matcher = KeywordMatcher(cfg.keywords)

    # ------------------------------------------------------------ posti
    def targets(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(repository, posto) per ogni elenco da confrontare."""
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        repos = tracked_repos(self.cfg, self.db)
        for place in self.places:
            selector = place.get("repos", "games")
            for repo_cfg in repos:
                if selector == "games":
                    ok = _is_game_repo(repo_cfg)
                elif selector == "all":
                    ok = True
                else:
                    ok = repo_cfg["name"] in (selector or [])
                if ok:
                    out.append((repo_cfg, place))
        return out

    def listing(self, repo_cfg: dict[str, Any], place: dict[str, Any]) -> dict[str, str] | None:
        """nome -> url per le voci del tipo richiesto; None se il percorso non esiste o non è un elenco."""
        path = str(place.get("path") or "").strip("/")
        data = self.client.get_json(f"/repos/{repo_cfg['repo']}/contents/{path}".rstrip("/"),
                                    {"ref": repo_cfg.get("branch", "master"), "per_page": 1000})
        if not isinstance(data, list):
            return None
        wanted = "dir" if place.get("kind", "dirs") == "dirs" else "file"
        return {e["name"]: e.get("html_url") or "" for e in data if e.get("type") == wanted and e.get("name")}

    # -------------------------------------------------------------- run
    def run(self) -> list[Finding]:
        if not self.enabled:
            return []
        findings: list[Finding] = []
        for repo_cfg, place in self.targets():
            findings += self.check(repo_cfg, place)
        return findings

    def check(self, repo_cfg: dict[str, Any], place: dict[str, Any]) -> list[Finding]:
        name, path = repo_cfg["name"], str(place.get("path") or "").strip("/")
        key = f"{STATE_PREFIX}{name}:{path or '/'}"
        current = self.listing(repo_cfg, place)
        if current is None:
            log.debug("Struttura: %s/%s non esiste o non è un elenco, salto", repo_cfg["repo"], path)
            return []
        known_raw = self.db.get_state(key)
        if known_raw is None:
            self.db.set_state(key, json.dumps(sorted(current)))
            log.info("Struttura: %s registrato (%d voci), senza avvisi", f"{repo_cfg['repo']}/{path}".rstrip("/"), len(current))
            return []
        known = set(json.loads(known_raw or "[]"))
        added = sorted(set(current) - known)
        removed = sorted(known - set(current))
        if not added and not removed:
            return []
        kind_word = "cartella" if place.get("kind", "dirs") == "dirs" else "file"
        title = str(place.get("title") or path or "/")
        where = f"{repo_cfg['repo']}/{path}" if path else repo_cfg["repo"]
        branch = repo_cfg.get("branch", "master")
        build = BuildInfo(game=name, source="github", ref=f"structure:{name}:{path or '/'}:{iso(utcnow())}",
                          date=utcnow(), url=f"https://github.com/{repo_cfg['repo']}/tree/{branch}/{path}",
                          message=f"struttura di {where}: {len(added)} voci nuove, {len(removed)} sparite")
        build_id = self.db.upsert_build(build)
        out: list[Finding] = []
        for entry in added:
            score, keywords = self.score, ["structure_new"]
            # il nome stesso può contenere una parola chiave: una cartella "hlx" vale più di una qualsiasi
            res = self.matcher.analyze_line(f"{entry.replace('_', ' ')} {entry}", file_path=f"{path}/{entry}",
                                            change_type="added")
            if res:
                score = min(max(score, res.score + 20), 250)
                keywords += res.keywords
            f = Finding(game=name, source="structure", file_path=f"{path}/{entry}" if path else entry,
                        change_type="structure", text=f"{title}: {kind_word} nuova '{entry}' in {where}",
                        score=score, keywords=keywords, token=f"structure:{name}:{path}/{entry}",
                        context=current.get(entry, ""))
            f.build = build
            self.db.add_finding(f, build_id)
            log.warning("[%s] STRUTTURA NUOVA: %s '%s' in %s (punteggio %d)", name, kind_word, entry, where, f.score)
            out.append(f)
        for entry in removed:
            f = Finding(game=name, source="structure", file_path=f"{path}/{entry}" if path else entry,
                        change_type="structure", text=f"{title}: {kind_word} '{entry}' sparita da {where}",
                        score=self.score_removed, keywords=["structure_removed"],
                        token=f"structure:{name}:{path}/{entry}:removed")
            f.build = build
            self.db.add_finding(f, build_id)
            log.info("[%s] struttura: %s '%s' sparita da %s", name, kind_word, entry, where)
            out.append(f)
        self.db.set_state(key, json.dumps(sorted(current)))
        return out
