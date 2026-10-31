"""Famiglie di classi nuove: un gruppo di nomi C++ con un prefisso mai visto, anche senza parole chiave.

Le tracce HLX del 29 ottobre 2026 in CS2 e Dota 2 erano una famiglia: CHLXCoverPreviewHelper,
CHLXBaseProjectileEmitterHelper... Un progetto con un nome in codice nuovo (come era Citadel per Deadlock) porterebbe
una famiglia allo stesso modo, ma senza la parola HLX le parole chiave non lo vedono.

Nella scansione completa giornaliera si leggono i nomi di classe di tutti i giochi seguiti: i file degli schemi
(DumpSource2/schemas/*/<Classe>.h) e i dump delle stringhe dei binari (*_strings.txt, escluse le librerie di terzi).
La famiglia di un nome è il primo pezzo dopo la C: CHLXCoverPreviewHelper -> CHLX, CCitadelPlayer -> CCitadel,
CMathCounter_API -> CMath. Si tengono solo nomi di classe veri (almeno due parole, con minuscole): niente costanti come
CALG_AES né frammenti casuali dei binari. Una famiglia è nota se compare in qualunque gioco.
  - prima scansione: impara tutte le famiglie, senza avvisi;
  - poi una famiglia mai vista con almeno `min_names` nomi diversi (2) è una voce; con `names_high` nomi (5) vale di più;
    una famiglia con un solo nome resta in attesa (non diventa nota) e viene segnalata quando arriva al secondo.
Provato su CS2 da luglio a fine ottobre 2026: 4 famiglie nuove con almeno 2 nomi (CHLX, e CMath, CTank, CRule delle
entità di gioco con API di scripting), contro 99 con un filtro grezzo.
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

STATE = "classfam:known"
GREP = r"\bC_?[A-Z][A-Za-z0-9_]{4,}"
CLASS = re.compile(r"^C_?(?P<fam>[A-Z]{2,}(?=[A-Z][a-z]|_[A-Z])|[A-Z][a-z]{2,}[0-9]*)"
                   r"(?P<rest>(?:[A-Z][a-z]{2,}|[A-Z]{2,}(?=[A-Z][a-z]|_|$)|_|[0-9])[A-Za-z0-9_]*)$")
SCHEMA_FILE = re.compile(r"^DumpSource2/schemas/[^/]+/(?P<name>[A-Za-z0-9_]+)\.h$")
# dump delle stringhe di librerie non di Valve: le loro classi non dicono niente sui progetti di Valve
DEFAULT_EXCLUDE = ["*Qt5*", "*Qt6*", "*OpenImageDenoise*", "*SDL2*", "*SDL3*", "*embree*", "*cairo*", "*d3dcompiler*",
                   "*dbghelp*", "*amd_ags*", "*nvngx*", "*cuda*", "*tbb*", "*libcef*", "*chrome*", "*icu*", "*ffmpeg*",
                   "*avcodec*", "*avformat*", "*avutil*", "*swscale*", "*libcurl*", "*openssl*", "*libcrypto*",
                   "*libssl*", "*vulkan*", "*dxil*", "*dxcompiler*", "*steam_api*", "*libGLES*", "*libEGL*"]
DEFAULT_IGNORE = ["CUDA", "CURL", "CLSID"]

GitFn = Callable[[list[str]], Any]


def family(name: str) -> str | None:
    """CHLXCoverPreviewHelper -> CHLX; None se il nome non sembra una classe vera."""
    m = CLASS.match(name)
    if not m or not re.search(r"[a-z]{2,}", m.group("rest")):
        return None
    return "C" + m.group("fam")


class ClassFamilyTracker:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        ccfg: dict[str, Any] = cfg.get("class_families", {}) or {}
        self.enabled = bool(ccfg.get("enabled", True))
        self.min_names = int(ccfg.get("min_names", 2))
        self.names_high = int(ccfg.get("names_high", 5))
        self.score = int(ccfg.get("score", 55))
        self.score_high = int(ccfg.get("score_high", 80))
        self.exclude = list(ccfg.get("exclude_files") or DEFAULT_EXCLUDE)
        self.ignore = set(ccfg.get("ignore") or DEFAULT_IGNORE)
        self.matcher = KeywordMatcher(cfg.keywords)
        # famiglia -> {"names": set, "games": {gioco: repo_cfg}} in questa scansione completa
        self.seen_now: dict[str, dict[str, Any]] = {}

    def known(self) -> set[str] | None:
        raw = self.db.get_state(STATE)
        return None if raw is None else set(json.loads(raw))

    def add(self, game: str, repo_cfg: dict[str, Any], names: set[str]) -> None:
        for name in names:
            fam = family(name)
            if fam is None or fam in self.ignore:
                continue
            entry = self.seen_now.setdefault(fam, {"names": set(), "games": {}})
            entry["names"].add(name)
            entry["games"].setdefault(game, repo_cfg)

    def collect(self, repo_cfg: dict[str, Any], git: GitFn) -> None:
        if not self.enabled:
            return
        names: set[str] = set()
        listing = git(["ls-tree", "-r", "--name-only", "HEAD", "DumpSource2/schemas"])
        if listing.returncode == 0:
            for path in listing.stdout.decode("utf-8", "replace").splitlines():
                m = SCHEMA_FILE.match(path)
                if m:
                    names.add(m.group("name"))
        spec = ["*_strings.txt", *(f":(exclude,glob)**/{p}" for p in self.exclude)]
        proc = git(["-c", "grep.threads=4", "grep", "-h", "-o", "-I", "-E", "-e", GREP, "HEAD", "--", *spec])
        if proc.returncode in (0, 1):
            names.update(proc.stdout.decode("utf-8", "replace").split())
        else:
            log.warning("[%s] famiglie di classi: git grep fallito", repo_cfg["name"])
        self.add(repo_cfg["name"], repo_cfg, names)

    def finish(self) -> list[Finding]:
        if not self.enabled or not self.seen_now:
            return []
        known = self.known()
        if known is None:
            self.db.set_state(STATE, json.dumps(sorted(self.seen_now)))
            log.info("Famiglie di classi: %d imparate, senza avvisi", len(self.seen_now))
            return []
        out: list[Finding] = []
        reported: set[str] = set()
        for fam in sorted(set(self.seen_now) - known):
            entry = self.seen_now[fam]
            if len(entry["names"]) < self.min_names:
                continue                        # un nome solo: resta in attesa, non diventa nota
            out.append(self._finding(fam, entry))
            reported.add(fam)
        if reported:
            self.db.set_state(STATE, json.dumps(sorted(known | reported)))
        return out

    def _finding(self, fam: str, entry: dict[str, Any]) -> Finding:
        names = sorted(entry["names"])
        games = sorted(entry["games"])
        score = self.score_high if len(names) >= self.names_high else self.score
        keywords = ["class_family"]
        for name in names[:50]:
            res = self.matcher.analyze_line(name, file_path="", change_type="added")
            if res and res.score + 10 > score:
                score, keywords = min(res.score + 10, 250), ["class_family", *res.keywords]
        game = games[0]
        repo_cfg = entry["games"][game]
        text = f"Famiglia di classi nuova: {fam} ({len(names)} nomi): {', '.join(names[:6])}"
        build = BuildInfo(game=game, source="fullscan", ref=f"classfam:{fam}:{iso(utcnow())[:10]}", date=utcnow(),
                          url=f"https://github.com/{repo_cfg['repo']}/search?q={fam}",
                          message=f"famiglia di classi nuova {fam}")
        f = Finding(game=game, source="fullscan", file_path=f"classi {fam}*", change_type="class_family",
                    text=text[:500], score=score, keywords=keywords, token=f"classfam:{fam.lower()}",
                    context=json.dumps({"famiglia": fam, "nomi": names[:100], "giochi": games}, ensure_ascii=False))
        f.build = build
        self.db.add_finding(f, self.db.upsert_build(build))
        log.warning("FAMIGLIA DI CLASSI NUOVA %s (%d nomi, %s): %s", fam, len(names), ", ".join(games), names[:4])
        return f
