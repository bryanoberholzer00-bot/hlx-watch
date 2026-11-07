"""Le opzioni riservate a HLX: che cosa sblocca ogni "mod != hlx" negli schemi del motore.

Negli schemi che SteamTracking estrae dai giochi (DumpSource2/schemas/...) ogni campo ha un blocco di commenti; la riga
`// MPropertySuppressExpr = "mod != hlx"` nasconde il campo nell'editor quando il progetto aperto non è HLX: è
un'opzione che esiste solo per HLX. HLX Watch registrava la riga ma non il campo sotto, che è l'informazione utile
(m_flSubPixelAAScale è l'antialiasing subpixel delle particelle, m_bIsGPUParticleSystem le particelle sulla scheda
video...). A ogni scansione completa si rilegge il blocco intero e si salva l'elenco nello stato `opzioni_hlx:<gioco>`;
il sito le mostra con una spiegazione in italiano (scritta a mano in SPIEGAZIONI, il nome inglese dell'editor se manca).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import PurePosixPath
from typing import Any, Callable

log = logging.getLogger(__name__)

STATE = "opzioni_hlx:"
SCHEMAS = "DumpSource2/schemas"
EXPR_RE = re.compile(r'MPropertySuppressExpr\s*=\s*"([^"]*\bhlx\b[^"]*)"', re.I)
NAME_RE = re.compile(r'MPropertyFriendlyName\s*=\s*"([^"]*)"')
FIELD_RE = re.compile(r"^\s*(?!//)[\w:<>,* ]+?\s+(m_\w+)\s*(?:\[[^\]]*\])?\s*;")
CLASS_RE = re.compile(r"^\s*(?:class|struct)\s+(\w+)")

# campo -> che cosa fa, in italiano (controllato a mano sugli schemi di novembre 2026)
SPIEGAZIONI = {
    "m_flSubPixelAAScale": "antialiasing subpixel: bordi puliti per particelle, scie e corde sottili",
    "m_bIsGPUParticleSystem": "particelle calcolate dalla scheda video invece che dal processore",
    "m_nFlowCP": "flusso sulla superficie dell'acqua, per gli effetti che seguono la corrente",
    "m_bOnlyRenderInEffectsWaterPass": "effetto disegnato solo nel passaggio dell'acqua (riflessi e trasparenze)",
    "m_flDiffuseClamp": "limite alla luce diffusa che colpisce le particelle",
    "m_bSaturateColorPreAlphaBlend": "colore saturato prima della trasparenza",
    "m_flFogAmount": "quanta nebbia copre la particella",
    "m_bRefract2Passes": "rifrazione in due passaggi (vetro, acqua, calore più realistici)",
    "m_flFeatheringDepthMapFilter": "bordi morbidi dove l'effetto tocca muri e pavimenti",
    "m_nLightingMode": "illuminazione delle particelle: per particella o per vertice",
    "m_flLightingTessellation": "suddivisione delle particelle per una luce per vertice più precisa",
    "m_flLightingDirectionality": "quanto la luce sulle particelle dipende dalla direzione",
}


def scope(expr: str) -> str:
    """Per chi è l'opzione, dall'espressione: "solo HLX", "HLX e CS2", "HLX e Dota 2"..."""
    e = " ".join(expr.lower().split())
    others = sorted(set(re.findall(r"mod != (\w+)", e)) - {"hlx"})
    names = {"csgo": "CS2", "dota": "Dota 2", "citadel": "Deadlock"}
    base = "solo HLX" if not others else "HLX e " + " e ".join(names.get(o, o) for o in others)
    return base + (" (con una condizione)" if "||" in e else "")


def parse(text: str, path: str) -> list[dict[str, Any]]:
    """Le opzioni con un'espressione HLX in un file di schema: classe, campo, nome dell'editor, espressione."""
    out: list[dict[str, Any]] = []
    cls = PurePosixPath(path).stem
    block: list[str] = []
    for line in text.splitlines():
        m = CLASS_RE.match(line)
        if m:
            cls, block = m.group(1), []
            continue
        if line.strip().startswith("//"):
            block.append(line)
            continue
        f = FIELD_RE.match(line)
        if f and block:
            joined = "\n".join(block)
            e = EXPR_RE.search(joined)
            if e:
                n = NAME_RE.search(joined)
                out.append({"classe": cls, "campo": f.group(1), "nome": n.group(1) if n else "", "espressione": e.group(1),
                            "file": path})
        block = []
    return out


def collect(db: Any, game: str, git: Callable[[list[str]], Any]) -> list[dict[str, Any]]:
    """Dalla copia del repository (git grep e git show): le opzioni HLX del gioco, salvate nello stato."""
    proc = git(["grep", "-l", "-I", "-i", "-E", "-e", r"MPropertySuppressExpr.*hlx", "HEAD", "--", SCHEMAS])
    if getattr(proc, "returncode", 1) not in (0, 1):
        return []
    files = [ln.split(":", 1)[-1] for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    found: list[dict[str, Any]] = []
    for path in sorted(set(files)):
        show = git(["show", f"HEAD:{path}"])
        if getattr(show, "returncode", 1) != 0:
            continue
        found += parse(show.stdout.decode("utf-8", "replace"), path)
    value = json.dumps(found, ensure_ascii=False, sort_keys=True)
    if db.get_state(STATE + game) != value:
        db.set_state(STATE + game, value)
    if found:
        log.info("[%s] opzioni riservate a HLX negli schemi: %d", game, len(found))
    return found


def summary(db: Any) -> list[dict[str, Any]]:
    """Le opzioni di tutti i giochi, una per campo (la stessa opzione può stare in più tipi di effetto), con i giochi,
    i tipi di effetto, la spiegazione e la prima comparsa."""
    from hlxwatch.sources.first_seen import line_hash
    merged: dict[str, dict[str, Any]] = {}
    # il giorno in cui SteamTracking ha cominciato a pubblicare gli schemi di quel gioco: una riga comparsa quel giorno
    # c'era già prima, e la data vale "almeno dal"
    start = {g: (d or "")[:10] for g, d in db.conn.execute(
        "SELECT game, MIN(commit_date) FROM first_seen WHERE file_path LIKE ? GROUP BY game", (SCHEMAS + "/%",)).fetchall()}
    for key, value in db.conn.execute("SELECT key, value FROM state WHERE key LIKE ?", (STATE + "%",)).fetchall():
        game = key[len(STATE):]
        try:
            items = json.loads(value or "[]")
        except ValueError:
            continue
        for it in items:
            o = merged.setdefault(it["campo"], {**it, "giochi": set(), "classi": set(), "dal": None, "almeno": False})
            o["giochi"].add(game)
            o["classi"].add(it["classe"])
            # la prima comparsa della riga dell'espressione in quel file (la scansione completa la cerca da sola)
            row = db.conn.execute("SELECT MIN(commit_date), MAX(at_least) FROM first_seen WHERE game = ? AND file_path = ? "
                                  "AND line_hash = ?", (game, it["file"], line_hash(f'// MPropertySuppressExpr = "{it["espressione"]}"'))).fetchone()
            if row and row[0] and (o["dal"] is None or row[0] < o["dal"]):
                o["dal"], o["almeno"] = row[0], bool(row[1]) or row[0][:10] == start.get(game)
    out = []
    for o in merged.values():
        o["giochi"] = sorted(o["giochi"])
        o["classi"] = sorted(o["classi"])
        o["spiegazione"] = SPIEGAZIONI.get(o["campo"], "")
        o["per"] = scope(o["espressione"])
        o["solo"] = o["per"].startswith("solo")
        out.append(o)
    out.sort(key=lambda o: (not o["per"].startswith("solo"), o["dal"] or "9999", o["campo"]))
    return out
