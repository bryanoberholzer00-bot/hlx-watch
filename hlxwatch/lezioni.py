"""Registro degli errori: gli errori già corretti non devono tornare.

Ogni errore corretto (a mano o approvato dalla revisione settimanale) diventa un esempio con il risultato giusto:
il tipo di una traccia, le parole chiave di una riga, la riga "Cosa cambia" di una build, un esito scritto a mano,
un avviso silenziato. A ogni giro gli esempi si ricontrollano con le regole di quel momento (config, codice e
correzioni approvate). Se una modifica fa tornare un errore, arriva un avviso su Telegram, e un altro quando
torna a posto. L'esito resta nello stato (lezioni:controllo) e compare sul sito; l'elenco è in Metodologia.

Gli esempi scritti a mano sono in lezioni.yaml; quelli delle correzioni approvate nello stato lezioni:imparate.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

FILE = Path(__file__).with_name("lezioni.yaml")
STATE = "lezioni:controllo"
LEARNED = "lezioni:imparate"
TITLE_FAIL = "HLX Watch: è tornato un errore già corretto"
TITLE_OK = "HLX Watch: gli errori già corretti sono di nuovo a posto"


def static_lessons() -> list[dict[str, Any]]:
    data = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    return list(data.get("lezioni") or [])


def learned(db: Database | None) -> list[dict[str, Any]]:
    if db is None:
        return []
    try:
        data = json.loads(db.get_state(LEARNED) or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def add_learned(db: Database, lesson: dict[str, Any]) -> None:
    items = [x for x in learned(db) if x.get("id") != lesson.get("id")]
    items.append(lesson)
    db.set_state(LEARNED, json.dumps(items, ensure_ascii=False))


def all_lessons(db: Database | None) -> list[dict[str, Any]]:
    return static_lessons() + learned(db)


def _names(items: Any) -> list[str]:
    return [str(x) for x in (items or [])]


class Checker:
    def __init__(self, cfg: Config, db: Database | None = None):
        self.cfg = cfg
        self.db = db
        self._matcher = None
        if db is not None:                    # le correzioni approvate fanno parte delle regole da controllare
            from hlxwatch.revisione import load
            load(db)

    @property
    def matcher(self) -> Any:
        if self._matcher is None:
            from hlxwatch.analyzers import KeywordMatcher
            self._matcher = KeywordMatcher(self.cfg.get("keywords", {}) or {})
        return self._matcher

    def check(self, lesson: dict[str, Any]) -> str | None:
        """None se l'esempio è rispettato, altrimenti che cosa non va (in italiano)."""
        kind = lesson.get("controllo")
        if kind == "tipo":
            from hlxwatch.fase import trace_kind
            got = trace_kind(lesson.get("token"), lesson.get("testo"), _names(lesson.get("parole"))) or "nessuno"
            want = str(lesson.get("atteso") or "nessuno")
            return None if got == want else f"tipo {got} invece di {want}"
        if kind == "parola":
            res = self.matcher.analyze_line(str(lesson.get("testo") or ""), file_path=str(lesson.get("file") or ""))
            found = set(res.keywords) if res else set()
            score = res.score if res else 0
            problems = [f"manca la parola chiave {k}" for k in _names(lesson.get("contiene")) if k not in found]
            problems += [f"c'è la parola chiave {k}" for k in _names(lesson.get("non_contiene")) if k in found]
            if lesson.get("punteggio_max") is not None and score > int(lesson["punteggio_max"]):
                problems.append(f"punteggio {score}, il massimo è {lesson['punteggio_max']}")
            if lesson.get("punteggio_min") is not None and score < int(lesson["punteggio_min"]):
                problems.append(f"punteggio {score}, il minimo è {lesson['punteggio_min']}")
            return "; ".join(problems) or None
        if kind == "app_nuova":
            from hlxwatch.sources.steam_webapi import name_rule
            score, _ = name_rule(self.cfg.get("steam.new_apps", {}) or {}, lesson.get("nome"),
                                 lesson.get("sviluppatore"), lesson.get("editore"))
            problems = []
            if lesson.get("punteggio_max") is not None and score > int(lesson["punteggio_max"]):
                problems.append(f"punteggio del nome {score}, il massimo è {lesson['punteggio_max']}")
            if lesson.get("punteggio_min") is not None and score < int(lesson["punteggio_min"]):
                problems.append(f"punteggio del nome {score}, il minimo è {lesson['punteggio_min']}")
            return "; ".join(problems) or None
        if kind == "file_sorvegliato":
            from hlxwatch.sources.github_tracking import watched_line, watched_rules
            got = "voce" if watched_line(watched_rules(self.cfg), str(lesson.get("file") or ""), str(lesson.get("testo") or "")) else "niente"
            want = str(lesson.get("atteso") or "niente")
            return None if got == want else f"{got} invece di {want}"
        if kind == "cosa_cambia":
            from hlxwatch.build_notes import describe
            got = describe(list(lesson.get("files") or []))
            want = _names(lesson.get("atteso"))
            return None if got == want else f"«{'; '.join(got)}» invece di «{'; '.join(want)}»"
        if kind == "esito":
            from hlxwatch.outcomes import manual_entries
            match = str(lesson.get("match") or "").lower()
            entries = manual_entries(self.cfg, self.db)
            entry = entries.get(match)
            if entry is None:
                return f"manca l'esito scritto a mano per «{match}»"
            want = str(lesson.get("atteso") or "")
            return None if not want or entry.get("esito") == want else f"esito «{entry.get('esito')}» invece di «{want}»"
        if kind == "avviso":
            from hlxwatch.revisione import silenced
            from hlxwatch.models import Finding
            finding = Finding(game=str(lesson.get("gioco") or ""), source="github", file_path=str(lesson.get("file") or ""),
                              change_type=str(lesson.get("tipo_modifica") or "added"),
                              text=str(lesson.get("testo") or ""), score=int(lesson.get("punteggio") or 0),
                              keywords=_names(lesson.get("parole")), token=lesson.get("token"))
            got = "silenziato" if self.db is not None and silenced(self.db, finding) else "avvisa"
            want = str(lesson.get("atteso") or "silenziato")
            return None if got == want else f"{got} invece di {want}"
        return f"controllo sconosciuto: {kind}"

    def run(self, lessons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        lessons = all_lessons(self.db) if lessons is None else lessons
        failed = []
        for lesson in lessons:
            try:
                problem = self.check(lesson)
            except Exception as exc:              # un esempio scritto male non deve fermare gli altri
                problem = f"errore nel controllo: {type(exc).__name__}: {exc}"
            if problem:
                failed.append({"id": lesson.get("id"), "errore": lesson.get("errore"), "problema": problem})
        return {"at": iso(utcnow()), "n": len(lessons), "imparate": len(learned(self.db)), "ok": not failed,
                "falliti": failed}


def state(db: Database) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(STATE) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def run_check(cfg: Config, db: Database, notifiers: list) -> dict[str, Any]:
    """Ricontrolla gli esempi, salva l'esito e avvisa quando un errore torna o quando tutto torna a posto."""
    from hlxwatch.notifiers import notify_text
    before = state(db) or {}
    result = Checker(cfg, db).run()
    db.set_state(STATE, json.dumps(result, ensure_ascii=False))
    old = {f["id"] for f in before.get("falliti") or []}
    new = [f for f in result["falliti"] if f["id"] not in old]
    if result["falliti"]:
        log.warning("Registro degli errori: %d esempi su %d non rispettati", len(result["falliti"]), result["n"])
        if new:
            notify_text(notifiers, TITLE_FAIL, "Una modifica ha fatto tornare errori già corretti:\n" + "\n".join(
                f"- {f['errore']}\n  ora: {f['problema']}" for f in new))
    else:
        log.info("Registro degli errori: %d esempi, tutti rispettati", result["n"])
        if before.get("ok") is False:
            notify_text(notifiers, TITLE_OK, f"Tutti i {result['n']} esempi del registro degli errori sono di nuovo rispettati.")
    return result
