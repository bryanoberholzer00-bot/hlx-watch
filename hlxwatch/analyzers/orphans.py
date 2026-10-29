"""Entità e helper di Hammer "senza padrone": definiti nel motore condiviso ma non usati da nessun gioco uscito.

Valve sviluppa tutti i giochi su un solo ramo di Source 2. Le definizioni delle entità per l'editor stanno in
`game/core/*.fgd` e arrivano in ogni gioco, anche quando servono a un progetto non annunciato; le classi C++ che
le implementano invece stanno solo nel gioco che le usa (SteamTracking le elenca in
`DumpSource2/entities/server.fgd` e `client.fgd`). Hammer stesso fa questo controllo
("%d fgd class(es) have no c++ entity class"). Qui lo si rifà su tutti i giochi insieme:
  - un'entità del motore condiviso senza classe in nessun gioco è "senza padrone";
  - un helper dell'editor (`GetAttributeTable_C...Helper` nel dump di Hammer) mai visto è un aiuto per
    un'entità nuova: le classi HLX di settembre 2026 erano helper.
La prima scansione completa impara gli elenchi senza avvisi (stato `orphans:entities` e `orphans:helpers`); poi
ogni entità senza padrone o helper mai visto diventa una voce "orphan", una volta sola.
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

CLASS_RE = re.compile(r'^@(?P<kind>\w+)Class\b[^=\n]*(?:\n[^@=\n]*)*?=\s*(?P<name>[A-Za-z0-9_]+)(?:\s*:\s*"(?P<desc>[^"]*)")?',
                      re.MULTILINE)
HELPER_RE = re.compile(r"GetAttributeTable_(C[A-Za-z0-9_]*Helper)\b")
CORE_FGD = re.compile(r"^game/core/.*\.fgd$")
ENTITY_DUMPS = ("DumpSource2/entities/server.fgd", "DumpSource2/entities/client.fgd")
HAMMER_DUMP = re.compile(r"(^|/)tools/hammer_strings\.txt$")
NOT_ENTITIES = {"base", "override"}            # classi base e ritocchi: non sono entità piazzabili
STATE_ENTITIES = "orphans:entities"
STATE_HELPERS = "orphans:helpers"

GitFn = Callable[[list[str]], Any]


def parse_classes(text: str) -> dict[str, tuple[str, str]]:
    """nome (minuscolo) -> (tipo, descrizione) per ogni classe di una FGD."""
    out: dict[str, tuple[str, str]] = {}
    for m in CLASS_RE.finditer(text):
        out.setdefault(m.group("name").lower(), (m.group("kind"), (m.group("desc") or "").strip()))
    return out


class OrphanTracker:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        ocfg: dict[str, Any] = cfg.get("orphans", {}) or {}
        self.enabled = bool(ocfg.get("enabled", True))
        self.score_entity = int(ocfg.get("score_entity", 100))
        self.score_helper = int(ocfg.get("score_helper", 90))
        self.matcher = KeywordMatcher(cfg.keywords)
        self.core: dict[str, dict[str, Any]] = {}       # entità del motore condiviso: dove sono definite
        self.implemented: set[str] = set()                # classi C++ di tutti i giochi
        self.helpers: dict[str, dict[str, Any]] = {}
        self.dumps_seen = 0

    # ------------------------------------------------------------ raccolta
    def collect(self, repo_cfg: dict[str, Any], git: GitFn) -> None:
        """Legge FGD condivise, elenco delle classi C++ e dump di Hammer dall'ultimo commit di un repository."""
        if not self.enabled:
            return
        listing = git(["ls-tree", "-r", "--name-only", "HEAD"])
        files = listing.stdout.decode("utf-8", "replace").splitlines() if listing.returncode == 0 else []

        def show(path: str) -> str:
            proc = git(["show", f"HEAD:{path}"])
            return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else ""

        name = repo_cfg["name"]
        for path in (f for f in files if CORE_FGD.match(f)):
            for cls, (kind, desc) in parse_classes(show(path)).items():
                self.core.setdefault(cls, {"kind": kind, "desc": desc, "game": name, "path": path, "repo_cfg": repo_cfg})
        for path in (f for f in files if f in ENTITY_DUMPS):
            self.implemented |= set(parse_classes(show(path)))
            self.dumps_seen += 1
        for path in (f for f in files if HAMMER_DUMP.search(f)):
            for helper in set(HELPER_RE.findall(show(path))):
                self.helpers.setdefault(helper, {"game": name, "path": path, "repo_cfg": repo_cfg})

    # ------------------------------------------------------------ confronto
    def orphans(self) -> dict[str, dict[str, Any]]:
        return {n: info for n, info in self.core.items()
                if info["kind"].lower() not in NOT_ENTITIES and n not in self.implemented}

    def finish(self) -> list[Finding]:
        """Dopo una scansione completa di tutti i repository: voci per le entità senza padrone e gli helper nuovi."""
        if not self.enabled or not self.dumps_seen or not self.core:
            return []                  # senza l'elenco delle classi C++ non si può dire che cosa manca
        orphans, helpers = self.orphans(), self.helpers
        known_e, known_h = self.db.get_state(STATE_ENTITIES), self.db.get_state(STATE_HELPERS)
        out: list[Finding] = []
        if known_e is None:
            log.info("Entità senza padrone: %d imparate (su %d del motore condiviso), senza avvisi", len(orphans), len(self.core))
        else:
            for name in sorted(set(orphans) - set(json.loads(known_e))):
                info = orphans[name]
                text = (f"entità senza padrone: {name} ({info['kind']}Class) definita in {info['path']}, "
                        f"nessun gioco uscito ha la sua classe" + (f". Descrizione: {info['desc'][:200]}" if info["desc"] else ""))
                out.append(self._finding(info, text, f"orphan:{name}", self.score_entity, "orphan_entity",
                                         f"{name} {info['desc']}"))
        if known_h is None:
            log.info("Helper di Hammer: %d imparati, senza avvisi", len(helpers))
        else:
            for name in sorted(set(helpers) - set(json.loads(known_h))):
                info = helpers[name]
                text = f"helper di Hammer mai visto: {name} (aiuto dell'editor per un'entità, in {info['path']})"
                out.append(self._finding(info, text, f"hammerhelper:{name}", self.score_helper, "hammer_helper_new", name))
        # si salva l'unione: un'entità che torna senza padrone dopo essere stata implementata non è una novità
        self.db.set_state(STATE_ENTITIES, json.dumps(sorted(set(orphans) | set(json.loads(known_e or "[]")))))
        self.db.set_state(STATE_HELPERS, json.dumps(sorted(set(helpers) | set(json.loads(known_h or "[]")))))
        return out

    def _finding(self, info: dict[str, Any], text: str, token: str, score: int, keyword: str, probe: str) -> Finding:
        repo_cfg, game = info["repo_cfg"], info["game"]
        keywords = [keyword]
        res = self.matcher.analyze_line(probe, file_path=info["path"], change_type="added")
        if res:
            score = min(max(score, res.score + 20), 250)
            keywords += res.keywords
        branch = repo_cfg.get("branch", "master")
        build = BuildInfo(game=game, source="fullscan", ref=f"orphans:{game}:{iso(utcnow())[:10]}", date=utcnow(),
                          url=f"https://github.com/{repo_cfg['repo']}/blob/{branch}/{info['path']}",
                          message="entità e helper senza padrone")
        build_id = self.db.upsert_build(build)
        f = Finding(game=game, source="fullscan", file_path=info["path"], change_type="orphan", text=text[:500],
                    score=score, keywords=keywords, token=token, context="controllo delle entità senza padrone")
        f.build = build
        self.db.add_finding(f, build_id)
        log.warning("[%s] %s (punteggio %d)", game, text[:160], score)
        return f
