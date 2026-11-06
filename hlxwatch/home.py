"""Pagina iniziale: le risposte in quattro riquadri e le ultime tracce HLX, al posto dei lunghi elenchi di voci.

Chi arriva sul sito vuole sapere a che punto è HLX, non scorrere 100 voci. Qui si preparano, dai moduli delle pagine
di approfondimento, i dati dei riquadri (di cosa è fatto HLX, prima di un annuncio, VR o no, prova sul passato) e le
ultime tracce HLX, ognuna con il suo sistema e la sua scheda. Gli elenchi completi restano nella Ricerca, nella
Cronologia e, in fondo alla pagina, nei dettagli tecnici.
"""
from __future__ import annotations

from typing import Any

ICONS = {"particelle": "✨", "nemici": "👾", "fisica": "🧱", "suono": "🔊", "strumenti": "🛠️", "rete": "📡",
         "armi": "🔫", "mondo": "🌍", "grafica": "🖥️", "testi": "📝", "interfaccia": "🧭", "altro": "•"}
MAX_TRACES = 10


def answers(db: Any, reg: dict[str, Any], alyx_seed: dict[str, Any] | None) -> dict[str, Any]:
    from hlxwatch import backtest, segnali, sistemi
    out: dict[str, Any] = {}
    s = sistemi.summary(reg, alyx_seed, db)
    if s:
        top = s["systems"][:3]
        biggest = max(x["n"] for x in top)
        out["sistemi"] = {"n": s["n"], "count": len(s["systems"]), "empty": s["empty"],
                          "top": [{"name": x["name"], "n": x["n"], "icon": ICONS.get(x["key"], "•"),
                                   "pct": round(100 * x["n"] / biggest)} for x in top]}
        v = s["vr"]
        out["vr"] = {"hlx_vr": len(v["hlx_vr"]), "hlx_n": v["hlx_n"],
                     "alyx_vr": v["alyx"]["vr"] if v.get("alyx") else None,
                     "alyx_n": v["alyx"]["n"] if v.get("alyx") else None}
        traces = sorted(sistemi.pieces(reg), key=lambda p: p["first"], reverse=True)[:MAX_TRACES]
        out["traces"] = [{**p, "icon": ICONS.get(p["system"], "•"), "system_name": sistemi.NAMES[p["system"]]}
                         for p in traces]
    g = segnali.summary(db, reg)
    have = [r["label"] for r in g["rows"] if "no" not in r["hlx"]]
    out["segnali"] = {"have": len(have), "total": len(g["rows"]), "missing": g["missing"]}
    best: dict[str, int] = {}
    for r in backtest.summary(backtest.load_seed()):
        if r.get("first"):
            best[r["project"]] = max(best.get(r["project"], 0), int(r["first"]["anticipo_giorni"]))
    out["passato"] = [{"project": k, "days": d} for k, d in sorted(best.items(), key=lambda x: -x[1])]
    return out
