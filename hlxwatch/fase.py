"""Fase del progetto: ogni traccia è un pezzo di motore (grafica, suono, fisica) o un pezzo di gioco (armi, nemici,
oggetti, intelligenza artificiale)? Prima dell'annuncio, Alyx aveva nei file soprattutto pezzi di gioco; HLX per anni
soprattutto motore. Qui si conta come cambia la proporzione nel tempo.

Le regole sono parole chiave lette nel nome, controllate a mano su tutte le tracce di HLX e di Alyx (novembre 2026).
Una traccia che non corrisponde a nessuna regola resta "da capire" e si vede come tale: meglio che indovinare.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# in ordine: la prima regola che corrisponde decide
RULES: list[tuple[str, str]] = [
    # strumenti di lavoro di Valve: il progetto esiste, ma non dice a che punto è
    ("strumenti", r'"product"|"component"|bugreport|^"hlx"$'),
    # pezzi di gioco: armi, munizioni, oggetti, nemici, comportamenti dei nemici, elementi del mondo di Half-Life
    ("gioco", r"^hlvr/hlx$|energyreserve|weapon|grenade|clip|ammo|item_|crowbar|thumper|antlion|combine|headcrab|zombie|npc|enemy|cover|"
              r"projectile|nav_?markup|ai_markup|markup_ai|markupvolumetagged_(?:nav|ai)|crafting|battery|health"),
    # pezzi di motore: grafica, materiali, particelle, suono, fisica, impostazioni degli shader
    ("motore", r"hrtf|sound|soundevent|_2d_basic|_3d|material|particle|mod != |suppressexpr|fsr|render|tonemap|"
               r"shader|refract|light|physics|lightweight_body|\.exr"),
]
LABELS = {"gioco": "gioco", "motore": "motore", "strumenti": "strumenti di lavoro", "dubbio": "da capire"}


def classify(name: str) -> str:
    t = (name or "").strip().lower()
    for kind, pattern in RULES:
        if re.search(pattern, t):
            return kind
    return "dubbio"


def piece_key(label: str) -> str:
    """Lo stesso pezzo scritto in modi diversi conta una volta: `mod != hlx` e la sua versione commentata,
    CHLXCoverPreviewHelper e GetAttributeTable_CHLXCoverPreviewHelper, CDummyHLXCover e hlxcover."""
    t = (label or "").strip().lower()
    t = re.sub(r"^//\s*mpropertysuppressexpr\s*=\s*", "", t).strip('" ')
    t = re.sub(r"^getattributetable_", "", t)
    return re.sub(r"^c(?:dummy)?(?=hlx)", "", t)


def summary(reg: dict[str, Any], alyx_seed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pezzi HLX diversi per anno e tipo, accanto ai nomi di Alyx prima dell'annuncio."""
    traces = sorted(reg.get("traces") or [], key=lambda t: t["first"])
    if not traces:
        return None
    items: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for t in traces:
        kind = classify(t["label"])
        # le righe del configuratore dei bug ("product" HLX, "component" hlx...) sono un pezzo solo
        key = f"strumenti:{t['first']}" if kind == "strumenti" else piece_key(t["label"])
        label = t["label"].strip()
        if key in by_key:                                  # stesso pezzo: si tiene il nome più corto
            if len(label) < len(by_key[key]["label"]):
                by_key[key]["label"] = label
            continue
        by_key[key] = {"label": label, "first": t["first"], "year": t["first"][:4], "kind": kind}
        items.append(by_key[key])
    years: dict[str, Counter] = {}
    for it in items:
        years.setdefault(it["year"], Counter())[it["kind"]] += 1
    rows = [{"year": y, "gioco": c["gioco"], "motore": c["motore"], "strumenti": c["strumenti"], "dubbio": c["dubbio"],
             "total": sum(c.values())} for y, c in sorted(years.items())]
    total = Counter(it["kind"] for it in items)
    alyx = None
    if alyx_seed and alyx_seed.get("names"):
        pre = [n for n in alyx_seed["names"] if n["day"] < alyx_seed.get("announced", "2019-11-21")]
        c = Counter(classify(n["name"]) for n in pre)
        alyx = {"gioco": c["gioco"], "motore": c["motore"], "strumenti": c["strumenti"], "dubbio": c["dubbio"],
                "total": len(pre)}
    first_game = next((it for it in items if it["kind"] == "gioco"), None)
    return {"rows": rows, "items": items, "total": {k: total[k] for k in LABELS}, "n": len(items), "alyx": alyx,
            "first_game": first_game, "labels": LABELS}


# --- negli avvisi: il tipo di una traccia HLX appena trovata, in una parola e un simbolo ---------------------------

HLX_KEYWORDS = {"hlx", "hlx_prefix", "hlx_class", "hlx_fused", "hlx_path", "hlx_game_type"}
TAGS = {"gioco": ("🎮", "pezzo di gioco"), "motore": ("⚙️", "pezzo di motore"), "alyx": ("🔁", "eredità di Alyx")}
_alyx_names: set[str] | None = None


def alyx_names() -> set[str]:
    """I nomi hlvr di Alyx (alyx_seed.json), letti una volta sola."""
    global _alyx_names
    if _alyx_names is None:
        from hlxwatch.alyx import load_seed
        _alyx_names = {n["name"] for n in (load_seed() or {}).get("names", [])}
    return _alyx_names


def trace_kind(token: str | None, text: str | None, keywords: list[str] | None) -> str | None:
    """Tipo di una traccia HLX: 'alyx' (vecchio nome di Alyx con hlx al posto di hlvr), 'gioco', 'motore',
    'strumenti' o 'dubbio'. None se la voce non è una traccia HLX."""
    if not HLX_KEYWORDS.intersection(keywords or []):
        return None
    label = (token or text or "").strip().lower()
    names = alyx_names()
    for cand in (label.replace("hlx", "hlvr"), piece_key(label).replace("hlx", "hlvr")):
        # "hlvr" da solo è il nome del gioco, non un pezzo: "mod != hlx" non è un'eredità di Alyx
        if "hlvr" in cand and cand != "hlvr" and cand in names:
            return "alyx"
    kind = classify(token or "")
    if kind == "dubbio" and text:
        kind = classify(text)
    return kind


def alert_kind(token: str | None, text: str | None, keywords: list[str] | None) -> str | None:
    """Negli avvisi solo 'alyx', 'gioco' o 'motore': se il tipo non è chiaro, meglio niente che un'etichetta sbagliata."""
    kind = trace_kind(token, text, keywords)
    return kind if kind in ("alyx", "gioco", "motore") else None


def alert_tag(kind: str | None, with_label: bool = True) -> str:
    if kind not in TAGS:
        return ""
    icon, label = TAGS[kind]
    return f"{icon} {label}" if with_label else icon


def share(row: dict[str, Any], kind: str) -> int:
    """Percentuale arrotondata di un tipo sul totale della riga."""
    return round(100 * row.get(kind, 0) / row["total"]) if row.get("total") else 0
