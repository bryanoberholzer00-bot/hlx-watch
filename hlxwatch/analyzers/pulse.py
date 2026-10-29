"""Nodi Pulse nuovi: le capacità dello scripting di Valve che arrivano con le fusioni del motore.

Pulse è il sistema di scripting visuale dei giochi Source 2 recenti (Deadlock lo usa per tutte le interazioni del
rifugio). Ogni tipo di nodo è una classe `CPulseCell_*`, e SteamTracking ne pubblica una per file in
`DumpSource2/schemas/<modulo>/CPulseCell_<nome>.h`. I nodi non hanno parole chiave, ma dicono cosa sa fare il motore:
`CPulseCell_Outflow_PlayDynamicVCD` (scene recitate "dinamiche") è arrivato in CS2 il 2026-07-09 e in Deadlock il
2026-09-29, gli stessi giorni delle fusioni che hanno portato tracce HLX. CS2 contiene i nodi per scene e sequenze
scriptate pur non avendo personaggi.
  - nei commit: un file di un nodo mai visto (stato `pulse:nodes`) è una voce subito;
  - nella scansione completa: il primo giro impara i nodi di tutti i giochi senza avvisi, poi fa da rete di sicurezza.
I nodi per scene, sequenze, dialoghi, telecamera e IA valgono di più (`score_narrative`).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

# i tipi interni di un nodo (CPulseCell_PlaySequence__CursorState_t) arrivano con il nodo: non sono nodi a sé
PULSE_FILE = re.compile(r"^DumpSource2/schemas/[^/]+/(?P<name>CPulseCell_(?:[A-Za-z0-9]|_(?!_))+)\.h$")
DEFAULT_NARRATIVE = (r"vcd|scene|sequence|choreo|subtitle|dialog|speech|response|camera|cinematic|cutscene"
                     r"|npc|squad|cover|objective|checkpoint|chapter|save")
STATE = "pulse:nodes"

GitFn = Callable[[list[str]], Any]


class PulseTracker:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        pcfg: dict[str, Any] = cfg.get("pulse", {}) or {}
        self.enabled = bool(pcfg.get("enabled", True))
        self.score = int(pcfg.get("score", 70))
        self.score_narrative = int(pcfg.get("score_narrative", 100))
        self.narrative = re.compile(str(pcfg.get("narrative_pattern") or DEFAULT_NARRATIVE), re.IGNORECASE)
        self.matcher = KeywordMatcher(cfg.keywords)
        self.seen_now: dict[str, dict[str, Any]] = {}      # nodi letti in questa scansione completa

    # ------------------------------------------------------------- stato
    def known(self) -> set[str] | None:
        raw = self.db.get_state(STATE)
        return None if raw is None else set(json.loads(raw))

    def _save(self, names: set[str]) -> None:
        self.db.set_state(STATE, json.dumps(sorted(names)))

    # ------------------------------------------------------------- commit
    def check_added(self, game: str, path: str) -> Finding | None:
        """File aggiunto in un commit: una voce se è il primo nodo Pulse con quel nome (la salva il chiamante)."""
        m = PULSE_FILE.match(path or "")
        if not self.enabled or not m:
            return None
        known = self.known()
        if known is None or m.group("name") in known:
            return None                    # prima dell'apprendimento lo trova la scansione completa
        self._save(known | {m.group("name")})
        return self._finding(game, path, m.group("name"))

    # ---------------------------------------------------- scansione completa
    def collect(self, repo_cfg: dict[str, Any], git: GitFn) -> None:
        if not self.enabled:
            return
        listing = git(["ls-tree", "-r", "--name-only", "HEAD", "DumpSource2/schemas"])
        if listing.returncode != 0:
            return
        for path in listing.stdout.decode("utf-8", "replace").splitlines():
            m = PULSE_FILE.match(path)
            if m:
                self.seen_now.setdefault(m.group("name"), {"game": repo_cfg["name"], "path": path, "repo_cfg": repo_cfg})

    def finish(self) -> list[Finding]:
        """Dopo una scansione completa di tutti i repository: impara al primo giro, poi segnala i nodi mai visti."""
        if not self.enabled or not self.seen_now:
            return []
        known = self.known()
        if known is None:
            self._save(set(self.seen_now))
            log.info("Nodi Pulse: %d imparati, senza avvisi", len(self.seen_now))
            return []
        out: list[Finding] = []
        for name in sorted(set(self.seen_now) - known):
            info = self.seen_now[name]
            f = self._finding(info["game"], info["path"], name)
            repo_cfg = info["repo_cfg"]
            build = BuildInfo(game=info["game"], source="fullscan", ref=f"pulse:{info['game']}:{iso(utcnow())[:10]}",
                              date=utcnow(), message="nodi Pulse nuovi",
                              url=f"https://github.com/{repo_cfg['repo']}/blob/{repo_cfg.get('branch', 'master')}/{info['path']}")
            f.source, f.build = "fullscan", build
            self.db.add_finding(f, self.db.upsert_build(build))
            out.append(f)
        self._save(known | set(self.seen_now))
        return out

    def _finding(self, game: str, path: str, name: str) -> Finding:
        narrative = bool(self.narrative.search(name))
        score = self.score_narrative if narrative else self.score
        keywords = ["pulse_node"]
        res = self.matcher.analyze_line(name, file_path=path, change_type="file_added")
        if res:
            score = min(max(score, res.score + 20), 250)
            keywords += res.keywords
        kind = "scene, sequenze, dialoghi, telecamera o IA" if narrative else "scripting"
        log.warning("[%s] NODO PULSE NUOVO: %s (%s, punteggio %d)", game, name, kind, score)
        return Finding(game=game, source="github", file_path=path, change_type="pulse", text=name, score=score,
                       keywords=keywords, token=f"pulse:{name}",
                       context=f"nodo Pulse mai visto ({kind}): una nuova capacità dello scripting del motore")
