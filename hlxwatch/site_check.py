"""Controllo automatico del sito: dopo ogni generazione, prima di pubblicarlo.

Controlla la cartella generata, senza scaricare niente da internet:
  - i file che servono (pagine, dati dell'app e del bot, feed, immagine di anteprima) ci sono e non sono vuoti;
  - i file JSON si leggono (l'app Android e il widget li usano) e il feed è XML valido;
  - ogni pagina è completa (finisce con </html>) e non mostra pezzi di modello rimasti ({{ }}, None, NaN);
  - i link interni portano a un file che esiste, e i link a una sezione (#nome) a una sezione che esiste;
  - il termometro e l'ultima traccia forte hanno lo stesso valore sulle pagine e nel widget;
  - nessun file è così grande da rendere il sito lento sul telefono.

I problemi "gravi" (file mancanti, JSON illeggibili, pagine tagliate) fermano la pubblicazione: resta online la
versione precedente. Gli altri vengono solo segnalati. L'avviso su Telegram parte quando compare un problema
nuovo e quando tutto torna a posto, non a ogni giro. L'esito resta nello stato (site:check) e compare sul sito.
"""
from __future__ import annotations

import html
import importlib.util
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

log = logging.getLogger(__name__)

STATE = "site:check"
TITLE_FAIL = "HLX Watch: problemi nel sito"
TITLE_OK = "HLX Watch: il sito è di nuovo a posto"

REQUIRED = ("index.html", "en.html", "hlx.html", "termometro.html", "search.html", "metodologia.html",
            "alyx.html", "sistemi.html", "segnali.html", "annuncio.html", "mappa.html", "stato.html", "cronologia.html", "contatore.html", "orologio.html", "bot/contatore.json", "findings.json", "feed.xml", "manifest.webmanifest", "sw.js",
            "bot/termometro.json", "bot/termometro_storico.json", "bot/stato.json", "bot/ultime.json",
            "bot/indice.json")
# pagine con il termometro in alto: devono dire lo stesso numero del widget dell'app
TERMO_PAGES = ("index.html", "hlx.html", "en.html")
MAX_MB = 20
MAX_LISTED = 8

LINK = re.compile(r'\b(?:href|src)="([^"]+)"')
IDS = re.compile(r'\bid="([^"]+)"')
TERMO = re.compile(r'class="termo"[^>]*>\s*<b>(\d+)</b>')
LAST_TRACE = re.compile(r'Ultima traccia HLX forte</span>\s*<b>(\d{4}-\d{2}-\d{2})</b>')
SCRIPTS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
LEFTOVERS = (("{{", "segni {{ di un modello non riempito"), ("{%", "segni {% di un modello non riempito"),
             ("Traceback", "un errore di Python"))
LEFTOVER_WORDS = re.compile(r"(?:^|\s)(None|NaN|nan|undefined)(?:\s|$)")


def _problem(grave: bool, text: str) -> dict[str, Any]:
    return {"grave": grave, "testo": text}


def _listed(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" e altri {len(items) - MAX_LISTED}" if len(items) > MAX_LISTED else "")


def _visible_text(page: str) -> str:
    return html.unescape(TAGS.sub(" ", SCRIPTS.sub(" ", page)))


def check_site(root: Path, need_preview: bool | None = None) -> list[dict[str, Any]]:
    """Problemi trovati nella cartella del sito generato (vuoto = tutto a posto)."""
    root = root.resolve()                        # i link si risolvono con resolve(): stesse chiavi per pagine e sezioni
    if need_preview is None:                     # l'anteprima si genera solo se Pillow è installato
        need_preview = importlib.util.find_spec("PIL") is not None
    problems: list[dict[str, Any]] = []
    required = list(REQUIRED) + (["anteprima.png"] if need_preview else [])
    missing = [name for name in required if not (root / name).is_file() or (root / name).stat().st_size == 0]
    if missing:
        problems.append(_problem(True, "file mancanti o vuoti: " + _listed(missing)))

    data: dict[str, Any] = {}
    broken_json = []
    for path in sorted(list(root.rglob("*.json")) + list(root.rglob("*.webmanifest"))):
        name = path.relative_to(root).as_posix()
        try:
            data[name] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            broken_json.append(name)
    if broken_json:
        problems.append(_problem(True, "file di dati illeggibili (l'app li usa): " + _listed(broken_json)))
    feed = root / "feed.xml"
    if feed.is_file() and feed.stat().st_size:
        try:
            ET.parse(feed)
        except ET.ParseError as exc:
            problems.append(_problem(False, f"il feed (feed.xml) non è XML valido: {exc}"))

    pages = {p: p.read_text(encoding="utf-8", errors="replace") for p in sorted(root.rglob("*.html"))}
    ids = {p: set(IDS.findall(text)) for p, text in pages.items()}
    cut, leftovers, links, anchors = [], [], [], []
    for path, text in pages.items():
        name = path.relative_to(root).as_posix()
        if not text.rstrip().lower().endswith("</html>"):
            cut.append(name)
        visible = _visible_text(text)
        found = [label for mark, label in LEFTOVERS if mark in visible]
        word = LEFTOVER_WORDS.search(visible)
        if word:
            found.append(f"la parola «{word.group(1)}» al posto di un valore")
        if found:
            leftovers.append(f"{name} ({'; '.join(found)})")
        for href in LINK.findall(text):
            href = html.unescape(href)
            url = urlparse(href)
            if url.scheme or href.startswith(("//", "data:")) or "{" in href or "$" in href:
                continue                         # link esterni e link costruiti dagli script
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            try:
                target.relative_to(root)
            except ValueError:
                links.append(f"{name} → {href}")
                continue
            if url.path and not target.exists():
                links.append(f"{name} → {href}")
            elif url.fragment and target.suffix == ".html" and url.fragment not in ids.get(target, set()):
                anchors.append(f"{name} → {href}")
    if cut:
        problems.append(_problem(True, "pagine tagliate a metà: " + _listed(cut)))
    if leftovers:
        problems.append(_problem(False, "pagine con valori mancanti: " + _listed(leftovers)))
    if links:
        problems.append(_problem(False, f"{len(links)} link a pagine o file che non esistono: " + _listed(sorted(set(links)))))
    if anchors:
        problems.append(_problem(False, f"{len(anchors)} link a sezioni che non esistono: " + _listed(sorted(set(anchors)))))

    termo = data.get("bot/termometro.json")
    if isinstance(termo, dict) and termo.get("indice") is not None:
        value = int(termo["indice"])
        wrong = []
        for name in TERMO_PAGES:
            page = pages.get(root / name)
            match = TERMO.search(page or "")
            if page is not None and (match is None or int(match.group(1)) != value):
                wrong.append(f"{name} ({match.group(1) if match else 'nessun valore'})")
        if wrong:
            problems.append(_problem(False, f"il termometro non è uguale ovunque: il widget dice {value}, "
                                            "ma " + _listed(wrong)))
        day = (termo.get("ultima_traccia") or {}).get("data")
        home = pages.get(root / "index.html", "")
        match = LAST_TRACE.search(home)
        if day and match and match.group(1) != day:
            problems.append(_problem(False, f"ultima traccia forte: {match.group(1)} sulla pagina iniziale, "
                                            f"{day} nel widget"))

    heavy = [f"{p.relative_to(root).as_posix()} ({p.stat().st_size / 1e6:.0f} MB)"
             for p in sorted(root.rglob("*")) if p.is_file() and p.stat().st_size > MAX_MB * 1e6]
    if heavy:
        problems.append(_problem(False, f"file più grandi di {MAX_MB} MB, lenti da aprire sul telefono: "
                                        + _listed(heavy)))
    return problems


def state(db: Database) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(STATE) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def run_site_check(db: Database, root: Path, notifiers: list, need_preview: bool | None = None) -> dict[str, Any]:
    """Controlla il sito, salva l'esito e avvisa quando compare un problema nuovo o tutto torna a posto."""
    from hlxwatch.notifiers import notify_text
    before = state(db) or {}
    problems = check_site(root, need_preview)
    grave = any(p["grave"] for p in problems)
    texts = [p["testo"] for p in problems]
    result = {"at": iso(utcnow()), "ok": not problems, "grave": grave, "problemi": texts}
    db.set_state(STATE, json.dumps(result, ensure_ascii=False))
    # si confronta il tipo di problema (la parte prima dei due punti), non i conteggi che cambiano a ogni giro
    kinds = lambda items: {t.split(":", 1)[0] for t in items}   # noqa: E731
    new = kinds(texts) - kinds(before.get("problemi") or [])
    if problems:
        log.warning("Controllo del sito: %s", " | ".join(texts))
        if new or (grave and not before.get("grave")):
            tail = ("\nIl sito NON è stato pubblicato: resta online la versione precedente." if grave else
                    "\nIl sito è stato pubblicato lo stesso: sono problemi da sistemare, non bloccanti.")
            notify_text(notifiers, TITLE_FAIL, "Il controllo automatico del sito ha trovato:\n- "
                        + "\n- ".join(texts) + tail)
    else:
        log.info("Controllo del sito: tutto a posto")
        if before and before.get("ok") is False:
            notify_text(notifiers, TITLE_OK, "Il controllo automatico non trova più problemi nel sito.")
    return result
