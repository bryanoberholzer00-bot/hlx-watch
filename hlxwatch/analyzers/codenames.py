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
    # il messaggio di rete con i comandi del giocatore, uno per gioco: CDota2UserCmdPB, CCitadelUserCmdPB (Deadlock),
    # CSGOUserCmdPB e, in Dota 2 dal 9 giugno 2022 al 6 marzo 2023, CHLXUserCmdPB. Nei .proto e nei dump dei binari.
    ("usercmd", re.compile(r"(?<![a-z0-9_])C(?P<name>[A-Z][A-Za-z0-9]*?)UserCmdPB(?![A-Za-z0-9])"),
     "messaggio di rete con i comandi del giocatore (UserCmdPB)", None),
    ("vr_device", re.compile(r'"controller_type"\s*:\s*"(?P<name>[A-Za-z0-9_]+)"'),
     "tipo di dispositivo di SteamVR (controller_type)", None),
    # l'elenco dei dispositivi Valve nei driver di SteamVR, una riga per prodotto: "Valve Index HMD", "Valve SR",
    # "Valve VMinus1". Il 31 marzo 2022 ci è entrato "Valve Deckard", tre anni e mezzo prima di Steam Frame
    ("valve_device", re.compile(r"^\s*Valve (?P<name>[A-Z][A-Za-z0-9]+)\b"),
     "dispositivo Valve nei driver di SteamVR (Valve <nome>)", re.compile(r"^drivers/[^/]+/bin/.+_strings\.txt$", re.I)),
    # i tipi di controller del client di Steam: k_eControllerType_SteamControllerNeptune è comparso l'11 maggio 2021,
    # due mesi prima dello Steam Deck. I tipi Valve (Steam...) valgono più di quelli di altre marche
    ("controller_type_valve", re.compile(r"k_e(?:Steam)?(?:Controller|Input)Type_(?P<name>Steam[A-Za-z0-9]+)", re.I),
     "tipo di controller Valve del client di Steam (k_EControllerType_Steam...)", None),
    ("controller_type", re.compile(r"k_e(?:Steam)?(?:Controller|Input)Type_(?P<name>(?!steam)[A-Za-z0-9]+)", re.I),
     "tipo di controller del client di Steam (k_EControllerType_...)", None),
]
C_IN_NAME = {"csgo"}          # nomi in cui la C iniziale è del gioco (CSGOUserCmdPB), non il prefisso delle classi
# parole che compaiono in quei posti ma non sono nomi di progetto
GENERIC = {"base", "other", "unassigned", "default", "bugreporter", "none", "core", "tools", "---", "version", "owner",
           "severity", "priority", "category", "product", "component", "main", "bin", "sdk", "build", "src",
           "corporation", "corp", "software", "unknown", "count", "maximumpossiblevalue"}
# regex estesa per `git grep -i -E` della scansione completa: seleziona le righe dei posti
PREFILTER = (r'mod *[!=]=|"(product|component|mod)"|source2[\\/]main[\\/](content|game)[\\/]|buildworker[\\/]'
             r'|"controller_type"|UserCmdPB|^[[:space:]]*Valve [A-Z]|k_e(steam)?(controller|input)type_')
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
            if place == "usercmd" and ("c" + name) in C_IN_NAME:
                name = "c" + name                 # CSGOUserCmdPB: la C è del nome del gioco, non delle classi
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
        # tanti nomi nuovi nello stesso file insieme = un elenco comparso per la prima volta: una voce sola
        self.group_min = int(ccfg.get("group_min", 4))
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


def group_new_names(findings: list[Finding], min_group: int) -> list[Finding]:
    """Nomi nuovi dello stesso file: da `min_group` in su diventano una voce sola con l'elenco.

    L'11 maggio 2021 il client di Steam ha ricevuto tutto l'elenco dei tipi di controller in un colpo: 25 avvisi, e
    quello giusto (SteamControllerNeptune) si perdeva tra Xbox e PlayStation. Ora è un avviso solo, con il punteggio
    del nome più alto e tutti i nomi nel testo; i nomi restano imparati uno per uno."""
    if min_group < 2:
        return findings
    groups: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        if f.change_type == "codename" and "codename_new" in (f.keywords or []):
            groups.setdefault((f.game, f.file_path or ""), []).append(f)
    out: list[Finding] = []
    done: set[tuple[str, str]] = set()
    for f in findings:
        key = (f.game, f.file_path or "")
        group = groups.get(key) if f.change_type == "codename" and "codename_new" in (f.keywords or []) else None
        if group is None or len(group) < min_group:
            out.append(f)
            continue
        if key in done:
            continue
        done.add(key)
        group = sorted(group, key=lambda g: -g.score)
        names = [str(g.token or "").split(":", 1)[-1] for g in group]
        base = key[1].rsplit("/", 1)[-1] or "file"
        out.append(Finding(
            game=key[0], source=group[0].source, file_path=key[1], line_no=group[0].line_no, change_type="codename",
            text=f"elenco nuovo in {base}: {len(names)} nomi mai visti: {', '.join(names)}"[:500],
            score=group[0].score, keywords=["codename_new", "codename_group"],
            token="codenames:" + ",".join(sorted(names))[:200],
            context=f"il nome con il punteggio più alto: {group[0].text.strip()[:200]}",
        ))
    return out
