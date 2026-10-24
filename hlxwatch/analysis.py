"""Analisi AI giornaliera delle scoperte con Claude Opus 5.5.

Raccoglie le novità rilevanti dall'ultima analisi, il contesto storico (voci ad alta rilevanza e
occorrenze già presenti) e l'analisi precedente, e chiede a Claude un'interpretazione in italiano che
separa i fatti dalle ipotesi, citando ogni prova con un identificativo (E1, E2...) collegato al commit.

Gira nel workflow di GitHub: la chiave ANTHROPIC_API_KEY sta nei Secrets, il sito resta statico.
Un limite di spesa mensile (ai.monthly_budget_usd) blocca le chiamate oltre la soglia.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from hlxwatch.config import Config
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
PRICE_INPUT = 4.00 / 1_000_000       # $ per token, Claude Opus 5.5
PRICE_OUTPUT = 20.00 / 1_000_000
PRICE_CACHE_READ = 0.20 / 1_000_000
SITE_URL = "https://bryanoberholzer00-bot.github.io/hlx-watch/"

SYSTEM_PROMPT = """Sei l'analista di HLX Watch, uno strumento che sorveglia i dati pubblici dei giochi Valve su \
Source 2 (dump di stringhe, schemi del motore, file FGD, ConVar, build Steam estratti dai repository \
SteamTracking/GameTracking-*) alla ricerca di indizi su HLX, il presunto nome in codice di Half-Life 3, e su \
altri progetti Valve non annunciati.

Ricevi le novità dall'ultima analisi e il contesto storico, ogni voce con un identificativo (E1, E2...). \
Scrivi un'analisi in italiano per un appassionato che conosce Half-Life ma non il motore Source 2.

Distingui con rigore:
- fatti: ciò che i dati mostrano direttamente, con gli identificativi delle prove;
- ipotesi: interpretazioni plausibili, ognuna con il ragionamento, il livello di fiducia e le prove.

Sii scettico. Fonti note di falsi segnali: cambi di formato dei dump da parte di SteamTracking (una stringa \
che "sparisce" dal dump delle stringhe e "compare" negli schemi è uno spostamento, non una modifica di \
Valve); voci rimaste da progetti chiusi o già usciti (Half-Life: Alyx è uscito nel 2020, Left 4 Dead 3 è noto \
dal 2015); sistemi del motore condiviso che non appartengono a un gioco specifico. Se le novità non dicono \
nulla di nuovo su HLX, dillo chiaramente e imposta novita_rilevanti a false: un giorno tranquillo è un esito \
normale. Non inventare fatti, date o nomi che non sono nei dati."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titolo": {"type": "string", "description": "Una frase che riassume la giornata"},
        "sintesi": {"type": "string", "description": "Due-quattro frasi"},
        "novita_rilevanti": {"type": "boolean"},
        "fatti": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"testo": {"type": "string"}, "prove": {"type": "array", "items": {"type": "string"}}},
                "required": ["testo", "prove"],
                "additionalProperties": False,
            },
        },
        "ipotesi": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titolo": {"type": "string"},
                    "spiegazione": {"type": "string"},
                    "fiducia": {"type": "string", "enum": ["alta", "media", "bassa"]},
                    "prove": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["titolo", "spiegazione", "fiducia", "prove"],
                "additionalProperties": False,
            },
        },
        "da_osservare": {"type": "array", "items": {"type": "string"},
                         "description": "Cosa tenere d'occhio nei prossimi aggiornamenti"},
        "rumore": {"type": "array", "items": {"type": "string"},
                   "description": "Cose che sembrano importanti ma non lo sono, e perché"},
    },
    "required": ["titolo", "sintesi", "novita_rilevanti", "fatti", "ipotesi", "da_osservare", "rumore"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- dati
def _evidence(rows: list[Any], start: int, label: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Raggruppa per token+commit+tipo e assegna gli identificativi E1, E2... (ritorna elenco e mappa id->voce)."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        r = dict(r)
        key = ((r.get("token") or r["text"]).lower(), r.get("build_id"), r["change_type"])
        g = grouped.get(key)
        if g:
            g["altri_file"] += 1
            continue
        grouped[key] = {
            "gioco": r["game"], "data_modifica": (r.get("build_date") or r["created_at"])[:10],
            "tipo": r["change_type"], "punteggio": r["score"], "token": r.get("token"),
            "riga": (r["text"] or "")[:300], "riga_precedente": (r.get("old_text") or "")[:300] or None,
            "file": r["file_path"], "contesto": (r.get("context") or "")[:600] or None,
            "commit": r.get("build_url"), "altri_file": 0, "sezione": label,
        }
    out, by_id = [], {}
    for i, item in enumerate(grouped.values(), start=start):
        item = {"id": f"E{i}", **item}
        out.append(item)
        by_id[item["id"]] = item
    return out, by_id


def collect_input(db: Database, since: datetime, max_items: int = 60) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    recent_rows = db.search_findings(since=since, min_score=50, limit=2000, order="score",
                                     exclude_change_type=("baseline", "present"))
    recent, ids = _evidence(list(recent_rows)[:max_items * 4], 1, "novità")
    recent = recent[:max_items]
    history_rows = db.search_findings(min_score=100, limit=500, order="created_at",
                                      exclude_change_type=("baseline", "present"))
    history, ids2 = _evidence(list(history_rows), len(recent) + 1, "storico")
    history = [h for h in history if h["id"] not in {r["id"] for r in recent}][:40]
    present_rows = db.search_findings(change_type="present", min_score=90, limit=2000, order="score")
    present, ids3 = _evidence(list(present_rows), len(recent) + len(history) + 1, "già presenti")
    # le centinaia di righe quasi identiche (es. file di animazione) bastano poche volte
    seen_folders: dict[str, int] = {}
    present_short = []
    for p in present:
        folder = "/".join(p["file"].split("/")[:3])
        seen_folders[folder] = seen_folders.get(folder, 0) + 1
        if seen_folders[folder] <= 3:
            present_short.append(p)
    ids.update(ids2)
    ids.update(ids3)
    previous = db.latest_analysis()
    data = {
        "periodo": {"dal": iso(since), "al": iso(utcnow())},
        "novita": recent,
        "storico_alta_rilevanza": history,
        "gia_presenti_nel_contenuto_attuale": present_short[:40],
        "analisi_precedente": ({"data": previous["created_at"][:10], **json.loads(previous["content"])}
                               if previous else None),
        "statistiche": db.stats(),
    }
    return data, ids


# -------------------------------------------------------------------- costo
def cost_of(usage: Any) -> float:
    def g(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)
    return round(g("input_tokens") * PRICE_INPUT + g("cache_creation_input_tokens") * PRICE_INPUT * 1.25
                 + g("cache_read_input_tokens") * PRICE_CACHE_READ + g("output_tokens") * PRICE_OUTPUT, 4)


# ---------------------------------------------------------------- analisi
class Analyzer:
    def __init__(self, cfg: Config, db: Database, client: Any = None):
        self.cfg = cfg
        self.db = db
        self.acfg: dict[str, Any] = cfg.get("ai", {}) or {}
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # importato qui: il resto dello strumento funziona anche senza
            self._client = anthropic.Anthropic()
        return self._client

    def enabled(self) -> bool:
        return bool(self.acfg.get("enabled", True)) and (self._client is not None or bool(Config.env("ANTHROPIC_API_KEY")))

    def month_spend(self) -> float:
        start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.db.analyses_cost_since(start)

    def run(self, *, force: bool = False, days: int | None = None) -> dict[str, Any] | None:
        if not self.enabled():
            log.info("Analisi AI: disattivata o ANTHROPIC_API_KEY assente, salto")
            return None
        today = utcnow().strftime("%Y-%m-%d")
        if not force and self.db.get_state("ai:last_day") == today:
            return None
        budget = float(self.acfg.get("monthly_budget_usd", 10))
        spent = self.month_spend()
        if spent >= budget:
            log.warning("Analisi AI: limite mensile raggiunto (%.2f $ su %.2f $), salto", spent, budget)
            return None

        previous = self.db.latest_analysis()
        if days is not None:
            since = utcnow() - timedelta(days=days)
        elif previous:
            since = parse_iso(previous["period_end"]) or utcnow() - timedelta(days=1)
        else:
            since = utcnow() - timedelta(days=3650)       # prima analisi: tutto l'archivio
        data, ids = collect_input(self.db, since, int(self.acfg.get("max_items", 60)))
        if not data["novita"] and previous and not force:
            log.info("Analisi AI: nessuna novità sopra 50 punti dall'ultima analisi, salto (nessun costo)")
            self.db.set_state("ai:last_day", today)
            return None

        prompt = ("Ecco i dati in JSON. Le prove hanno un campo id (E1, E2...): citale nei campi 'prove'.\n\n"
                  + json.dumps(data, ensure_ascii=False, default=str))
        log.info("Analisi AI: %d novità, %d voci storiche, %d già presenti -> %s",
                 len(data["novita"]), len(data["storico_alta_rilevanza"]),
                 len(data["gia_presenti_nel_contenuto_attuale"]), MODEL)
        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={"effort": str(self.acfg.get("effort", "high")),
                           "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        cost = cost_of(response.usage)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            log.error("Analisi AI rifiutata dal modello (%s), costo %.4f $", getattr(details, "category", None), cost)
            self.db.add_analysis(MODEL, iso(since), iso(utcnow()), None, cost, response.usage, error="refusal")
            return None
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            content = json.loads(text)
        except json.JSONDecodeError:
            log.error("Analisi AI: risposta non in JSON (stop_reason=%s)", response.stop_reason)
            self.db.add_analysis(MODEL, iso(since), iso(utcnow()), None, cost, response.usage, error="json")
            return None
        content["evidenze"] = {k: {"gioco": v["gioco"], "data": v["data_modifica"], "token": v["token"],
                                   "riga": v["riga"][:160], "file": v["file"], "commit": v["commit"]}
                               for k, v in ids.items() if k in _cited(content)}
        served_by = getattr(response, "model", MODEL)
        self.db.add_analysis(served_by, iso(since), iso(utcnow()), content, cost, response.usage)
        self.db.set_state("ai:last_day", today)
        log.info("Analisi AI salvata: \"%s\" (costo %.4f $, mese %.2f $ su %.2f $)",
                 content.get("titolo", ""), cost, spent + cost, budget)
        return content


def _cited(content: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for section in ("fatti", "ipotesi"):
        for item in content.get(section, []) or []:
            ids.update(str(x).strip() for x in item.get("prove", []) or [])
    return ids


def telegram_text(content: dict[str, Any]) -> str:
    lines = [content.get("sintesi", "")]
    for h in (content.get("ipotesi") or [])[:4]:
        lines.append(f"- [{h.get('fiducia', '?')}] {h.get('titolo', '')}")
    lines.append("")
    lines.append(f"Analisi completa: {SITE_URL}analisi.html")
    return "\n".join(lines)
