"""Cronologia HLX: gli indizi importanti in ordine di data, ognuno con una spiegazione in italiano semplice.

Le voci ad alta rilevanza (punteggio >= timeline.min_score, 100 di default) si raggruppano per testo
normalizzato: la stessa riga in CS2, Dota 2 e Deadlock è un solo gruppo. I gruppi poi si ordinano in
"indizi": lo fa Claude una volta sola (titolo, spiegazione, rilevanza), e la classificazione resta salvata
nello stato del database (chiave timeline:temi). L'AI viene richiamata solo per gruppi mai visti.

La data di un indizio è la prima comparsa più vecchia tra le sue righe: per le voci già presenti è il primo
commit di SteamTracking che le contiene ("almeno dal" se c'erano già all'inizio del tracciamento).
Correzioni a mano in config: timeline.nascondi (id da nascondere), timeline.rilevanza ({id: alta|media|...}).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from hlxwatch.analysis import Analyzer, structured_call
from hlxwatch.config import Config
from hlxwatch.sources.first_seen import line_hash
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow
from hlxwatch.timeutil import local_day

log = logging.getLogger(__name__)

STATE_KEY = "timeline:temi"
LEVELS = ("alta", "media", "bassa", "rumore")
MAX_GROUPS_PER_CALL = 150
MAX_ATTEMPTS = 3

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "indizi": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "id di un indizio esistente, oppure 'nuovo'"},
                    "titolo": {"type": "string", "description": "Breve, in italiano, es. 'HLX nel bug reporter di Valve'"},
                    "spiegazione": {"type": "string",
                                    "description": "Due-tre frasi semplici: cosa è, dove si trova, cosa può significare"},
                    "rilevanza": {"type": "string", "enum": list(LEVELS)},
                    "cerca": {"type": "string", "description": "Una parola o frase breve per trovare le voci sul sito"},
                    "gruppi": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "titolo", "spiegazione", "rilevanza", "cerca", "gruppi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["indizi"],
    "additionalProperties": False,
}

TASK = """Questa volta il compito è diverso dall'analisi giornaliera: costruisci la CRONOLOGIA DEGLI INDIZI HLX.
Ricevi dei gruppi di righe (G1, G2...) trovate nei dati dei giochi Valve, ognuno con giochi, file, tipo,
numero di righe, un esempio e la data più vecchia nota. Raggruppali in indizi: un indizio è una singola
scoperta comprensibile da un appassionato (per esempio tutte le righe "mod != hlx" degli effetti particellari
sono un solo indizio, tutte le animazioni dalla cartella hl3 di Half-Life: Alyx sono un altro).

Per ogni indizio scrivi titolo e spiegazione in italiano semplice, senza gergo tecnico non spiegato, e scegli
la rilevanza: alta = indizio concreto di lavoro su un progetto HLX; media = riferimento reale ma vecchio o
indiretto; bassa = curiosità con poco significato; rumore = falso positivo (stringhe casuali in file binari,
coincidenze di lettere). Sii scettico e non inventare date o fatti.
Se ricevi anche degli indizi esistenti, metti i gruppi nuovi in un indizio esistente quando parlano della
stessa cosa (usa il suo id: titolo e spiegazione esistenti non cambiano), altrimenti crea un indizio con
id "nuovo". Ogni gruppo deve finire in esattamente un indizio."""


# --------------------------------------------------------------------- gruppi
def group_key(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r'exportscene="[^"]*"', 'exportscene="…"', t)
    return re.sub(r"\d+", "#", t)[:160]


def _repo_base(url: str | None) -> str:
    url = url or ""
    for marker in ("/tree/", "/commit/", "/blob/"):
        if marker in url:
            return url.split(marker)[0]
    return ""


def collect_groups(db: Database, min_score: int = 100,
                   include_keywords: list[str] | None = None) -> dict[str, dict[str, Any]]:
    # include_keywords: voci sotto la soglia da tenere come contesto (es. gli obiettivi HLX_ di Half-Life 2)
    cond = "f.score >= ?"
    params: list[Any] = [min_score]
    for kw in include_keywords or []:
        cond += " OR f.keywords LIKE ?"
        params.append(f'%"{kw}"%')
    rows = db.conn.execute(
        "SELECT f.*, b.url AS build_url, b.date AS build_date FROM findings f LEFT JOIN builds b ON b.id = f.build_id "
        f"WHERE ({cond}) AND f.change_type NOT IN ('baseline', 'build', 'new_app') ORDER BY f.score DESC",
        params).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for r in map(dict, rows):
        key = group_key(r["text"])
        g = groups.setdefault(key, {"key": key, "rows": [], "games": set(), "types": set(), "files": set()})
        g["rows"].append(r)
        g["games"].add(r["game"])
        g["types"].add(r["change_type"])
        g["files"].add(r["file_path"])
    for g in groups.values():
        g["events"] = [_event(db, r) for r in g["rows"]]
    return groups


def _event(db: Database, r: dict[str, Any]) -> dict[str, Any]:
    """Data e fonte di una riga: prima comparsa per le voci già presenti, data del commit per le altre."""
    if r["change_type"] == "present":
        fs = db.get_first_seen(r["game"], r["file_path"], line_hash(r["text"]))
        if fs and fs["commit_date"]:
            base = _repo_base(r.get("build_url"))
            return {"date": fs["commit_date"], "at_least": bool(fs["at_least"]), "kind": "comparsa",
                    "url": f"{base}/commit/{fs['commit_sha']}" if base else r.get("build_url"), "game": r["game"]}
        return {"date": None, "at_least": False, "kind": "comparsa", "url": r.get("build_url"), "game": r["game"]}
    kind = "rimozione" if r["change_type"] in ("removed", "file_removed") else "comparsa"
    return {"date": r.get("build_date") or r["created_at"], "at_least": False, "kind": kind,
            "url": r.get("build_url"), "game": r["game"]}


# -------------------------------------------------------------------- stato
SEED = Path(__file__).parent / "timeline_seed.json"


def load_state(db: Database) -> dict[str, Any]:
    raw = db.get_state(STATE_KEY)
    if not raw and SEED.exists():
        raw = SEED.read_text(encoding="utf-8")        # classificazione iniziale, rivista a mano e versionata
    state = json.loads(raw) if raw else {}
    state.setdefault("temi", {})
    state.setdefault("gruppi", {})
    state.setdefault("tentativi", {})
    return state


def save_state(db: Database, state: dict[str, Any]) -> None:
    db.set_state(STATE_KEY, json.dumps(state, ensure_ascii=False, separators=(",", ":")))


def _next_id(state: dict[str, Any]) -> str:
    n = max([int(t[1:]) for t in state["temi"] if t[1:].isdigit()] or [0]) + 1
    return f"t{n}"


# ------------------------------------------------------------------ classificazione
def _brief(gid: str, g: dict[str, Any]) -> dict[str, Any]:
    dates = [e["date"] for e in g["events"] if e["date"]]
    return {"id": gid, "giochi": sorted(g["games"]), "tipi": sorted(g["types"]),
            "file": sorted(g["files"])[:3], "righe": len(g["rows"]), "esempio": g["rows"][0]["text"][:200],
            "data_piu_vecchia": local_day(min(dates)) if dates else None}


def classify(cfg: Config, db: Database, groups: dict[str, dict[str, Any]], client: Any = None) -> int:
    """Assegna i gruppi mai visti a un indizio (nuovo o esistente). Restituisce quanti gruppi ha sistemato."""
    state = load_state(db)
    pending = [k for k in groups if k not in state["gruppi"] and state["tentativi"].get(k, 0) < MAX_ATTEMPTS]
    if not pending:
        return 0
    analyzer = Analyzer(cfg, db, client)
    if not analyzer.enabled():
        log.info("Cronologia: %d gruppi da classificare, ma l'AI non è attiva", len(pending))
        return 0
    budget = float((cfg.get("ai", {}) or {}).get("monthly_budget_usd", 10))
    if analyzer.month_spend() >= budget:
        log.warning("Cronologia: limite mensile dell'AI raggiunto, rimando la classificazione")
        return 0
    pending = pending[:MAX_GROUPS_PER_CALL]
    ids = {f"G{i}": key for i, key in enumerate(pending, start=1)}
    existing = [{"id": tid, "titolo": t["titolo"], "spiegazione": t["spiegazione"]} for tid, t in state["temi"].items()]
    data = {"indizi_esistenti": existing, "gruppi": [_brief(gid, groups[key]) for gid, key in ids.items()]}
    result, cost = structured_call(db, analyzer.client, schema=SCHEMA, label="Cronologia", effort="medium",
                                   max_tokens=16000,
                                   prompt=TASK + "\n\n" + json.dumps(data, ensure_ascii=False, default=str))
    result = result or {"indizi": []}
    for k in pending:
        state["tentativi"][k] = state["tentativi"].get(k, 0) + 1
    assigned = 0
    for item in result.get("indizi", []):
        tid = item.get("id", "nuovo")
        if tid not in state["temi"]:
            tid = _next_id(state)
            state["temi"][tid] = {
                "titolo": item["titolo"].strip(), "spiegazione": item["spiegazione"].strip(),
                "rilevanza": item["rilevanza"] if item["rilevanza"] in LEVELS else "media",
                "cerca": item.get("cerca", "").strip(), "creato": iso(utcnow()),
            }
        for gid in item.get("gruppi", []):
            if gid in ids and ids[gid] not in state["gruppi"]:
                state["gruppi"][ids[gid]] = tid
                state["tentativi"].pop(ids[gid], None)
                assigned += 1
    save_state(db, state)
    log.info("Cronologia: %d gruppi classificati su %d (costo %.4f $), %d indizi in totale",
             assigned, len(pending), cost, len(state["temi"]))
    return assigned


def update_timeline(cfg: Config, db: Database, client: Any = None) -> int:
    tcfg = cfg.get("timeline", {}) or {}
    if not tcfg.get("enabled", True):
        return 0
    groups = collect_groups(db, int(tcfg.get("min_score", 100)), tcfg.get("include_keywords") or [])
    return classify(cfg, db, groups, client)


# ------------------------------------------------------------------- pagina
def _sort_key(item: dict[str, Any]) -> str:
    return item["date"] or "0000"


def build_entries(cfg: Config, db: Database) -> dict[str, Any]:
    """Indizi pronti per la pagina, dal più recente al più vecchio (quelli senza data in fondo)."""
    tcfg = cfg.get("timeline", {}) or {}
    groups = collect_groups(db, int(tcfg.get("min_score", 100)), tcfg.get("include_keywords") or [])
    state = load_state(db)
    hidden = set(tcfg.get("nascondi", []) or [])
    overrides = tcfg.get("rilevanza", {}) or {}
    topics: dict[str, dict[str, Any]] = {}
    unclassified = 0
    for key, g in groups.items():
        tid = state["gruppi"].get(key)
        if not tid or tid not in state["temi"]:
            unclassified += 1
            continue
        t = topics.setdefault(tid, {"id": tid, **state["temi"][tid], "events": [], "games": set(), "lines": 0,
                                    "examples": [], "present": False})
        t["present"] = t["present"] or any(r["change_type"] == "present" for r in g["rows"])
        t["events"] += g["events"]
        t["games"] |= g["games"]
        t["lines"] += len(g["rows"])
        if len(t["examples"]) < 3:
            t["examples"].append(g["rows"][0]["text"][:220])
    entries, noise = [], []
    for tid, t in topics.items():
        if tid in hidden:
            continue
        t["rilevanza"] = overrides.get(tid, t["rilevanza"])
        firsts = sorted((e for e in t["events"] if e["kind"] == "comparsa" and e["date"]), key=lambda e: e["date"])
        removals = sorted((e for e in t["events"] if e["kind"] == "rimozione" and e["date"]), key=lambda e: e["date"])
        first = firsts[0] if firsts else None
        last_removal = removals[-1] if removals else None
        # una riga 'present' viene dal contenuto attuale: l'indizio c'è ancora, qualunque rimozione parziale
        # (in un solo gioco, o di una variante) ci sia stata dopo la prima comparsa
        still_present = t["present"] or (
            any(e["kind"] == "comparsa" for e in t["events"])
            and not (last_removal and (not firsts or last_removal["date"] > firsts[-1]["date"])))
        anchor = first or last_removal
        game_titles = sorted(cfg.game_title(g) for g in t["games"])
        entry = {
            "id": tid, "titolo": t["titolo"], "spiegazione": t["spiegazione"], "rilevanza": t["rilevanza"],
            "cerca": t.get("cerca") or "", "giochi": game_titles, "righe": t["lines"], "esempi": t["examples"],
            "date": anchor["date"] if anchor else None,
            "data": (("almeno dal " if first and first["at_least"] else "") + local_day(anchor["date"])) if anchor else
                    "data da determinare",
            "fonte": anchor["url"] if anchor else None,
            "rimosso": local_day(last_removal["date"]) if last_removal and not still_present else None,
            "anno": local_day(anchor["date"])[:4] if anchor else "senza data",
        }
        (noise if t["rilevanza"] == "rumore" else entries).append(entry)
    entries.sort(key=_sort_key, reverse=True)
    noise.sort(key=_sort_key, reverse=True)
    return {"entries": entries, "noise": noise, "unclassified": unclassified,
            "updated": local_day(utcnow().isoformat())}
