"""Voci fuori posto in alta rilevanza (3 novembre 2026): profilo di DOOM VFR per Steam Frame a 100, file di
traduzione stagionale di Deadlock rimasto a 100, link della scansione completa al repository invece che al commit."""
import json
from datetime import datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reevaluate import reevaluate
from hlxwatch.reports.site import apply_first_seen, first_seen_days
from hlxwatch.sources.first_seen import line_hash
from hlxwatch.sources.structure import StructureWatch
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


class FakeClient:
    def __init__(self, entries):
        self.entries = entries

    def get_json(self, path, params=None):
        if path.endswith("drivers/frame_controller/resources/input"):
            return [{"name": n, "type": "file", "html_url": ""} for n in self.entries]
        return None


def test_controller_bindings_score_60_unless_keyword(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient(["frame_controller_profile.json"])
    w = StructureWatch(cfg, db, client)
    assert w.run() == []
    client.entries += ["doomvfr_bindings_frame_controller.json", "hlx_bindings_frame_controller.json"]
    found = {f.file_path.rsplit("/", 1)[1]: f.score for f in w.run()}
    assert found["doomvfr_bindings_frame_controller.json"] == 60          # gioco già uscito: sito, non alta rilevanza
    assert found["hlx_bindings_frame_controller.json"] >= 120              # un nome con HLX resta alto
    db.close()


def test_reevaluate_rescores_old_structure_and_localization_entries(tmp_path):
    cfg, db = make(tmp_path)
    bid = db.upsert_build(BuildInfo(game="steamvr", source="github", ref="structure:x", date=datetime.now(timezone.utc)))
    vr = Finding(game="steamvr", source="structure", change_type="structure", score=100, keywords=["structure_new"],
                 file_path="drivers/frame_controller/resources/input/doomvfr_bindings_frame_controller.json",
                 text="gioco con i comandi per i controller Steam Frame: file nuova", token="structure:a")
    hw = Finding(game="steamvr", source="structure", change_type="structure", score=100, keywords=["structure_new"],
                 file_path="drivers/roy", text="driver di SteamVR: cartella nuova 'roy'", token="structure:b")
    dl = Finding(game="deadlock", source="github", change_type="localization", score=100, keywords=["loc_file_new"],
                 file_path="game/citadel/resource/localization/citadel_seasonal/citadel_seasonal_english.txt",
                 text="nuovo file di localizzazione citadel_seasonal_english.txt", token="locfile:a")
    cs = Finding(game="cs2", source="github", change_type="localization", score=100, keywords=["loc_file_new"],
                 file_path="game/csgo/resource/zeta_english.txt", text="nuovo file", token="locfile:b")
    for f in (vr, hw, dl, cs):
        db.add_finding(f, bid)
    reevaluate(cfg, db)
    score = {r["token"]: r["score"] for r in db.conn.execute("SELECT token, score FROM findings")}
    assert score == {"structure:a": 60, "structure:b": 100, "locfile:a": 45, "locfile:b": 100}
    db.close()


def test_late_line_links_to_first_commit(tmp_path):
    cfg, db = make(tmp_path)
    db.set_first_seen("cs2", "game/bin/win64/tools/hammer_strings.txt", line_hash("hlxcoverpreview"), "hlxcoverpreview",
                      "10f3693c381475016b128c549928d14eb3adfcf2", "2026-09-23T00:05:28+00:00", False)
    db.set_first_seen("cs2", "a.txt", line_hash("hlx_old"), "hlx_old", "aaaa", "2023-06-07T00:00:00+00:00", True)
    days = first_seen_days(db)
    tree = "https://github.com/SteamTracking/GameTracking-CS2/tree/b73cfdeb35"
    item = apply_first_seen({"source": "fullscan", "change_type": "added", "game": "cs2", "text": "hlxcoverpreview",
                             "file_path": "game/bin/win64/tools/hammer_strings.txt", "build_url": tree, "day": "2026-10-29"}, days)
    assert item["day"] == "2026-09-23"
    assert item["build_url"] == "https://github.com/SteamTracking/GameTracking-CS2/commit/10f3693c381475016b128c549928d14eb3adfcf2"
    old = apply_first_seen({"source": "fullscan", "change_type": "present", "game": "cs2", "text": "hlx_old",
                            "file_path": "a.txt", "build_url": tree, "day": "2026-10-29"}, days)
    assert old["build_url"] == tree and old["day_at_least"]          # "almeno dal": il commit non è quello vero
    db.close()
