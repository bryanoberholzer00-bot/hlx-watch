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
    una famiglia con un solo nome resta in attesa (non diventa nota) e viene segnalata quando arriva al secondo;
  - a una famiglia nuova con una classe sola si aggiungono i nomi che iniziano con la stessa parola senza la C
    (CCitadelModifierHandleTyped + CitadelHudEvent, Deadlock in Dota 2 nel 2021), purché siano al massimo
    `max_related_names` (5): oltre è una parola comune (Parse...). Trovato con la prova sul passato (backtest.py).
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
STATE_REPORTED = "classfam:reported"     # famiglie segnalate: quanti nomi avevano e con che punteggio
GREP = r"\bC_?[A-Z][A-Za-z0-9_]{4,}"
CLASS = re.compile(r"^C_?(?P<fam>[A-Z]{2,}(?=[A-Z][a-z]|_[A-Z])|[A-Z][a-z]{2,}[0-9]*)"
                   r"(?P<rest>(?:[A-Z][a-z]{2,}|[A-Z]{2,}(?=[A-Z][a-z]|_|$)|_|[0-9])[A-Za-z0-9_]*)$")
SCHEMA_FILE = re.compile(r"^DumpSource2/schemas/[^/]+/(?P<name>[A-Za-z0-9_]+)\.h$")
# dump delle stringhe di librerie non di Valve: le loro classi non dicono niente sui progetti di Valve
DEFAULT_EXCLUDE = ["*Qt5*", "*Qt6*", "*OpenImageDenoise*", "*SDL2*", "*SDL3*", "*embree*", "*cairo*", "*d3dcompiler*",
                   "*dbghelp*", "*amd_ags*", "*nvngx*", "*cuda*", "*tbb*", "*libcef*", "*chrome*", "*icu*", "*ffmpeg*",
                   "*avcodec*", "*avformat*", "*avutil*", "*swscale*", "*libcurl*", "*openssl*", "*libcrypto*",
                   "*libssl*", "*vulkan*", "*dxil*", "*dxcompiler*", "*steam_api*", "*libGLES*", "*libEGL*",
                   # strumenti Unity di SteamVR (assembly .NET, Steamworks.NET, runtime Mono), ImageMagick dentro
                   # imagetool, MFC negli strumenti HTC, Vulkan e Microsoft: dalla prova sul passato, 40 famiglie
                   # "nuove" in quattro mesi del 2022 venivano tutte da qui
                   "*_Data/Managed/*", "MonoBleedingEdge/**", "*UnityPlayer*", "*imagetool*", "*vivelink*",
                   "*AitH264Capture*", "*VkLayer*", "*msdia*", "*mfc*"]
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
        self.max_related = int(ccfg.get("max_related_greps", 40))   # ricerche dei nomi senza C, per repository
        self.max_related_names = int(ccfg.get("max_related_names", 5))
        # una famiglia già segnalata che cresce (almeno il doppio dei nomi e almeno growth_min in più) è un avviso nuovo,
        # più alto di growth_step punti fino a growth_max: un progetto vero cresce, il rumore di solito no
        self.growth_min = int(ccfg.get("growth_min", 5))
        self.growth_step = int(ccfg.get("growth_step", 25))
        self.growth_max = int(ccfg.get("growth_max", 95))
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
        self._related_names(repo_cfg, git, spec, names)

    def _related_names(self, repo_cfg: dict[str, Any], git: GitFn, spec: list[str], names: set[str]) -> None:
        """Una famiglia nuova con un nome solo cerca altri nomi che iniziano con la stessa parola, senza la C.
        Il 25 marzo 2021 Deadlock è entrato in Dota 2 con CCitadelModifierHandleTyped e CitadelHudEvent: una sola
        classe, ma due nomi del progetto Citadel. Solo per parole vere (almeno 4 lettere, con minuscole)."""
        known = self.known()
        if known is None:
            return                              # primo giro: si impara e basta
        fams = {f for f in (family(n) for n in names) if f and f not in known and f not in self.ignore}
        checked = 0
        for fam in sorted(fams):
            entry = self.seen_now.get(fam)
            word = fam[1:].lstrip("_")
            if not entry or len(entry["names"]) != 1 or len(word) < 4 or not re.search(r"[a-z]", word):
                continue
            checked += 1
            if checked > self.max_related:
                break
            proc = git(["-c", "grep.threads=4", "grep", "-h", "-o", "-I", "-E", "-e", rf"\b{word}[A-Z][A-Za-z0-9_]+",
                        "HEAD", "--", *spec])
            if proc.returncode not in (0, 1):
                continue
            related = {t for t in proc.stdout.decode("utf-8", "replace").split() if t}
            # una parola comune (Parse: ParseAndClassifyIdentifier e altri 20) non è il nome di un progetto
            if 0 < len(related) <= self.max_related_names:
                entry["names"].update(sorted(related)[:20])
                entry.setdefault("related", set()).update(related)

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
        out += self._growth(reported)
        return out

    # ---------------------------------------------------------------- crescita
    def reported(self) -> dict[str, dict[str, Any]]:
        """famiglia -> {"n": nomi, "score": punteggio, "names": [...]} delle famiglie segnalate. La prima volta si
        ricostruisce dalle voci già nell'archivio (CHLX e le altre segnalate prima di questa regola)."""
        raw = self.db.get_state(STATE_REPORTED)
        if raw is not None:
            return json.loads(raw)
        out: dict[str, dict[str, Any]] = {}
        for r in self.db.conn.execute("SELECT score, context FROM findings WHERE change_type = 'class_family' "
                                      "ORDER BY id").fetchall():
            try:
                ctx = json.loads(r["context"] or "{}")
            except ValueError:
                continue
            fam, names = ctx.get("famiglia"), ctx.get("nomi") or []
            if fam:
                out[fam] = {"n": int(ctx.get("n") or len(names)), "score": int(r["score"]), "names": names[:300]}
        self.db.set_state(STATE_REPORTED, json.dumps(out, ensure_ascii=False))
        return out

    def _remember_report(self, fam: str, names: list[str], score: int) -> None:
        rep = self.reported()
        rep[fam] = {"n": len(names), "score": score, "names": names[:300]}
        self.db.set_state(STATE_REPORTED, json.dumps(rep, ensure_ascii=False))

    def _growth(self, just_reported: set[str]) -> list[Finding]:
        out: list[Finding] = []
        rep = self.reported()
        for fam, entry in sorted(self.seen_now.items()):
            old = rep.get(fam)
            if old is None or fam in just_reported:
                continue
            n_old, n_now = int(old["n"]), len(entry["names"])
            if n_now < max(2 * n_old, n_old + self.growth_min):
                continue
            score = max(int(old["score"]), min(int(old["score"]) + self.growth_step, self.growth_max))
            out.append(self._finding(fam, entry, grown_from=n_old, score=score, old_names=set(old.get("names") or [])))
        return out

    def _finding(self, fam: str, entry: dict[str, Any], *, grown_from: int | None = None, score: int | None = None,
                 old_names: set[str] | None = None) -> Finding:
        names = sorted(entry["names"])
        games = sorted(entry["games"])
        keywords = ["class_family"]
        if score is None:
            score = self.score_high if len(names) >= self.names_high else self.score
            for name in names[:50]:
                res = self.matcher.analyze_line(name, file_path="", change_type="added")
                if res and res.score + 10 > score:
                    score, keywords = min(res.score + 10, 250), ["class_family", *res.keywords]
        game = games[0]
        repo_cfg = entry["games"][game]
        if grown_from is None:
            what, token = "nuova", f"classfam:{fam.lower()}"
            text = f"Famiglia di classi nuova: {fam} ({len(names)} nomi): {', '.join(names[:6])}"
        else:
            what, token = "in crescita", f"classfam:{fam.lower()}:crescita:{len(names)}"
            keywords.append("class_family_growth")
            fresh = [n for n in names if n not in (old_names or set())] or names
            text = (f"Famiglia di classi in crescita: {fam}, da {grown_from} a {len(names)} nomi. "
                    f"Nuovi: {', '.join(fresh[:10])}")
        build = BuildInfo(game=game, source="fullscan", ref=f"classfam:{fam}:{iso(utcnow())[:10]}", date=utcnow(),
                          url=f"https://github.com/{repo_cfg['repo']}/search?q={fam}",
                          message=f"famiglia di classi {what} {fam}")
        f = Finding(game=game, source="fullscan", file_path=f"classi {fam}*", change_type="class_family",
                    text=text[:500], score=score, keywords=keywords, token=token,
                    context=json.dumps({"famiglia": fam, "n": len(names), "nomi": names[:100], "giochi": games},
                                       ensure_ascii=False))
        f.build = build
        self.db.add_finding(f, self.db.upsert_build(build))
        self._remember_report(fam, names, score)
        log.warning("FAMIGLIA DI CLASSI %s %s (%d nomi, %s): %s", what.upper(), fam, len(names), ", ".join(games),
                    names[:4])
        return f
