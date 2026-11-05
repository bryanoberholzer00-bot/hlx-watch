"""Di cosa è fatto HLX: le tracce HLX divise per sistema (nemici, armi, fisica, suono, particelle...), e la domanda
"HLX è un gioco VR?" messa davanti ai dati.

`fase.py` divide le tracce in due (pezzi di gioco e pezzi di motore). Qui la divisione è più fine: dice *dove* Valve
lavora, e un sistema che comincia a riempirsi è già una notizia. Le regole sono parole lette nel nome della traccia,
controllate a mano su tutte le 32 tracce dell'archivio (5 novembre 2026); la prima che corrisponde decide, e una
traccia che non corrisponde a niente resta "altro", visibile come tale. Lo stesso pezzo scritto in modi diversi
(`mod != hlx` e la sua versione commentata) conta una volta, come in fase.py.

**VR o no.** Prima dell'annuncio, i 36 nomi di Half-Life: Alyx nei file pubblici erano armi, munizioni, oggetti e
intelligenza artificiale: nessuno parlava di mani, controller o teletrasporto. L'unica cosa che diceva "VR" era il
nome del progetto, HLVR. Quindi l'assenza di parole VR nelle tracce HLX non prova niente, e la pagina lo dice. I
conteggi si calcolano a ogni giro; gli indizi uno per uno (`CLUES`) sono controllati a mano, con la fonte.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from hlxwatch.fase import piece_key

# (chiave, nome, cosa contiene, regola): in ordine, la prima che corrisponde decide
SYSTEMS: list[tuple[str, str, str, str]] = [
    ("strumenti", "Strumenti di lavoro", "il configuratore dei bug e gli strumenti per le demo",
     r'"product"|"component"|bugreport|^"hlx"$|^hlvr/hlx$'),
    ("rete", "Comandi del giocatore e rete", "il messaggio con i comandi del giocatore",
     r"usercmd"),
    ("nemici", "Nemici e intelligenza artificiale", "comportamenti dei nemici, coperture, navigazione",
     r"thumper|antlion|headcrab|zombie|npc|enemy|cover|nav_?markup|ai_markup|markupvolumetagged"),
    ("armi", "Armi, proiettili e oggetti", "armi, proiettili, munizioni e oggetti da raccogliere",
     r"weapon|grenade|clip|ammo|item_|crowbar|projectile|crafting|battery|health"),
    ("mondo", "Mondo di gioco", "elementi del mondo di Half-Life, come le barriere dei Combine",
     r"barrier|combine|door|elevator"),
    ("fisica", "Fisica", "corpi e oggetti fisici",
     r"physics|lightweight_body|ragdoll|rope|cloth"),
    ("suono", "Suono", "suono spaziale e gruppi di suoni",
     r"hrtf|sound|_2d_basic|_3d|voicegraph|audio"),
    ("particelle", "Particelle ed effetti", "opzioni dell'editor degli effetti, rifrazione, texture di prova",
     r"particle|shlit|refract|mod != "),
    ("grafica", "Grafica e prestazioni", "impostazioni grafiche e di resa",
     r"fsr|render|tonemap|shader|light|material|\.exr"),
]
NAMES = {k: name for k, name, _, _ in SYSTEMS} | {"altro": "Altro"}
ABOUT = {k: about for k, _, about, _ in SYSTEMS} | {"altro": "nomi che non si capisce a cosa servano"}
ORDER = [k for k, _, _, _ in SYSTEMS] + ["altro"]

# parole che in un nome parlano di realtà virtuale (mani, controller, visore, modi di muoversi tipici della VR)
VR_WORDS = re.compile(r"(?<![a-z])vr(?![a-z])|_vr|vr_|hand|controller|teleport|haptic|grab|gravity_?glove|hmd|headset|"
                      r"steamvr|finger|holster|backpack|wrist|room_?scale|seated|snap_?turn|smooth_?turn|tracked", re.I)

# indizi uno per uno, controllati a mano: verso "vr", verso "schermo" (gioco tradizionale) o "neutro"
CLUES: list[dict[str, str]] = [
    {"verso": "schermo", "peso": "debole", "data": "2022-06-09", "titolo": "I comandi del giocatore senza campi VR",
     "testo": "Il messaggio CHLXUserCmdPB, comparso in Dota 2, contiene solo i campi comuni a tutti i giochi "
              "(tasti, direzione dello sguardo, movimento, mouse): niente posizioni di mani o visore. Quello di Deadlock, "
              "comparso lo stesso giorno, aveva già campi suoi; quello di HLX no, ed è stato tolto nel marzo 2023: può "
              "essere stato solo un segnaposto.",
     "url": "https://github.com/SteamTracking/GameTracking-Dota2/commit/7e011a5768a240cc2fe0815340edbbd524a003b5"},
    {"verso": "schermo", "peso": "debole", "data": "2025-02-25", "titolo": "Un'impostazione FSR 3",
     "testo": "r_hlx_fsr3_min_reactiveness è un'impostazione di AMD FSR 3, la tecnica che alza risoluzione e "
              "fotogrammi pensata soprattutto per i giochi su schermo. È però nelle impostazioni di Deadlock, e può "
              "essere solo un nome rimasto da codice condiviso.", "url": ""},
    {"verso": "vr", "peso": "debole", "data": "2021-03-25", "titolo": "\"hlvr/hlx\" nello strumento delle demo",
     "testo": "Un commento nello strumento che legge le demo mette insieme hlvr (Alyx) e hlx: le due cose sono trattate "
              "nello stesso modo. Dice che HLX viene dal codice di Alyx, non che sia in VR.", "url": ""},
    {"verso": "neutro", "peso": "", "data": "2021-09-16", "titolo": "Un nome di Alyx rinominato",
     "testo": "hlvr_combine_barrier_cell_spacing (Alyx, 2019) è ricomparso come hlx_combine_barrier_cell_spacing nel "
              "2021: HLX ha ereditato pezzi di Alyx. Una barriera dei Combine però esiste in VR come su schermo.", "url": ""},
    {"verso": "neutro", "peso": "", "data": "2021-03-04", "titolo": "Suono spaziale (HRTF)",
     "testo": "hlx.hrtf_basic è un effetto di suono spaziale. In VR è fondamentale, ma si usa anche nei giochi su "
              "schermo, CS2 compreso.", "url": ""},
]


def system_of(label: str) -> str:
    t = (label or "").strip().lower()
    for key, _, _, pattern in SYSTEMS:
        if re.search(pattern, t):
            return key
    return "altro"


def pieces(reg: dict[str, Any]) -> list[dict[str, Any]]:
    """Le tracce del registro degli esiti come pezzi diversi, ognuno con il suo sistema."""
    items: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for t in sorted(reg.get("traces") or [], key=lambda t: t["first"]):
        system = system_of(t["label"])
        key = f"strumenti:{t['first']}" if system == "strumenti" else piece_key(t["label"])
        label = t["label"].strip()
        games = set(t.get("games") or [])
        if key in by_key:                          # stesso pezzo: si tiene il nome più corto e si uniscono i giochi
            p = by_key[key]
            if len(label) < len(p["label"]):
                p["label"], p["slug"] = label, t.get("slug")
            p["games"] = sorted(set(p["games"]) | games)
            continue
        by_key[key] = {"label": label, "first": t["first"], "system": system, "slug": t.get("slug"),
                       "games": sorted(games), "status": t.get("status_label") or t.get("status") or ""}
        items.append(by_key[key])
    return items


def summary(reg: dict[str, Any], alyx_seed: dict[str, Any] | None) -> dict[str, Any] | None:
    items = pieces(reg)
    if not items:
        return None
    systems = []
    for key in ORDER:
        its = [p for p in items if p["system"] == key]
        if its:
            systems.append({"key": key, "name": NAMES[key], "about": ABOUT[key], "n": len(its),
                            "first": its[0]["first"], "last": its[-1]["first"], "items": its})
    systems.sort(key=lambda s: (-s["n"], ORDER.index(s["key"])))
    top = max(s["n"] for s in systems)
    for s in systems:
        s["pct"] = round(80 * s["n"] / top, 1)          # 80%: accanto alla barra più lunga resta posto per il numero
    years = sorted({p["first"][:4] for p in items})
    by_year = {y: Counter(p["system"] for p in items if p["first"][:4] == y) for y in years}
    rows = [{"key": s["key"], "name": s["name"], "cells": [by_year[y][s["key"]] for y in years]} for s in systems]
    # novità: sistemi che compaiono per la prima volta nell'ultimo anno con tracce
    new_last = [s["name"] for s in systems if s["first"][:4] == years[-1]]
    return {"n": len(items), "systems": systems, "years": years, "rows": rows, "new_last": new_last,
            "last_year": years[-1], "vr": vr_summary(reg, items, alyx_seed)}


def vr_summary(reg: dict[str, Any], items: list[dict[str, Any]], alyx_seed: dict[str, Any] | None) -> dict[str, Any]:
    hlx_vr = [p for p in items if VR_WORDS.search(p["label"])]
    hlx_steamvr = [p for p in items if "steamvr" in p["games"]]
    out: dict[str, Any] = {"hlx_n": len(items), "hlx_vr": hlx_vr, "hlx_steamvr": len(hlx_steamvr), "clues": CLUES,
                           "alyx": None}
    if alyx_seed and alyx_seed.get("names"):
        pre = [n for n in alyx_seed["names"] if n["day"] < alyx_seed.get("announced", "2019-11-21")]
        alyx_vr = [n for n in pre if VR_WORDS.search(n["name"].lower().replace("hlvr", ""))]
        renamed = [p for p in items
                   if "hlx" in p["label"].lower() and p["label"].lower().replace("hlx", "hlvr") in
                   {n["name"] for n in alyx_seed["names"]}]
        out["alyx"] = {"n": len(pre), "vr": len(alyx_vr), "steamvr": sum(1 for n in pre if n.get("repo") == "steamvr"),
                       "announced": alyx_seed.get("announced", "2019-11-21"), "renamed": renamed}
    counts = Counter(c["verso"] for c in CLUES)
    out["counts"] = {"vr": counts["vr"], "schermo": counts["schermo"], "neutro": counts["neutro"]}
    return out
