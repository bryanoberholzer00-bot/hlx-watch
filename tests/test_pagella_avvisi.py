"""Pagella degli avvisi: ogni avviso mandato si annota e si giudica con quello che si sa oggi."""
import json

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.pagella_avvisi import STATE, record, report
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "hlxwatch.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_alerts_are_judged_with_what_is_known_today(tmp_path):
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="x", date=utcnow()))

    def add(game, path, text, score, keywords, kind="added"):
        return db.add_finding(Finding(game=game, source="github", file_path=path, change_type=kind, text=text,
                                      token=text, score=score, keywords=keywords), b)
    old = add("cs2", "game/csgo/pak01_dir/a.txt", "hlx_vecchia", 110, ["hlx_prefix"])
    db.mark_notified([old.id])                                  # mandato prima del registro: si ricava dall'archivio
    hlx = add("cs2", "game/csgo/pak01_dir/b.txt", "hlx_nuova", 110, ["hlx_prefix"])
    tolta = add("cs2", "game/csgo/gameinfo.gi", '"net_x" "1"', 50, ["watched_file"])
    bassa = add("dota2", "depots/branches/public", "build 1", 60, ["steam_build"], kind="build")
    utile = add("alyx", "depots/branches/public", "build 2", 55, ["steam_build"], kind="build")
    record(db, [hlx, tolta, bassa, utile])
    record(db, [hlx])                                           # la stessa voce non si conta due volte
    assert len(json.loads(db.get_state(STATE))) == 5
    # dopo: una voce tolta dalle regole, una con il punteggio abbassato sotto la soglia
    db.conn.execute("DELETE FROM findings WHERE id = ?", (tolta.id,))
    db.conn.execute("UPDATE findings SET score = 45 WHERE id = ?", (bassa.id,))
    rep = report(cfg, db)
    month = rep["mesi"][0]
    assert (month["n"], month["hlx"], month["utile"], month["rumore"], month["perc"]) == (5, 2, 1, 2, 40)
    assert month["parziale"] and rep["dal"] == utcnow().date().isoformat()
    assert {p["n"] for p in rep["peggiori"]} == {1}
    # un tipo silenziato con 👎 diventa rumore anche per gli avvisi già mandati
    from hlxwatch import noise
    db.set_state(noise.STATE, json.dumps({noise.rule_of("alyx", "build", ["steam_build"], "depots/branches/public"): {}}))
    assert report(cfg, db)["mesi"][0]["rumore"] == 3
    db.close()
