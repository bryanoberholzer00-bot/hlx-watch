"""Una scheda per ogni traccia HLX: tutto quello che l'archivio sa di un nome, in una pagina sola.

Prima le informazioni su una traccia erano sparse tra la Ricerca (le singole righe), gli Esiti (dove è finita) e la
pagina su Alyx (motore o gioco). La scheda le mette insieme:
  - il tipo (gioco, motore, eredità di Alyx, strumenti, da capire) e l'esito (presente, rinominata, sparita...);
  - in quali giochi è comparsa, quando, e quando è stata tolta;
  - le righe del file attorno alla riga HLX nel commit in cui è comparsa ("righe vicine");
  - le altre tracce HLX comparse lo stesso giorno nello stesso file;
  - ogni comparsa nell'archivio, con il link al commit.

Una pagina per traccia (traccia-<nome>.html, accanto alle altre pagine del sito): si apre senza script, si può
condividere e il controllo del sito ne verifica i link come per le altre pagine.

Le righe vicine nei dump di stringhe (_strings.txt) non dicono niente: quei file sono in ordine alfabetico, quindi i
"vicini" hanno solo l'iniziale in comune. Si mostrano per primi i file strutturati (FGD, schemi, Protobuf, suoni) e,
se ci sono solo dump, con l'avvertenza.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hlxwatch.fase import trace_kind
from hlxwatch.outcomes import MAIN_GAMES, _is_hlx, merge_partners, norm, trace_key
from hlxwatch.storage import Database
from hlxwatch.timeutil import local_day

PREFIX = "traccia-"
MAX_SLUG = 50
MAX_ROWS = 60                  # comparsa per comparsa: oltre si rimanda alla Ricerca
MAX_BLOCKS = 3
MAX_BLOCK_LINES = 24
DUMP = re.compile(r"(?:_strings\.txt|\.stringsignore|pak\d*_dir\.txt)$|(?:^|/)Strings/", re.I)

KIND_TEXT = {
    "gioco": ("🎮 pezzo di gioco", "Armi, nemici, oggetti, comportamenti o controlli del giocatore: parti di un gioco, "
                                  "non della tecnologia comune."),
    "motore": ("⚙️ pezzo di motore", "Grafica, suono, materiali, particelle, fisica o impostazioni degli shader: "
                                    "tecnologia che può servire a più giochi."),
    "alyx": ("🔁 eredità di Alyx", "Un nome di Half-Life: Alyx con hlx al posto di hlvr: probabilmente codice ripreso "
                                  "da Alyx, non lavoro nuovo."),
    "strumenti": ("🛠️ strumenti di lavoro", "Strumenti interni di Valve, per esempio il modulo per segnalare i bug: "
                                           "dicono che il progetto esiste, non a che punto è."),
    "dubbio": ("❔ da capire", "Il nome da solo non basta per dire se è un pezzo di motore o di gioco."),
}
STATUS_TEXT = {
    "presente": "La riga c'è ancora oggi nei file (scansione completa).",
    "rinominata": "La riga è ricomparsa nello stesso file con un altro prefisso: la tecnologia è passata altrove.",
    "sparita": "Valve l'ha tolta e non è più nei file.",
    "manuale": "Un caso che lo strumento non può dimostrare da solo: l'esito è stato scritto a mano dopo un controllo.",
    "da_verificare": "Vista nei commit, ma la scansione completa non la segue: non si sa se c'è ancora.",
}
# cosa dimostra il tipo di file in cui sta la traccia (le etichette sono quelle di evidence.py)
EVIDENCE_TEXT = {
    "solo stringhe dei binari": "Il nome è dentro i programmi del gioco, ma non si vede cosa fa: le stringhe "
                                "suggeriscono, non provano.",
    "classi, variabili o protocolli del motore": "Una classe, una variabile o un messaggio di rete del motore: "
                                                 "dice com'è fatto il codice.",
    "dati di gioco o traduzioni": "Nei file di dati o nei testi del gioco: contenuto vero, non solo un nome.",
    "file dell'interfaccia": "Nei file dell'interfaccia: qualcosa che il giocatore vedrebbe.",
    "elenco di file": "Il file esiste nei pacchetti del gioco, ma il contenuto non si vede.",
    "build, depot o struttura dei repository": "Un cambiamento nelle build o nei depot di Steam.",
    "codice pubblico di Valve su GitHub": "Nel codice che Valve pubblica su GitHub.",
}
CHANGE_LABELS = {"added": "aggiunta", "removed": "tolta", "present": "presente (scansione completa)",
                 "file_added": "file nuovo", "file_removed": "file tolto", "changed": "cambiata",
                 "pulse": "ricomparsa", "renamed": "rinominata"}


# ------------------------------------------------------------------ nomi delle pagine
def _clean(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def assign_slugs(traces: list[dict[str, Any]]) -> dict[str, str]:
    """chiave della traccia -> nome della pagina (senza prefisso né .html), stabile tra un giro e l'altro.
    Il nome viene dalla traccia stessa; se è troppo lungo o due tracce darebbero lo stesso nome (cambiano solo
    segni di punteggiatura), si aggiungono 6 caratteri presi dalla chiave."""
    base = {t["key"]: _clean(t["key"]) or "traccia" for t in traces}
    seen: dict[str, int] = {}
    for s in base.values():
        seen[s] = seen.get(s, 0) + 1
    out = {}
    for key, s in base.items():
        if len(s) > MAX_SLUG or seen[s] > 1:
            s = s[:MAX_SLUG].strip("-") + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
        out[key] = s
    for t in traces:
        t["slug"] = out[t["key"]]
    return out


def page_name(slug: str) -> str:
    return f"{PREFIX}{slug}.html"


# ------------------------------------------------------------------ dati dall'archivio
def occurrences(db: Database, keys: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Tutte le righe HLX dell'archivio delle tracce richieste, con la data della prima comparsa se nota."""
    rows = db.conn.execute(
        "SELECT f.id, f.game, f.file_path, f.line_no, f.change_type, f.token, f.text, f.context, f.keywords, f.score, "
        "COALESCE(fs.commit_date, b.date, f.created_at) AS d, fs.at_least AS al, b.url AS url FROM findings f "
        "LEFT JOIN builds b ON b.id = f.build_id LEFT JOIN first_seen fs ON fs.game = f.game AND fs.file_path = f.file_path "
        "AND fs.text = f.text WHERE f.change_type NOT IN ('baseline', 'summary', 'renamed') "
        "AND f.file_path NOT LIKE '%.stringsignore'").fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if not _is_hlx(r["keywords"] or ""):
            continue
        key = trace_key(r["token"], r["text"])
        if key not in keys:
            continue
        item = dict(r)
        item["day"] = local_day(item["d"]) if item["d"] else ""
        try:
            item["kw"] = json.loads(item["keywords"] or "[]")
        except ValueError:
            item["kw"] = []
        out.setdefault(key, []).append(item)
    for items in out.values():
        items.sort(key=lambda x: (x["day"], x["id"]))
    return out


def _unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """La stessa riga nello stesso file e gioco con lo stesso cambiamento conta una volta (la prima). La riga
    "presente" della scansione completa si salta se c'è già la sua aggiunta: è la stessa comparsa vista due volte."""
    added = {(r["game"], r["file_path"], norm(r["text"])) for r in rows if r["change_type"] == "added"}
    seen: set[tuple[str, str, str, str]] = set()
    out = []
    for r in rows:
        line = norm(r["text"])
        k = (r["game"], r["change_type"], r["file_path"], line)
        if k in seen or (r["change_type"] == "present" and (r["game"], r["file_path"], line) in added):
            continue
        seen.add(k)
        out.append(r)
    return out


def _is_diff(context: str) -> bool:
    """Il contesto è un pezzo del diff (righe con +, - o spazio davanti), non una nota come "archeologia: ..."."""
    lines = [x for x in (context or "").splitlines() if x.strip()]
    return len(lines) > 1 and any(x[:1] in "+-" for x in lines)


def _block(r: dict[str, Any], raw_lines: list[str], source: str) -> dict[str, Any]:
    lines = []
    for raw in raw_lines[:MAX_BLOCK_LINES]:
        diff = source == "diff" and raw[:1] in "+- "
        body = raw[1:] if diff else raw
        lines.append({"mark": raw[:1] if diff else " ", "text": body[:220], "hlx": "hlx" in body.lower()})
    return {"game": r["game"], "day": r["day"], "file": r["file_path"], "url": r.get("url"), "lines": lines,
            "source": source}


def context_blocks(rows: list[dict[str, Any]], stored: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Le righe vicine al momento della comparsa: un blocco per file, prima i file strutturati (dal diff o, se il
    diff non c'è, dal file intero scaricato una volta da fill_neighbours). Il secondo valore dice se i blocchi
    vengono solo da dump in ordine alfabetico."""
    added = [r for r in rows if r["change_type"] in ("added", "pulse", "changed") and _is_diff(r.get("context") or "")]
    structured = [_block(r, r["context"].splitlines(), "diff") for r in added if not DUMP.search(r["file_path"])]
    if stored and stored.get("lines"):
        structured.append(_block(stored, stored["lines"], "file"))
    pick = structured or [_block(r, r["context"].splitlines(), "diff") for r in added]
    blocks, files = [], set()
    for b in pick:
        name = b["file"].rsplit("/", 1)[-1]
        if name in files:
            continue
        files.add(name)
        blocks.append(b)
        if len(blocks) >= MAX_BLOCKS:
            break
    return blocks, bool(blocks) and not structured


def per_game(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per ogni gioco: prima comparsa, ultima rimozione (se dopo l'ultima aggiunta) e quante righe diverse."""
    games: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = games.setdefault(r["game"], {"game": r["game"], "first": "", "last_add": "", "removed": "", "files": set()})
        if not r["day"]:
            continue
        if r["change_type"] in ("removed", "file_removed"):
            g["removed"] = max(g["removed"], r["day"])
        else:
            g["first"] = min(g["first"] or r["day"], r["day"])
            g["last_add"] = max(g["last_add"], r["day"])
            g["files"].add(r["file_path"])
    out = []
    for g in games.values():
        if not g["first"]:
            continue
        out.append({"game": g["game"], "first": g["first"], "files": len(g["files"]),
                    "removed": g["removed"] if g["removed"] and g["removed"] >= g["last_add"] else ""})
    return sorted(out, key=lambda g: g["first"])


def display_label(label: str) -> str:
    """Il nome da mostrare: senza <br>, virgolette e trattini rimasti dalla riga del file."""
    t = re.sub(r"<br\s*/?>", " ", label or "", flags=re.I)
    return " ".join(t.split()).strip(' "-') or (label or "").strip()


def card(t: dict[str, Any], rows: list[dict[str, Any]], reg: dict[str, Any], ctx: dict[str, Any] | None,
         stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """I dati di una scheda: la traccia del registro degli esiti più le sue righe nell'archivio."""
    rep = next((r for r in rows if r["change_type"] not in ("removed", "file_removed")), rows[0] if rows else None)
    kind = trace_kind(rep["token"], rep["text"], rep["kw"]) if rep else None
    kind = kind or "dubbio"
    blocks, only_dumps = context_blocks(rows, stored)
    games = per_game(rows)
    merge: list[str] = []
    if ctx and rep and games and games[0]["game"] in MAIN_GAMES:
        merge = merge_partners(ctx["merge"], games[0]["game"], rep["token"], rep["text"], games[0]["first"],
                               ctx.get("titles"))
    together = [{"label": display_label(o["label"]), "slug": o["slug"]} for o in reg["traces"]
                if o["key"] != t["key"] and o.get("slug") and o["first"] == t["first"] and o["file"] == t["file"]]
    unique = _unique(rows)
    search_term = rep["token"] if rep and rep["token"] and rep["token"].lower() != "hlx" else (rep["text"] if rep else t["label"])
    ev_title = (t.get("evidence") or "altro").split(":")[0]
    return {
        **t, "label": display_label(t["label"]), "kind": kind, "kind_label": KIND_TEXT[kind][0],
        "kind_text": KIND_TEXT[kind][1], "ev_title": ev_title[:1].upper() + ev_title[1:],
        "ev_text": EVIDENCE_TEXT.get(ev_title, ""),
        "status_text": STATUS_TEXT.get(t["status"], ""), "per_game": games, "blocks": blocks, "only_dumps": only_dumps,
        "merge": merge, "together": together[:12],
        "rows": [dict(r, change_label=CHANGE_LABELS.get(r["change_type"], r["change_type"])) for r in unique[:MAX_ROWS]],
        "n_rows": len(unique), "text": (rep["text"] if rep else t["label"]) or "",
        "search": "search.html?q=" + quote((search_term or "")[:80]),
    }


def write_cards(env: Any, db: Database, reg: dict[str, Any], ctx: dict[str, Any] | None, out_dir: Path) -> int:
    """Scrive una pagina per traccia e toglie quelle di tracce che non ci sono più (una correzione può unirle)."""
    if not any("slug" in t for t in reg["traces"]):
        assign_slugs(reg["traces"])
    found = occurrences(db, {t["key"] for t in reg["traces"]})
    template = env.get_template("scheda.html")
    written = set()
    for t in reg["traces"]:
        name = page_name(t["slug"])
        c = card(t, found.get(t["key"], []), reg, ctx, stored_neighbours(db, t["key"]))
        (out_dir / name).write_text(template.render(c=c), encoding="utf-8")
        written.add(name)
    for old in out_dir.glob(f"{PREFIX}*.html"):
        if old.name not in written:
            old.unlink()
    return len(written)


# ------------------------------------------------------------------ righe vicine dal file intero
STATE_PREFIX = "scheda:vicine:"
COMMIT = re.compile(r"github\.com/([^/]+/[^/]+)/commit/([0-9a-f]{7,40})")
MAX_FILE_BYTES = 8 * 1024 * 1024


def _state_key(key: str) -> str:
    return STATE_PREFIX + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def stored_neighbours(db: Database, key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(_state_key(key)) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def paragraph(lines: list[str], i: int, cap: int = 10) -> tuple[int, int]:
    """Il blocco della riga i: fino alle righe vuote prima e dopo, al massimo cap righe per parte."""
    a = i
    while a > 0 and lines[a - 1].strip() and i - a < cap:
        a -= 1
    b = i
    while b + 1 < len(lines) and lines[b + 1].strip() and b - i < cap:
        b += 1
    return a, b


def _candidate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La prima aggiunta in un file strutturato, con il link al commit e senza un diff già salvato."""
    for r in rows:
        if (r["change_type"] == "added" and not DUMP.search(r["file_path"]) and COMMIT.search(r.get("url") or "")
                and not _is_diff(r.get("context") or "")):
            return r
    return None


def fill_neighbours(cfg: Any, db: Database, client: Any = None, limit: int = 12) -> int:
    """Per le tracce in file strutturati senza diff (trovate dall'archeologia) scarica il file al commit della prima
    comparsa da raw.githubusercontent.com e salva le righe attorno alla riga HLX. Una volta sola per traccia: anche
    un tentativo andato a vuoto resta salvato. Restituisce quanti file ha scaricato."""
    from hlxwatch.outcomes import outcome_register
    from hlxwatch.storage.db import iso, utcnow
    reg = outcome_register(cfg, db)
    found = occurrences(db, {t["key"] for t in reg["traces"]})
    done = 0
    for t in reg["traces"]:
        if done >= limit:
            break
        if db.get_state(_state_key(t["key"])) is not None:
            continue
        r = _candidate(found.get(t["key"], []))
        if r is None:
            continue
        if client is None:
            from hlxwatch.sources.github_tracking import GitHubClient
            client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                                  cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
        repo, sha = COMMIT.search(r["url"]).groups()
        done += 1
        lines = (client.raw_file(repo, sha, r["file_path"], MAX_FILE_BYTES) or "").splitlines()
        want = norm(r["text"])
        hit = next((i for i, x in enumerate(lines) if norm(x) == want), None)
        if hit is None and want:
            hit = next((i for i, x in enumerate(lines) if want in norm(x)), None)
        data: dict[str, Any] = {"at": iso(utcnow()), "lines": []}
        if hit is not None:
            a, b = paragraph(lines, hit)
            data.update({"game": r["game"], "day": r["day"], "file_path": r["file_path"], "url": r["url"],
                         "lines": lines[a:b + 1]})
        db.set_state(_state_key(t["key"]), json.dumps(data, ensure_ascii=False))
    return done
