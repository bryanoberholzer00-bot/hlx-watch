"""Mappa dei collegamenti: quali pezzi di HLX stanno negli stessi posti e quali sono arrivati insieme.

Ogni traccia HLX da sola dice poco; messe insieme dicono dove Valve lavora. Qui ogni pezzo (lo stesso di
`sistemi.pieces`) si collega ai *luoghi* dove compare, cioè al programma o all'insieme di file che lo contiene
(Hammer, l'editor degli effetti, i suoni...), e agli altri pezzi arrivati nello stesso aggiornamento. Due pezzi nello
stesso luogo servono alla stessa parte del lavoro; due pezzi arrivati insieme sono stati scritti insieme.

I luoghi sono riconosciuti dal percorso del file con regole scritte a mano (`PLACES`), controllate su tutti i file
delle tracce dell'archivio (6 novembre 2026). Il disegno è calcolato qui, sempre uguale (stesso
archivio, stesso disegno), e la pagina non ha bisogno di script: luoghi a sinistra, pezzi a destra, gruppo per gruppo.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from hlxwatch.home import ICONS

# (chiave, nome, cosa contiene, tipo, regola sul percorso): la prima che corrisponde decide
PLACES: list[tuple[str, str, str, str, str]] = [
    ("hammer", "Hammer", "l'editor dei livelli, con le definizioni delle entità da mettere nelle mappe", "strumento",
     r"/tools/hammer_strings|\.fgd$"),
    ("effetti", "Editor degli effetti", "il programma delle particelle e le sue opzioni", "strumento",
     r"particles_strings|/schemas/particles/"),
    ("suoni", "Suoni", "eventi e gruppi di suoni, compresi quelli di prova", "dati",
     r"/soundevents/|/soundstacks/"),
    ("bug", "Configuratore dei bug", "l'elenco dei prodotti su cui i dipendenti segnalano bug", "strumento",
     r"/bugreporter/"),
    ("demo", "Strumento delle demo", "il programma che legge le registrazioni delle partite", "strumento",
     r"/demoinfo2/"),
    ("base", "Base comune degli strumenti", "il codice su cui sono costruiti tutti gli strumenti di sviluppo", "strumento",
     r"toolframework2_strings"),
    ("anteprima", "Anteprima dei modelli", "il programma che mostra modelli ed effetti prima di usarli", "strumento",
     r"assetpreview_strings"),
    ("rete", "Messaggi di rete", "i messaggi che gioco e server si scambiano", "gioco",
     r"^protobufs/|\.proto$"),
    ("gioco-dota", "Codice di Dota 2", "il programma del gioco Dota 2", "gioco", r"^game/dota/"),
    ("gioco-deadlock", "Codice di Deadlock", "il programma del gioco Deadlock", "gioco", r"^game/citadel/"),
    ("gioco-cs2", "Codice di Counter-Strike 2", "il programma del gioco Counter-Strike 2", "gioco", r"^game/csgo/bin/.*client"),
    ("strumenti", "Altri strumenti di sviluppo", "editor dei modelli, compilatore delle risorse e altri programmi", "strumento",
     r"_strings\.txt$"),
]
PLACE_NAMES = {k: n for k, n, _, _, _ in PLACES} | {"altro": "Altri file"}
PLACE_ABOUT = {k: a for k, _, a, _, _ in PLACES} | {"altro": "file che non rientrano negli altri luoghi"}
PLACE_KIND = {k: t for k, _, _, t, _ in PLACES} | {"altro": "dati"}
_RULES = [(k, re.compile(rx, re.I)) for k, _, _, _, rx in PLACES]
SKIP_FILES = re.compile(r"\.stringsignore$", re.I)      # elenco di servizio di SteamTracking, non un file di Valve

# misure del disegno
WIDTH = 760
TOP, ROW, GROUP_GAP, BOTTOM = 26, 24, 22, 14
PLACE_X, PIECE_X, BRACKET_X = 118, 300, 640
PLACE_GAP = 30
SHORT = 34


def place_of(path: str) -> str:
    p = (path or "").replace("\\", "/")
    for key, rx in _RULES:
        if rx.search(p):
            return key
    return "altro"


def short_label(label: str) -> str:
    """Un nome corto per il disegno: senza il commento dei file di schema, i percorsi ridotti all'ultimo pezzo."""
    t = re.sub(r"^//\s*MPropertySuppressExpr\s*=\s*", "", (label or "").strip()).strip('" ')
    t = re.sub(r"^GetAttributeTable_", "", t)
    t = re.sub(r"<br>$", "", t).strip(' "-')
    if "\\" in t:
        t = t.rsplit("\\", 1)[-1]
    if t.lower().startswith("product") or t.lower().startswith("component") or t.upper() == "HLX":
        t = "HLX nei prodotti"
    if len(t) <= SHORT:
        return t
    if " " not in t:                  # un nome solo: si taglia nel mezzo, la fine spesso distingue due nomi simili
        head = (SHORT - 1) // 2
        return t[:head] + "…" + t[-(SHORT - 1 - head):]
    return t[:SHORT - 1] + "…"


def files_by_trace(db: Any) -> dict[str, set[str]]:
    """Tutti i file in cui è comparsa ogni traccia HLX, in qualunque gioco (non solo il primo)."""
    from hlxwatch.outcomes import _is_hlx, trace_key
    out: dict[str, set[str]] = defaultdict(set)
    for r in db.conn.execute("SELECT file_path, token, text, keywords FROM findings WHERE keywords LIKE '%hlx%' "
                             "AND change_type NOT IN ('baseline', 'summary')"):
        if _is_hlx(r["keywords"]) and r["file_path"] and not SKIP_FILES.search(r["file_path"]):
            out[trace_key(r["token"], r["text"])].add(r["file_path"])
    return out


def graph(reg: dict[str, Any], files: dict[str, set[str]]) -> dict[str, Any]:
    """Pezzi, luoghi e collegamenti, senza disegno: i pezzi uniti come in sistemi.pieces."""
    from hlxwatch import sistemi
    pieces = sistemi.pieces(reg, with_traces=True)
    by_trace = {t.get("key") or t["label"]: t for t in reg.get("traces") or []}
    nodes: list[dict[str, Any]] = []
    for i, p in enumerate(pieces):
        paths: set[str] = set()
        updates: set[str] = set()
        for key in p["keys"]:
            t = by_trace.get(key) or {}
            paths |= files.get(key, set())
            if t.get("file"):
                paths.add(t["file"])
            # solo l'aggiornamento che ha portato il pezzo la prima volta: le versioni comparse dopo (per esempio
            # quando è partito l'archivio degli schemi) non sono "arrivate insieme" agli altri pezzi
            if t.get("url") and t.get("first") == p["first"]:
                updates.add(f"{t['first']}|{t['url']}")
        places = sorted({place_of(x) for x in paths if not SKIP_FILES.search(x)} or {"altro"})
        nodes.append({"id": f"p{i}", "label": p["label"], "short": short_label(p["label"]), "first": p["first"],
                      "system": p["system"], "system_name": sistemi.NAMES[p["system"]],
                      "icon": ICONS.get(p["system"], "•"), "slug": p.get("slug"), "places": places,
                      "updates": sorted(updates), "files": sorted(paths)})
    # pezzi arrivati nello stesso aggiornamento (stesso commit, stesso giorno)
    together: dict[str, list[int]] = defaultdict(list)
    for i, n in enumerate(nodes):
        for u in n["updates"]:
            together[u].append(i)
    links: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for u, idx in sorted(together.items()):
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                pair = (idx[a], idx[b])
                if pair not in seen:
                    seen.add(pair)
                    links.append({"a": idx[a], "b": idx[b], "day": u.split("|", 1)[0]})
    places: dict[str, dict[str, Any]] = {}
    for i, n in enumerate(nodes):
        for k in n["places"]:
            pl = places.setdefault(k, {"key": k, "name": PLACE_NAMES[k], "about": PLACE_ABOUT[k], "kind": PLACE_KIND[k],
                                       "pieces": []})
            pl["pieces"].append(i)
    return {"nodes": nodes, "places": places, "links": links}


def groups(g: dict[str, Any]) -> list[dict[str, Any]]:
    """Gruppi di pezzi collegati (stesso luogo o stesso aggiornamento), dal più grande."""
    n = len(g["nodes"])
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for pl in g["places"].values():
        for i in pl["pieces"][1:]:
            union(pl["pieces"][0], i)
    for ln in g["links"]:
        union(ln["a"], ln["b"])
    comp: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        comp[find(i)].append(i)
    out = []
    for members in comp.values():
        ns = sorted((g["nodes"][i] for i in members), key=lambda x: x["first"])
        places = sorted({k for x in ns for k in x["places"]},
                        key=lambda k: (-len(g["places"][k]["pieces"]), PLACE_NAMES[k]))
        updates = {u for x in ns for u in x["updates"]}
        systems = sorted({x["system_name"] for x in ns})
        out.append({"pieces": ns, "places": [g["places"][k] for k in places], "n": len(ns), "updates": len(updates),
                    "first": ns[0]["first"], "last": ns[-1]["first"], "systems": systems,
                    "together": sum(1 for ln in g["links"] if ln["a"] in members)})
    out.sort(key=lambda x: (-x["n"], x["first"]))
    return out


def layout(g: dict[str, Any], gr: list[dict[str, Any]]) -> dict[str, Any]:
    """Disegno a due colonne: i luoghi a sinistra, i pezzi a destra, gruppo per gruppo. Ogni pezzo sta sotto il suo
    luogo principale (quello del gruppo con più pezzi), in ordine di data; i pezzi ponte chiudono il blocco, vicino
    al luogo che viene dopo. Le parentesi a destra uniscono i pezzi arrivati nello stesso aggiornamento.
    Scrive x e y in nodi e luoghi e restituisce misure e parentesi."""
    y = TOP
    order: list[int] = []
    for grp in gr:
        idx = [g["nodes"].index(n) for n in grp["pieces"]]
        keys = [pl["key"] for pl in grp["places"]]               # già dal luogo più grande
        main = {i: min(g["nodes"][i]["places"], key=keys.index) for i in idx}
        for k in keys:
            block = sorted((i for i in idx if main[i] == k),
                           key=lambda i: (len(g["nodes"][i]["places"]) > 1, g["nodes"][i]["first"], g["nodes"][i]["short"]))
            for i in block:
                g["nodes"][i]["x"], g["nodes"][i]["y"] = PIECE_X, y
                order.append(i)
                y += ROW
        # luoghi: al centro dei loro pezzi in questo gruppo, senza toccarsi
        last = None
        for k in keys:
            pl = g["places"][k]
            ys = [g["nodes"][i]["y"] for i in pl["pieces"]]
            want = sum(ys) / len(ys)
            if last is not None and want < last + PLACE_GAP:
                want = last + PLACE_GAP
            pl["x"], pl["y"] = PLACE_X, want
            last = want
        y = max(y, (last or y) + PLACE_GAP / 2) + GROUP_GAP
    for pl in g["places"].values():
        pl["w"] = round(16 + 7.3 * len(pl["name"]), 1)
        pl["right"] = round(pl["x"] + pl["w"] / 2, 1)
    # parentesi degli aggiornamenti con più pezzi, spostate a destra se nello stesso tratto ce n'è già un'altra
    per_update: dict[str, list[int]] = defaultdict(list)
    for i, n in enumerate(g["nodes"]):
        for u in n["updates"]:
            per_update[u].append(i)
    brackets: list[dict[str, Any]] = []
    for u, idx in sorted(per_update.items(), key=lambda kv: min(g["nodes"][i]["y"] for i in kv[1])):
        if len(idx) < 2:
            continue
        ys = sorted(g["nodes"][i]["y"] for i in idx)
        lane = 0
        while any(b["lane"] == lane and b["y1"] <= ys[-1] and ys[0] <= b["y2"] for b in brackets):
            lane += 1
        brackets.append({"day": u.split("|", 1)[0], "y1": ys[0], "y2": ys[-1], "ys": ys, "lane": lane,
                         "x": BRACKET_X + 14 * lane, "n": len(idx)})
    return {"height": round(y - GROUP_GAP + BOTTOM), "brackets": brackets}


def summary(db: Any, reg: dict[str, Any]) -> dict[str, Any] | None:
    g = graph(reg, files_by_trace(db))
    if not g["nodes"]:
        return None
    gr = groups(g)
    drawing = layout(g, gr)
    places = sorted(g["places"].values(), key=lambda p: (-len(p["pieces"]), p["name"]))
    tools = [n for n in g["nodes"] if all(PLACE_KIND[k] == "strumento" for k in n["places"])]
    bridges = [n for n in g["nodes"] if len(n["places"]) > 1]
    for pl in places:
        pl["n"] = len(pl["pieces"])
    # l'aggiornamento che ha portato più pezzi insieme
    per_update: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in g["nodes"]:
        for u in n["updates"]:
            per_update[u].append(n)
    big = max(per_update.items(), key=lambda kv: (len(kv[1]), kv[0]), default=None)
    biggest = {"day": big[0].split("|", 1)[0], "url": big[0].split("|", 1)[1], "pieces": big[1]} if big and len(big[1]) > 1 else None
    # un nemico arrivato nello stesso aggiornamento dei suoi suoni o dei suoi effetti sarebbe un pezzo di gioco messo
    # insieme. Stare nello stesso gruppo non basta: i gruppi passano anche per programmi condivisi come Hammer
    enemy_linked = any("nemici" in {x["system"] for x in ns} and {"suono", "particelle"} & {x["system"] for x in ns}
                       for ns in per_update.values())
    return {"nodes": g["nodes"], "places": places, "links": g["links"], "groups": gr, "width": WIDTH, "height": drawing["height"],
            "brackets": drawing["brackets"],
            "n": len(g["nodes"]), "n_places": len(places), "tools": len(tools), "bridges": bridges,
            "together": len(g["links"]), "alone": [x for x in gr if x["n"] == 1], "biggest": biggest,
            "enemy_linked": enemy_linked}
