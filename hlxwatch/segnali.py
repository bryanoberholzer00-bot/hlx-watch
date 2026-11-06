"""Prima di un annuncio: quali segnali sono comparsi prima degli annunci di Valve, e quali ci sono già per HLX.

Per cinque progetti già annunciati (Half-Life: Alyx, Counter-Strike 2, Steam Deck, Deadlock, Steam Frame) si mettono in
fila, con la data e i giorni prima dell'annuncio, i segnali che un osservatore dei file pubblici poteva vedere:
  - il nome in codice nei file (prima comparsa, da alyx_seed.json e progetti_seed.json);
  - il primo avviso che HLX Watch avrebbe dato senza conoscere il nome (prova sul passato, backtest_seed.json);
  - menu o schermate (interfaccia) e testi tradotti;
  - il sito del gioco nei certificati HTTPS (registri pubblici di Certificate Transparency, crt.sh);
  - un repository nuovo di SteamTracking.
Le date dei progetti passati sono storia chiusa, controllata a mano il 6 novembre 2026 (`PAST`). La colonna di HLX si
ricalcola a ogni giro dall'archivio: tracce HLX, sistemi (sistemi.py), domini e repository nuovi con un nome Half-Life.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

HLX_NAME = re.compile(r"hlx|half[\s_-]*life|(?<![a-z0-9])hl[\s_-]?3(?![0-9])", re.IGNORECASE)

PROJECTS = [("alyx", "Half-Life: Alyx", "2019-11-21"), ("cs2", "Counter-Strike 2", "2023-03-22"),
            ("deck", "Steam Deck", "2021-07-15"), ("deadlock", "Deadlock", "2024-08-23"),
            ("frame", "Steam Frame", "2025-11-12")]

# (chiave, segnale, spiegazione)
SIGNALS = [
    ("nome", "Nome in codice nei file", "la prima volta che il nome del progetto compare nei file pubblici"),
    ("avviso", "Primo avviso di HLX Watch", "il primo avviso che i rilevatori di oggi avrebbero dato senza conoscere il nome"),
    ("interfaccia", "Menu o schermate", "pezzi dell'interfaccia del prodotto"),
    ("testi", "Testi tradotti", "frasi del gioco nei file delle traduzioni"),
    ("sito", "Sito nei certificati", "il primo certificato HTTPS del sito del prodotto"),
    ("repo", "Repository nuovo su SteamTracking", "SteamTracking apre un archivio dedicato al gioco"),
]

# progetto -> segnale -> {"day": data} oppure {"no": spiegazione}; "almeno": la data è l'inizio dell'archivio
PAST: dict[str, dict[str, dict[str, Any]]] = {
    "alyx": {
        "nome": {"day": "2016-12-11", "almeno": True, "fonte": "item_hlvr_weapon_crowbar in Dota 2 Test"},
        "avviso": {"no": "non provato: il nome hlvr c'era già quando sono cominciati gli archivi di SteamTracking"},
        "interfaccia": {"no": "no"},
        "testi": {"no": "no"},
        "sito": {"no": "no: half-life.com esisteva già"},
        "repo": {"no": "no: aperto dopo l'uscita, il 19/03/2020"},
    },
    "cs2": {
        "nome": {"day": "2019-06-07", "fonte": "le luci di csgo nel motore Source 2, in Dota 2"},
        "avviso": {"no": "non provato: csgo era il nome di un gioco già uscito"},
        "interfaccia": {"no": "no"},
        "testi": {"no": "no"},
        "sito": {"no": "no: counter-strike.net esisteva già"},
        "repo": {"no": "no: è continuato quello di CS:GO"},
    },
    "deck": {
        "nome": {"day": "2019-11-19", "fonte": "s_rgPropValuesGyroButtonNeptune nel client di Steam"},
        "avviso": {"day": "2021-05-11", "fonte": "k_eControllerType_SteamControllerNeptune"},
        "interfaccia": {"day": "2020-09-11", "fonte": "la raccolta NeptuneGames nella libreria di Steam"},
        "testi": {"no": "no"},
        "sito": {"no": "non misurabile: il dominio era stato usato da altri prima"},
        "repo": {"no": "no"},
    },
    "deadlock": {
        "nome": {"day": "2021-03-25", "fonte": "CCitadelModifierHandleTyped in Dota 2"},
        "avviso": {"day": "2021-03-25", "fonte": "famiglia di classi nuova CCitadel"},
        "interfaccia": {"no": "no"},
        "testi": {"no": "no"},
        "sito": {"day": "2024-06-05", "fonte": "primo certificato di playdeadlock.com"},
        "repo": {"day": "2024-08-09", "fonte": "GameTracking-Deadlock"},
    },
    "frame": {
        "nome": {"day": "2022-03-31", "fonte": "Valve Deckard nei driver di SteamVR"},
        "avviso": {"day": "2022-03-31", "fonte": "Valve Deckard nell'elenco dei dispositivi Valve"},
        "interfaccia": {"day": "2022-06-15", "fonte": "deckard_devtools in SteamVR"},
        "testi": {"no": "no"},
        "sito": {"no": "non controllato"},
        "repo": {"no": "no"},
    },
}


def _days(day: str, announced: str) -> int:
    return (date.fromisoformat(announced) - date.fromisoformat(day)).days


def past_table() -> list[dict[str, Any]]:
    """Una riga per segnale, una cella per progetto, con i giorni prima dell'annuncio."""
    rows = []
    for key, label, about in SIGNALS:
        cells = []
        for pk, _, announced in PROJECTS:
            c = dict(PAST[pk][key])
            if "day" in c:
                c["days"] = _days(c["day"], announced)
            cells.append(c)
        rows.append({"key": key, "label": label, "about": about, "cells": cells})
    return rows


def _first_finding(db: Any, change_types: tuple[str, ...]) -> dict[str, Any] | None:
    """La prima voce di quel tipo marcata "possibile annuncio" (un Half-Life nuovo nel nome): un sottodominio nuovo di
    half-life.com non basta."""
    from hlxwatch.announcement import KEYWORD
    marks = ",".join("?" * len(change_types))
    for r in db.conn.execute(f"SELECT created_at, text, file_path, keywords FROM findings WHERE change_type IN ({marks}) "
                             "ORDER BY created_at", change_types).fetchall():
        if KEYWORD in (r["keywords"] or "") and HLX_NAME.search(f"{r['text'] or ''} {r['file_path'] or ''}"):
            return {"day": (r["created_at"] or "")[:10], "fonte": (r["file_path"] or r["text"] or "")[:80]}
    return None


def hlx_column(db: Any, reg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """La colonna di HLX, ricalcolata dall'archivio."""
    from hlxwatch.sistemi import pieces
    traces = sorted(reg.get("traces") or [], key=lambda t: t["first"])
    items = pieces(reg)
    out: dict[str, dict[str, Any]] = {}
    if traces:
        out["nome"] = {"day": traces[0]["first"], "almeno": True, "fonte": traces[0]["label"][:60]}
        out["avviso"] = {"day": traces[0]["first"], "fonte": "le parole chiave HLX"}
    for key in ("interfaccia", "testi"):
        first = next((p for p in items if p["system"] == key), None)
        out[key] = {"day": first["first"], "fonte": first["label"][:60]} if first else {"no": "non ancora"}
    out["sito"] = _first_finding(db, ("domain",)) or {"no": "non ancora"}
    out["repo"] = _first_finding(db, ("new_repo",)) or {"no": "non ancora"}
    for key, _, _ in SIGNALS:
        out.setdefault(key, {"no": "non ancora"})
    return out


def summary(db: Any, reg: dict[str, Any]) -> dict[str, Any]:
    hlx = hlx_column(db, reg)
    rows = past_table()
    for r in rows:
        r["hlx"] = hlx[r["key"]]
    missing = [r["label"] for r in rows if "no" in r["hlx"]]
    # segnali arrivati negli ultimi tre mesi prima di un annuncio, in qualche progetto
    late = sorted({r["label"] for r in rows for c in r["cells"] if c.get("days") is not None and c["days"] <= 90})
    return {"projects": [{"key": k, "title": t, "announced": a} for k, t, a in PROJECTS], "rows": rows,
            "missing": missing, "late": late}
