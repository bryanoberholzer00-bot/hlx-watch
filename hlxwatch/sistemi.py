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


def summary(reg: dict[str, Any], alyx_seed: dict[str, Any] | None, db: Any = None) -> dict[str, Any] | None:
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
            "last_year": years[-1], "vr": vr_summary(reg, items, alyx_seed), "twins": twins(reg),
            "lists": engine_lists(db) if db is not None else None}


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


# --------------------------------------------------------------- HLX accanto agli altri giochi
# pezzi di HLX con un fratello in un altro gioco, scritto allo stesso modo. Date dalla storia dei repository
# SteamTracking (git log -S su GameTracking-Deadlock e GameTracking-HalfLifeAlyx, 5 novembre 2026): storia chiusa.
TWINS: list[dict[str, str]] = [
    {"hlx": "hlx.hrtf_basic", "hlx_first": "2021-03-04", "twin": "hlvr.hrtf_basic", "game": "Half-Life: Alyx",
     "twin_first": "2020-03-28", "rel": "ereditato da Alyx",
     "note": "L'effetto di suono spaziale di Alyx ha avuto una versione HLX; a gennaio 2026 è diventato core.hrtf_basic, "
             "del motore comune. Per Alyx la data è quella del primo aggiornamento pubblico dopo l'uscita."},
    {"hlx": "hlx_combine_barrier_cell_spacing", "hlx_first": "2021-09-16", "twin": "hlvr_combine_barrier_cell_spacing",
     "game": "Half-Life: Alyx", "twin_first": "2019-06-13", "rel": "ereditato da Alyx",
     "note": "Un'impostazione delle barriere dei Combine di Alyx, ricomparsa con hlx al posto di hlvr."},
    {"hlx": "CHLXUserCmdPB", "hlx_first": "2022-06-09", "twin": "CCitadelUserCmdPB", "game": "Deadlock",
     "twin_first": "2022-06-09", "rel": "lo stesso giorno",
     "note": "Il messaggio con i comandi del giocatore: quello di Deadlock e quello di HLX sono comparsi nello stesso "
             "commit di Dota 2. Deadlock è stato annunciato 806 giorni dopo."},
    {"hlx": "hlx_2d_basic", "hlx_first": "2024-02-07", "twin": "citadel_2d_basic", "game": "Deadlock",
     "twin_first": "2026-09-29", "rel": "prima HLX",
     "note": "Un gruppo di suoni: in HLX (file di CS2) da febbraio 2024, in Deadlock solo da settembre 2026."},
    {"hlx": "r_hlx_fsr3_min_reactiveness", "hlx_first": "2025-02-25", "twin": "r_citadel_fsr3_min_reactiveness",
     "game": "Deadlock", "twin_first": "2025-05-08", "rel": "passato da HLX a Deadlock",
     "note": "Un'impostazione di AMD FSR 3 nelle impostazioni di Deadlock: prima col nome HLX, poi rinominata Citadel."},
    {"hlx": "hlx_physics_particle_spawner", "hlx_first": "2026-04-21", "twin": "generic_physics_particle_spawner",
     "game": "motore comune", "twin_first": "2024-08-10", "rel": "versione HLX di un pezzo comune",
     "note": "Il generatore comune c'era già (in Deadlock dal primo aggiornamento pubblico, agosto 2024). In CS2 è "
             "comparso col nome HLX ad aprile 2026 ed è stato tolto pochi mesi dopo: lì sembra tornato al nome comune."},
]

MOD_RE = re.compile(r"\bmod\s*[!=]=\s*([A-Za-z0-9_]+)")
BUG_RE = re.compile(r'"(product|component)"\s+"([^"]+)"', re.I)
BUG_FILE = re.compile(r"bugreporter/config\.txt$", re.I)
LISTS_STATE = "engine_lists:"
GAME_MODS = {"hlx": "HLX", "csgo": "CS2", "dota": "Dota 2", "citadel": "Deadlock", "hlvr": "Half-Life: Alyx"}


class EngineLists:
    """Durante la scansione completa: per ogni "mod" (gioco) quante opzioni degli strumenti lo nominano
    (`mod != hlx`, `mod == csgo`...), e i prodotti e componenti del bug reporter. Si salva nello stato
    `engine_lists:<gioco>`, così la pagina si aggiorna a ogni scansione."""

    def __init__(self) -> None:
        self.mods: dict[str, set[str]] = {}
        self.bug: dict[str, set[str]] = {"product": set(), "component": set()}

    def add(self, file_path: str, text: str) -> None:
        if "mod" in text:
            expr = " ".join(piece_key(text).split())
            for m in MOD_RE.finditer(text):
                self.mods.setdefault(m.group(1).lower(), set()).add(expr)
        if BUG_FILE.search(file_path or ""):
            for m in BUG_RE.finditer(text):
                self.bug[m.group(1).lower()].add(m.group(2).strip())

    def save(self, db: Any, game: str) -> None:
        import json
        from hlxwatch.storage.db import iso, utcnow
        db.set_state(f"{LISTS_STATE}{game}", json.dumps(
            {"mods": {k: len(v) for k, v in sorted(self.mods.items())}, "products": sorted(self.bug["product"]),
             "components": sorted(self.bug["component"]), "quando": iso(utcnow())}, ensure_ascii=False))


def engine_lists(db: Any, games: tuple[str, ...] = ("cs2", "dota2", "deadlock")) -> dict[str, Any] | None:
    """Gli elenchi dei tre giochi insieme: il file degli strumenti è lo stesso, per ogni mod si tiene il conteggio più
    alto; i prodotti del bug reporter si uniscono."""
    import json
    mods: Counter = Counter()
    products: set[str] = set()
    components: set[str] = set()
    when = ""
    for g in games:
        raw = db.get_state(f"{LISTS_STATE}{g}")
        if not raw:
            continue
        d = json.loads(raw)
        for k, v in (d.get("mods") or {}).items():
            mods[k] = max(mods[k], int(v))
        products |= set(d.get("products") or [])
        components |= set(d.get("components") or [])
        when = max(when, d.get("quando") or "")
    if not mods and not products:
        return None
    rows = [{"mod": k, "game": GAME_MODS.get(k, k), "n": n} for k, n in mods.most_common()]
    return {"mods": rows, "products": sorted(products, key=str.lower), "components": sorted(components, key=str.lower),
            "when": when[:10], "deadlock_in_mods": "citadel" in mods,
            "deadlock_in_bug": any(p.lower() in ("citadel", "deadlock") for p in products | components)}


def twins(reg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """I gemelli con la distanza in giorni (positiva: il gemello è arrivato dopo HLX) e la scheda della traccia HLX."""
    from datetime import date
    slugs = {t["label"].strip().lower(): t.get("slug") for t in (reg or {}).get("traces") or []}
    out = []
    for t in TWINS:
        days = (date.fromisoformat(t["twin_first"]) - date.fromisoformat(t["hlx_first"])).days
        out.append({**t, "days": days, "slug": slugs.get(t["hlx"].lower())})
    return out
