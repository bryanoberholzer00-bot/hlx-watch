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
from hlxwatch.timeutil import local_day

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
normale. Non inventare fatti, date o nomi che non sono nei dati.

Per le voci già presenti nel contenuto attuale, il campo prima_comparsa è la data del primo commit di \nSteamTracking in cui quella riga compare nel file; \"almeno dal\" significa che c'era già quando il \nrepository ha iniziato a tracciare il file, quindi può essere più vecchia."""

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
DEFAULT_GENERIC = ["npc", "vehicle", "weather", "physics", "half_life"]


def _key(r: dict[str, Any]) -> tuple[Any, ...]:
    return ((r.get("token") or r["text"]).lower(), r.get("build_id"), r["change_type"])


def is_generic(row: dict[str, Any], generic: set[str]) -> bool:
    """Voce fatta solo di parole generiche (npc_, vehicle_, phys_...): rumore per l'analisi, salvo punteggi alti."""
    try:
        kws = json.loads(row.get("keywords") or "[]")
    except (TypeError, json.JSONDecodeError):
        kws = []
    return row["score"] < 100 and bool(kws) and all(k in generic for k in kws)


def _evidence(rows: list[dict[str, Any]], start: int, label: str, db: Database | None = None,
              skip: set[tuple[Any, ...]] | None = None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Raggruppa per token+commit+tipo e assegna gli identificativi E1, E2... (ritorna elenco e mappa id->voce)."""
    from hlxwatch.sources.first_seen import line_hash
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = _key(r)
        if skip and key in skip:
            continue
        g = grouped.get(key)
        if g:
            g["altri_file"] += 1
            continue
        item = {
            "gioco": r["game"], "data_modifica": local_day(r.get("build_date") or r["created_at"]),
            "tipo": r["change_type"], "punteggio": r["score"], "token": r.get("token"),
            "riga": (r["text"] or "")[:300], "riga_precedente": (r.get("old_text") or "")[:300] or None,
            "file": r["file_path"], "contesto": (r.get("context") or "")[:600] or None,
            "commit": r.get("build_url"), "altri_file": 0, "sezione": label, "_key": key,
        }
        if r["change_type"] == "present" and db is not None:
            fs = db.get_first_seen(r["game"], r["file_path"], line_hash(r["text"]))
            if fs and fs["commit_date"]:
                item["prima_comparsa"] = ("almeno dal " if fs["at_least"] else "") + local_day(fs["commit_date"])
                repo = r.get("build_url", "") or ""
                base = repo.split("/tree/")[0] if "/tree/" in repo else ""
                item["commit_prima_comparsa"] = f"{base}/commit/{fs['commit_sha']}" if base else fs["commit_sha"]
            else:
                item["prima_comparsa"] = "non ancora determinata"
            item["data_modifica"] = None     # per le voci già presenti conta la prima comparsa, non la scansione
        grouped[key] = item
    out, by_id = [], {}
    for i, item in enumerate(grouped.values(), start=start):
        item = {"id": f"E{i}", **item}
        out.append(item)
        by_id[item["id"]] = item
    return out, by_id


def collect_input(db: Database, since: datetime, max_items: int = 60,
                  generic: list[str] | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    generic_set = set(generic if generic is not None else DEFAULT_GENERIC)
    recent_rows = [dict(r) for r in db.search_findings(since=since, min_score=50, limit=5000, order="score",
                                                        exclude_change_type=("baseline", "present"))]
    noise = [r for r in recent_rows if is_generic(r, generic_set)]
    recent, ids = _evidence([r for r in recent_rows if not is_generic(r, generic_set)], 1, "novità")
    recent = recent[:max_items]
    recent_keys = {e["_key"] for e in recent}
    history_rows = [dict(r) for r in db.search_findings(min_score=100, limit=500, order="created_at",
                                                         exclude_change_type=("baseline", "present"))]
    history, ids2 = _evidence(history_rows, len(recent) + 1, "storico", skip=recent_keys)
    history = history[:40]
    present_rows = [dict(r) for r in db.search_findings(change_type="present", min_score=90, limit=2000, order="score")]
    present, ids3 = _evidence(present_rows, len(recent) + len(history) + 1, "già presenti", db=db)
    # le centinaia di righe quasi identiche (es. file di animazione) bastano poche volte
    seen_folders: dict[str, int] = {}
    present_short = []
    for item in present:
        folder = "/".join(item["file"].split("/")[:3])
        seen_folders[folder] = seen_folders.get(folder, 0) + 1
        if seen_folders[folder] <= 3:
            present_short.append(item)
    ids.update(ids2)
    ids.update(ids3)
    for collection in (recent, history, present_short):
        for item in collection:
            item.pop("_key", None)
    # riassunto del rumore escluso, perché il modello sappia che c'è senza sprecarci spazio
    noise_summary: dict[str, int] = {}
    for r in noise:
        label = f"{r['game']}: {'/'.join(r['file_path'].split('/')[:4])}"
        noise_summary[label] = noise_summary.get(label, 0) + 1
    previous = db.latest_analysis()
    data = {
        "periodo": {"dal": iso(since), "al": iso(utcnow())},
        "novita": recent,
        "rumore_generico_escluso": {
            "spiegazione": "voci con sole parole generiche (npc_, vehicle_, phys_, meteo, half-life generico) "
                           "e punteggio sotto 100, non incluse una per una",
            "totale": len(noise),
            "per_cartella": dict(sorted(noise_summary.items(), key=lambda kv: -kv[1])[:15]),
        },
        "storico_alta_rilevanza": history,
        "gia_presenti_nel_contenuto_attuale": present_short[:40],
        "analisi_precedente": ({"data": local_day(previous["created_at"]), **json.loads(previous["content"])}
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
        from hlxwatch.timeutil import today_local
        today = today_local()
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
        data, ids = collect_input(self.db, since, int(self.acfg.get("max_items", 60)),
                                  self.acfg.get("generic_keywords"))
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
        content["evidenze"] = {k: {"gioco": v["gioco"], "data": v.get("data_modifica") or v.get("prima_comparsa"),
                                   "token": v["token"], "riga": v["riga"][:160], "file": v["file"],
                                   "commit": v.get("commit_prima_comparsa") or v["commit"]}
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
