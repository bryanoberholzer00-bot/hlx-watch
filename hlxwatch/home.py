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
        from hlxwatch.fase import piece_key
        out["traces"] = [{**p, "icon": ICONS.get(p["system"], "•"), "system_name": sistemi.NAMES[p["system"]],
                          "pk": piece_key(p["label"])} for p in traces]
    g = segnali.summary(db, reg)
    have = [r["label"] for r in g["rows"] if "no" not in r["hlx"]]
    out["segnali"] = {"have": len(have), "total": len(g["rows"]), "missing": g["missing"]}
    best: dict[str, int] = {}
    for r in backtest.summary(backtest.load_seed()):
        if r.get("first"):
            best[r["project"]] = max(best.get(r["project"], 0), int(r["first"]["anticipo_giorni"]))
    out["passato"] = [{"project": k, "days": d} for k, d in sorted(best.items(), key=lambda x: -x[1])]
    out["visita"] = visit_data(db, reg)
    return out


def visit_data(db: Any, reg: dict[str, Any], days: int = 120) -> dict[str, Any]:
    """Per la riga "Dalla tua ultima visita", che il browser calcola da solo: quando HLX Watch ha trovato ogni traccia
    HLX (la prima voce nell'archivio, non la data in cui Valve l'ha aggiunta, che spesso è più vecchia) e gli orari
    delle voci nuove ad alta rilevanza degli ultimi `days` giorni."""
    from datetime import timedelta
    from hlxwatch.fase import piece_key
    from hlxwatch.outcomes import _is_hlx, trace_key
    from hlxwatch.storage.db import iso, utcnow
    found: dict[str, str] = {}
    # la prima riga inserita (id più basso), non la data più vecchia: l'archeologia e la prima comparsa aggiungono dopo
    # righe datate al commit di Valve (CHLXCoverPreviewHelper: trovato il 29/10/2026, commit del 23/09/2026)
    # le righe "present" della prima scansione completa erano già nei file quando HLX Watch è partito: non sono
    # novità, e una traccia che ha solo quelle non compare mai come nuova
    for r in db.conn.execute("SELECT token, text, keywords, created_at, change_type FROM findings "
                             "WHERE keywords LIKE '%hlx%' ORDER BY id").fetchall():
        if _is_hlx(r["keywords"]):
            key = trace_key(r["token"], r["text"])
            if r["change_type"] == "present":
                found.setdefault(key, "")
            else:
                found.setdefault(key, r["created_at"] or "")
    traces: list[dict[str, Any]] = []
    for t in sorted(reg.get("traces") or [], key=lambda t: t.get("first", ""), reverse=True):
        when = found.get(t.get("key") or trace_key(None, t["label"]))
        if when:
            traces.append({"p": piece_key(t["label"]), "l": t["label"].strip()[:80], "s": t.get("slug") or "",
                           "f": when[:19]})
    since = iso(utcnow() - timedelta(days=days))
    high = [r["created_at"][:19] for r in db.conn.execute(
        "SELECT created_at FROM findings WHERE score >= 100 AND is_new = 1 AND created_at >= ? ORDER BY created_at",
        (since,)).fetchall()]
    return {"tracce": traces, "alte": high}
