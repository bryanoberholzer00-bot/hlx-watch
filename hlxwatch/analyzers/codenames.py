"""Nomi di progetto mai visti nei posti dove Valve li scrive.

Le parole chiave trovano solo nomi già noti (hlx, hl3...). Qui invece si guardano i *posti* in cui compaiono
i nomi in codice dei progetti, qualunque siano:
  - le espressioni `mod != nome` / `mod == nome` degli strumenti (opzioni riservate a un progetto);
  - prodotti, componenti e sezioni della configurazione del bug reporter;
  - le cartelle di progetto nei percorsi `source2/main/content/<nome>` e `.../game/<nome>`;
  - i rami di compilazione nei percorsi della macchina di build che restano dentro i binari
    (`C:/buildworker/csgo_rel_win64/build/src/engine2`): un ramo nuovo non si può nascondere.
Un nome che non è nella lista dei noti (config `codenames.known` più quelli imparati) diventa una voce
"codename" ad alto punteggio. Alla prima scansione completa i nomi presenti vengono solo imparati, senza
avvisi (stato `codenames:learned`); da lì in poi ogni nome nuovo è un avviso.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

BUGREPORTER_FILE = re.compile(r"bugreporter/config\.txt$", re.IGNORECASE)
PLACES: list[tuple[str, re.Pattern[str], str, re.Pattern[str] | None]] = [
    ("mod", re.compile(r"\bmod\s*[!=]=\s*(?P<name>[A-Za-z0-9_]+)"), "espressione mod != / == degli strumenti", None),
    ("bugreporter", re.compile(r'"(?:product|component)"\s+"(?P<name>[^"]+)"', re.IGNORECASE),
     "prodotto o componente del bug reporter", BUGREPORTER_FILE),
    ("bugreporter_block", re.compile(r'^\s*"(?P<name>[A-Za-z0-9_ ]+)"\s*$'), "sezione del bug reporter", BUGREPORTER_FILE),
    ("content_dir", re.compile(r"source2[\\/]main[\\/](?:content|game)[\\/](?P<name>[A-Za-z0-9_]+)", re.IGNORECASE),
     "cartella di progetto nei percorsi degli strumenti", None),
    # KeyValues "mod" "nome": per esempio la tabella di importazione da Source 1 (source1import_txtmap.txt), dove
    # nel 2018 il modello dell'headcrab era assegnato al mod "hl3", oggi "hlvr"
    ("kv_mod", re.compile(r'"mod"\s+"(?P<name>[A-Za-z0-9_ -]+)"', re.IGNORECASE), 'chiave "mod" di un file KeyValues', None),
    # percorsi della macchina di compilazione Valve, conservati nei dump delle stringhe delle DLL
    # (<gioco>_strings.txt): il nome dopo buildworker\ è il ramo del progetto che ha prodotto il binario
    ("buildworker", re.compile(r"buildworker[\\/]+(?P<name>[A-Za-z0-9_.-]+)", re.IGNORECASE),
     "ramo di compilazione nei percorsi della macchina di build (buildworker)", None),
    # tipo di dispositivo nei profili dei driver di SteamVR: ogni visore o controller dichiara qui il suo nome
    # (deckard, frame_hmd, frame_controller, knuckles_ev1...). Il gruppo di Brad Lynch ha scoperto così l'hardware Valve
    ("vr_device", re.compile(r'"controller_type"\s*:\s*"(?P<name>[A-Za-z0-9_]+)"'),
     "tipo di dispositivo di SteamVR (controller_type)", None),
]
# parole che compaiono in quei posti ma non sono nomi di progetto
GENERIC = {"other", "unassigned", "default", "bugreporter", "none", "core", "tools", "---", "version", "owner",
           "severity", "priority", "category", "product", "component", "main", "bin", "sdk", "build", "src"}
# regex estesa per `git grep -i -E` della scansione completa: seleziona le righe dei tre posti
PREFILTER = r'mod *[!=]=|"(product|component|mod)"|source2[\\/]main[\\/](content|game)[\\/]|buildworker[\\/]|"controller_type"'
STATE_SEEN = "codenames:seen"
STATE_LEARNED = "codenames:learned"
STATE_LEARNED_PLACES = "codenames:learned_places"
# posti che esistevano prima dell'apprendimento per posto: chi aveva già concluso l'apprendimento li conosce tutti.
# Un posto aggiunto dopo impara in silenzio alla prima scansione completa, senza avvisi su ciò che c'era già
LEGACY_PLACES = ("mod", "bugreporter", "bugreporter_block", "content_dir", "kv_mod", "buildworker")


def normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def detect(text: str, file_path: str = "") -> list[tuple[str, str, str]]:
    """(posto, nome normalizzato, descrizione) per ogni nome di progetto che compare nella riga."""
    out: list[tuple[str, str, str]] = []
    for place, regex, desc, file_re in PLACES:
        if file_re is not None and not file_re.search(file_path or ""):
            continue
        for m in regex.finditer(text):
            name = normalize(m.group("name"))
            if len(name) < 2 or name.isdigit() or name in GENERIC:
                continue
            if (place, name) not in [(p, n) for p, n, _ in out]:
                out.append((place, name, desc))
    return out


class CodenameTracker:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        ccfg: dict[str, Any] = cfg.get("codenames", {}) or {}
        self.enabled = bool(ccfg.get("enabled", True))
        self.score = int(ccfg.get("score", 120))
        # punteggio per posto: un controller nuovo di un altro marchio non vale un progetto Valve nuovo
        self.place_scores = {str(k): int(v) for k, v in (ccfg.get("place_scores") or {}).items()}
        self.seen: dict[str, dict[str, Any]] = json.loads(db.get_state(STATE_SEEN) or "{}")
        self.known: set[str] = {normalize(str(n)) for n in (ccfg.get("known") or [])} | set(self.seen)

    @property
    def learned(self) -> bool:
        return self.db.get_state(STATE_LEARNED) == "1"

    def learned_places(self) -> set[str]:
        raw = self.db.get_state(STATE_LEARNED_PLACES)
        if raw is not None:
            return set(json.loads(raw))
        return set(LEGACY_PLACES) if self.learned else set()

    def finish_learning(self) -> None:
        if not self.learned:
            self.db.set_state(STATE_LEARNED, "1")
            log.info("Nomi di progetto: fase di apprendimento conclusa, %d nomi noti", len(self.known))
        places = {p for p, _, _, _ in PLACES}
        new_places = places - self.learned_places()
        if new_places or self.db.get_state(STATE_LEARNED_PLACES) is None:
            self.db.set_state(STATE_LEARNED_PLACES, json.dumps(sorted(places)))
            if new_places:
                log.info("Nomi di progetto: apprendimento concluso anche per %s", ", ".join(sorted(new_places)))

    def _refresh(self) -> None:
        """Rilegge i nomi salvati: più tracker nello stesso run condividono lo stato nel database."""
        stored = json.loads(self.db.get_state(STATE_SEEN) or "{}")
        self.seen.update(stored)
        self.known |= set(stored)

    def _remember(self, name: str, info: dict[str, Any]) -> None:
        self._refresh()               # si scrive sempre l'unione, mai la copia in memoria da sola
        self.seen[name] = info
        self.known.add(name)
        self.db.set_state(STATE_SEEN, json.dumps(self.seen, ensure_ascii=False))

    def check(self, text: str, file_path: str, *, game: str, change_type: str = "added") -> Finding | None:
        """Registra i nomi nuovi; dopo l'apprendimento restituisce una voce per il primo nome mai visto."""
        if not self.enabled:
            return None
        for place, name, desc in detect(text, file_path):
            if name in self.known:
                continue
            self._refresh()           # un altro tracker (commit o scansione completa) può averlo già imparato
            if name in self.known:
                continue
            info = {"posto": place, "gioco": game, "file": file_path, "quando": iso(utcnow())}
            if place not in self.learned_places():
                # in apprendimento impara solo la scansione del contenuto: un nome visto in un commit prima
                # che l'apprendimento finisca non va tacitato, lo ritroverà la scansione completa (presente)
                # oppure, se è già sparito, resterà sconosciuto e verrà segnalato alla prossima comparsa
                if change_type != "present":
                    return None
                self._remember(name, info)
                log.info("[%s] nome di progetto imparato: '%s' (%s)", game, name, desc)
                continue
            self._remember(name, info)
            log.warning("[%s] NOME DI PROGETTO MAI VISTO: '%s' in %s (%s)", game, name, file_path, desc)
            return Finding(
                game=game, source="github", file_path=file_path, change_type="codename",
                text=text.strip()[:500], score=self.place_scores.get(place, self.score), keywords=["codename_new"],
                token=f"codename:{name}",
                context=f"nome di progetto '{name}' mai visto: {desc}",
            )
        return None
