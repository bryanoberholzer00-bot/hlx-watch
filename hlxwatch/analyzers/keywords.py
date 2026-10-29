"""Matching delle parole chiave e calcolo del punteggio di rilevanza."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from hlxwatch.analyzers.enrich import convar_bonus, fgd_entity_name, is_path_like

log = logging.getLogger(__name__)

TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./\\-")
MAX_SCORE = 250

DEFAULT_BONUSES = {"new_file": 20, "removed_file": 10, "fgd_entity": 25, "convar": 15, "path": 15}
DEFAULT_SEVERITY = {"high": 100, "medium": 50}


@dataclass
class Keyword:
    id: str
    regex: re.Pattern[str]
    weight: int
    description: str = ""
    excludes: list[re.Pattern[str]] = field(default_factory=list)
    path_excludes: list[re.Pattern[str]] = field(default_factory=list)    # file in cui la parola non conta


@dataclass
class Match:
    keyword: Keyword
    start: int
    end: int


@dataclass
class LineResult:
    score: int
    keywords: list[str]
    token: str | None
    bonuses: list[str]


def _compile(pattern: str, case_sensitive: bool = False) -> re.Pattern[str]:
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)


def mixed_case(word: str) -> bool:
    """True per 'hLX' o 'HLx'; False per 'hlx', 'HLX', 'Hlx', 'hlx_Foo' (parti separate da _ valutate a sé)."""
    for part in re.split(r"[_\-\s]+", word):
        letters = [ch for ch in part if ch.isalpha()]
        if len(letters) < 2:
            continue
        joined = "".join(letters)
        if joined.islower() or joined.isupper() or joined == joined.capitalize():
            continue
        return True
    return False


STRINGS_DUMP_RE = re.compile(r"(_strings\.txt$|(^|/)strings/)", re.IGNORECASE)
MACHINE_CODE_RE = re.compile(r"[A-Za-z0-9\\@`]\$[A-Za-z]")
ALLOWED_PUNCT = frozenset(" \t\"'_./\\-:=,;()[]{}<>!&|#*+%$")


def looks_like_garbage(text: str) -> bool:
    """Righe dei dump di stringhe binarie: poche lettere/cifre, troppe sequenze di escape,
    oppure frammenti corti pieni di simboli (es. '?Hl3@')."""
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    # frammenti di codice macchina nei dump dei binari: '$' attaccato a lettere o cifre (D$HL3, t$HL3, \$HL3).
    # Nei testi veri '$' segue uno spazio o una virgoletta ($basetexture, "$HOME")
    if len(stripped) < 40 and MACHINE_CODE_RE.search(stripped):
        return True
    alnum = sum(ch.isalnum() for ch in stripped)
    if alnum / len(stripped) < 0.45 or stripped.count("\\") >= 4:
        return True
    weird = sum(1 for ch in stripped if not ch.isalnum() and ch not in ALLOWED_PUNCT)
    letters = sum(ch.isalpha() for ch in stripped)
    return weird >= 1 and letters < 6


def bare_fragment(text: str, matches: list[Match]) -> bool:
    """Nei dump di stringhe dei binari (steamclient.txt, *_strings.txt) una riga cortissima fatta solo della
    parola chiave e di simboli ('hlx)', 't(hlx', '#hl3') è una sequenza casuale di byte, non una stringa."""
    stripped = text.strip()
    if len(stripped) > 8:
        return False
    best = max(matches, key=lambda m: m.end - m.start)
    outside = stripped[: max(0, best.start - (len(text) - len(text.lstrip())))]
    outside += stripped[best.end - (len(text) - len(text.lstrip())):]
    return sum(ch.isalpha() for ch in outside) <= 1


class KeywordMatcher:
    """Applica le regex configurate a una riga e produce punteggio, keyword e token."""

    def __init__(self, keyword_cfg: dict[str, Any], ignore_ids: Iterable[str] = ()):
        self.keywords: list[Keyword] = []
        for item in keyword_cfg.get("items", []) or []:
            try:
                kw = Keyword(
                    id=str(item["id"]),
                    regex=_compile(item["pattern"], bool(item.get("case_sensitive", False))),
                    weight=int(item.get("weight", 10)),
                    description=item.get("description", ""),
                    excludes=[_compile(x) for x in item.get("excludes", []) or []],
                    path_excludes=[_compile(x) for x in item.get("path_excludes", []) or []],
                )
            except (KeyError, re.error) as exc:
                log.error("Keyword non valida %r: %s", item, exc)
                continue
            self.keywords.append(kw)
        codename_weight = int(keyword_cfg.get("codename_weight", 30))
        for name in keyword_cfg.get("codenames", []) or []:
            name = str(name).strip()
            if not name:
                continue
            # i bordi escludono anche il backslash: nei dump di stringhe "tF\tF" non è un nome in codice
            self.keywords.append(Keyword(
                id=f"codename:{name.lower()}",
                regex=re.compile(rf"(?<![a-z0-9\\]){re.escape(name)}(?![a-z0-9\\])", re.IGNORECASE),
                weight=codename_weight,
                description=f"nome in codice '{name}'",
            ))
        self.global_excludes = [_compile(x) for x in keyword_cfg.get("global_excludes", []) or []]
        self.bonuses = {**DEFAULT_BONUSES, **(keyword_cfg.get("bonuses") or {})}
        self.severity = {**DEFAULT_SEVERITY, **(keyword_cfg.get("severity") or {})}
        # i bonus (file nuovo, entità FGD, convar, percorso) valgono solo per parole chiave almeno così forti:
        # due bonus su "npc_" (15) non devono trasformare un file di eroi di Dota in un avviso
        self.bonus_min_weight = int(keyword_cfg.get("bonus_min_weight", 40))
        self.ignore = {str(x) for x in ignore_ids}

    # ------------------------------------------------------------------ base
    def find(self, text: str, file_path: str = "") -> list[Match]:
        matches: list[Match] = []
        for kw in self.keywords:
            if kw.id in self.ignore:
                continue
            if any(ex.search(text) for ex in kw.excludes):
                continue
            if file_path and any(ex.search(file_path) for ex in kw.path_excludes):
                continue
            # la prima corrispondenza con maiuscole normali: "hLX", "HLx" sono sequenze casuali dei dump binari
            m = next((c for c in kw.regex.finditer(text) if not mixed_case(c.group(0))), None)
            if m is None:
                continue
            matches.append(Match(kw, m.start(), m.end()))
        if matches and self.global_excludes:
            best = max(m.keyword.weight for m in matches)
            if best < self.severity["high"] and any(ex.search(text) for ex in self.global_excludes):
                return []
        return matches

    @staticmethod
    def base_score(matches: list[Match]) -> int:
        weights = sorted((m.keyword.weight for m in matches), reverse=True)
        if not weights:
            return 0
        return weights[0] + sum(int(w * 0.25) for w in weights[1:])

    def severity_label(self, score: int) -> str:
        if score >= self.severity["high"]:
            return "high"
        if score >= self.severity["medium"]:
            return "medium"
        return "low"

    @staticmethod
    def extract_token(text: str, match: Match) -> str | None:
        """Espande il match fino ai bordi dell'identificatore (es. 'hlx_fsr3_min_reactiveness' o 'maps/hlx_01.vpk')."""
        s, e = match.start, match.end
        while s > 0 and text[s - 1] in TOKEN_CHARS:
            s -= 1
        while e < len(text) and text[e] in TOKEN_CHARS:
            e += 1
        token = text[s:e].strip("./\\-")
        if not token:
            return None
        return token[:160]

    # --------------------------------------------------------------- analisi
    def analyze_line(self, text: str, *, file_path: str = "", change_type: str = "added") -> LineResult | None:
        """Ritorna None se la riga non contiene nulla di interessante."""
        if not text or len(text) > 20000:
            return None
        matches = self.find(text, file_path)
        if not matches:
            return None
        if STRINGS_DUMP_RE.search(file_path) and bare_fragment(text, matches):
            return None
        score = self.base_score(matches)
        if score < self.severity["high"] and looks_like_garbage(text):
            return None
        bonuses: list[str] = []
        best = max(matches, key=lambda m: m.keyword.weight)
        token = self.extract_token(text, best)
        entity = fgd_entity_name(text) if file_path.lower().endswith(".fgd") else None
        if entity:
            token = entity

        if best.keyword.weight >= self.bonus_min_weight:
            if change_type in ("file_added",):
                score += self.bonuses["new_file"]
                bonuses.append("new_file")
            elif change_type == "file_removed":
                score += self.bonuses["removed_file"]
                bonuses.append("removed_file")
            if entity:
                score += self.bonuses["fgd_entity"]
                bonuses.append("fgd_entity")
            if convar_bonus(file_path, text):
                score += self.bonuses["convar"]
                bonuses.append("convar")
            if token and is_path_like(token):
                score += self.bonuses["path"]
                bonuses.append("path")

        return LineResult(
            score=min(score, MAX_SCORE),
            keywords=[m.keyword.id for m in matches],
            token=token,
            bonuses=bonuses,
        )
