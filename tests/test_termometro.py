import json
from datetime import timedelta

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.reports.termometro import compute
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def trace(db, days_ago, text, score=115, keywords=("hlx_class",), game="cs2"):
    build = BuildInfo(game=game, source="github", ref=f"sha{text}{days_ago}", date=utcnow() - timedelta(days=days_ago))
    db.add_finding(Finding(game=game, source="github", file_path="game/bin/win64/server_strings.txt",
                           change_type="added", text=text, score=score, keywords=list(keywords)),
                   db.upsert_build(build))


def test_index_rises_with_recent_traces_and_decays(tmp_path):
    cfg, db = make(tmp_path)
    assert compute(cfg, db)["indice"] == 0 and compute(cfg, db)["livello"] == "calma"
    trace(db, 40, "CHLXOldHelper")                          # vecchia: quasi niente
    trace(db, 2, "CHLXA", score=60)                           # sotto i 100 punti: non conta
    trace(db, 3, "Half-Life mention", keywords=("half_life",))  # famiglia larga: non conta
    old = compute(cfg, db)
    assert old["indice"] < 15
    for i in range(6):
        trace(db, 1, f"CHLXCoverPreviewHelper{i}")
    t = compute(cfg, db, {"g": "Deadlock", "k": "citadel_seasonal", "d": "2026-10-30", "s": 100})
    assert 40 <= t["indice"] < 100 and t["livello"] == "tracce"
    assert t["ultima_traccia"]["giorni"] == 1 and t["ultima_traccia"]["gioco"] == "Counter-Strike 2"
    assert t["tracce_30g"] == 6 and t["ultima_voce"]["testo"] == "citadel_seasonal"
    db.close()


def test_announcement_and_drill_are_100(tmp_path):
    cfg, db = make(tmp_path)
    from hlxwatch.announcement import DRILL_STATE
    from hlxwatch.storage.db import iso
    db.set_state(DRILL_STATE, json.dumps({"at": iso(utcnow()), "ok": True}))      # prova generale appena fatta
    t = compute(cfg, db)
    assert t["prova"] and t["indice"] == 100 and t["livello"] == "prova"
    trace(db, 0, "POSSIBILE ANNUNCIO: Half-Life 3", score=200, keywords=("possible_announcement", "hl3"),
          game="steamclient")
    t = compute(cfg, db)
    assert t["indice"] == 100 and t["livello"] == "annuncio" and t["annuncio"] and not t["prova"]
    db.close()


def test_site_writes_termometro_for_the_widget(tmp_path):
    cfg, db = make(tmp_path)
    trace(db, 1, "CHLXCoverPreviewHelper")
    out = build_site(db, cfg, tmp_path / "site")
    data = json.loads((out / "bot" / "termometro.json").read_text(encoding="utf-8"))
    assert {"indice", "livello", "etichetta", "colore", "ultima_traccia", "ultima_voce", "aggiornato"} <= set(data)
    assert data["colore"].startswith("#")
    db.close()
