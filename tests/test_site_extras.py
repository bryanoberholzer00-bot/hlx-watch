import json
from datetime import timedelta

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.reports.site_extras import area_of, hardware_data, schema_url, source_status
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_schema_url():
    assert schema_url("cs2", "DumpSource2/schemas/server/CPulseCell_Outflow_PlayDynamicVCD.h") == \
        "https://s2v.app/SchemaExplorer/cs2/server/CPulseCell_Outflow_PlayDynamicVCD"
    assert schema_url("dota2", "DumpSource2/schemas/particles/C_OP_RenderRopes.h").endswith("/dota2/particles/C_OP_RenderRopes")
    assert schema_url("alyx", "DumpSource2/schemas/server/X.h") is None              # Alyx non è nello Schema Explorer
    assert schema_url("cs2", "game/bin/win64/tools/hammer_strings.txt") is None


def test_area_of():
    cases = {
        ("game/citadel/steam.inf", "added"): "build",
        ("manifests/manifest_731.txt", "added"): "build",
        ("DumpSource2/schemas/particles/C_OP_RenderRopes.h", "added"): "schema",
        ("DumpSource2/convars.txt", "added"): "convar",
        ("game/core/ai_basenpc.fgd", "added"): "convar",
        ("Protobufs/dota_gcmessages.proto", "added"): "convar",
        ("game/citadel/resource/localization/citadel_main/citadel_main_english.txt", "added"): "loc",
        ("game/dota/pak01_dir/panorama/layout/hud.vxml", "added"): "ui",
        ("game/csgo/pak01_dir.txt", "added"): "assets",
        ("game/csgo/readonly_tools_asset_info.txt", "added"): "assets",
        ("game/bin/win64/tools/hammer_strings.txt", "added"): "engine_strings",
        ("game/csgo/bin/win64/client_strings.txt", "added"): "game_strings",
        ("game/dota/pak01_dir/scripts/npc/npc_heroes.txt", "added"): "data",
        ("anything", "pulse"): "schema",
        ("SteamTracking/GameTracking-HLX", "new_repo"): "build",
        ("weird.bin", "added"): "other",
    }
    for (path, kind), area in cases.items():
        assert area_of(path, kind) == area, (path, area_of(path, kind))


def test_source_status_and_hardware(tmp_path):
    cfg, db = make(tmp_path)
    old = utcnow() - timedelta(days=30)
    db.upsert_build(BuildInfo(game="cs2", source="github", ref="a" * 40, date=utcnow() - timedelta(days=2)))
    db.upsert_build(BuildInfo(game="dota2", source="github", ref="b" * 40, date=old))
    db.upsert_build(BuildInfo(game="deadlock", source="github", ref="c" * 40, date=utcnow(), message="[archeologia] x"))
    db.set_state("summary:cs2:" + "a" * 40, json.dumps({"title": "t", "url": "u", "created_at": utcnow().isoformat()}))
    db.set_state("health:github", json.dumps({"fails": 2, "alerted": False, "last_ok": old.isoformat()}))
    db.set_state("health:steam_pics", json.dumps({"fails": 0}))
    st = {r["name"]: r for r in source_status(cfg, db)["repos"]}
    assert st["cs2"]["days"] == 2 and not st["cs2"]["stale"] and st["cs2"]["summary"]
    assert st["dota2"]["stale"] and st["dota2"]["days"] >= 29
    assert st["deadlock"]["last_commit"] is None and st["deadlock"]["stale"]       # l'archeologia non conta
    assert not st["steamvr"]["stale"]                                               # solo i giochi principali
    assert [p["source"] for p in source_status(cfg, db)["problems"]] == ["github"]

    db.set_state("structure:steamvr:drivers", json.dumps(["deckard", "frame_hmd"]))
    db.set_state("codenames:seen", json.dumps({"deckard": {"posto": "vr_device"}, "csgo": {"posto": "mod"}}))
    bid = db.upsert_build(BuildInfo(game="steamvr", source="github", ref="d" * 40, date=utcnow(), url="https://x/commit/d"))
    db.add_finding(Finding(game="steamvr", source="github", file_path="drivers/deckard/resources/input_profile.json",
                           change_type="added", text='"Name" : "EV3"', score=55, keywords=["watched_file"]), bid)
    db.add_finding(Finding(game="steamclient", source="github", file_path="ClientExtracted/x.js", change_type="added",
                           text="fremont", score=35, keywords=["valve_hardware"]), bid)
    db.add_finding(Finding(game="cs2", source="github", file_path="x.txt", change_type="added", text="hlx", score=100,
                           keywords=["hlx"]), bid)
    hw = hardware_data(cfg, db)
    assert hw["drivers"] == ["deckard", "frame_hmd"] and hw["devices"] == ["deckard"]
    assert sorted(f["text"] for f in hw["findings"]) == ['"Name" : "EV3"', "fremont"]

    out = build_site(db, cfg, tmp_path / "site")
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Stato delle fonti" in index and "fermo da più di due settimane" in index
    hardware = (out / "hardware.html").read_text(encoding="utf-8")
    assert "deckard" in hardware and "2AES4" in hardware and "EV3" in hardware
    metodo = (out / "metodologia.html").read_text(encoding="utf-8")
    assert "Glossario" in metodo and "Da dove vengono i dati" in metodo and "Cosa HLX Watch non fa" in metodo
    assert 'href="hardware.html"' in index
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert {r["area"] for r in data} >= {"data", "ui"}
    db.close()
