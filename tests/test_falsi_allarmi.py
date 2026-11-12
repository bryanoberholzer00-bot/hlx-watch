"""Controllo generale dei falsi allarmi (12/11/2026): le regole nuove valgono anche per le voci già registrate."""
import json

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reevaluate import reevaluate
from hlxwatch.sources.github_tracking import watched_line, watched_rules
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "hlxwatch.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def add(db, game, path, kind, text, score, keywords, token=None, context=None, source="github"):
    b = db.upsert_build(BuildInfo(game=game, source=source, ref=f"{game}:{path}:{text}"[:200], date=utcnow()))
    db.add_finding(Finding(game=game, source=source, file_path=path, change_type=kind, text=text, score=score,
                           keywords=keywords, token=token, context=context), b)


def test_gameinfo_settings_are_not_alerts():
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    rules = watched_rules(Config(raw, PACKAGE_ROOT / "x.yaml"))
    gi = "game/csgo/gameinfo.gi"
    for line in ('"net_limit_sv_recvbuffer_kb" "128"', '"net_limit_sv_message_process_time_ms_warn_rate_per_sec" "128" // 2ms/tick',
                 '"cl_interp_parallel"\t\t\t"1"', "ForceUseBuildVersion\t1", "// commento", "{"):
        assert not watched_line(rules, gi, line), line
    for line in ("Game\thlx", "Mod\t\thlx", "AddonRoot\t\t\thlx_addons", 'GameData\t"hlx.fgd"', 'ModuleName "server.dll"'):
        assert watched_line(rules, gi, line), line
    assert not watched_line(rules, "game/csgo/scripts/x.txt", "Game hlx")                  # file non sorvegliato


def test_reevaluate_cleans_the_old_false_alarms(tmp_path):
    cfg, db = make(tmp_path)
    gi = "game/csgo/gameinfo.gi"
    add(db, "cs2", gi, "added", '"net_limit_sv_recvbuffer_kb" "128"', 50, ["watched_file"])
    add(db, "cs2", gi, "added", "ForceUseBuildVersion\t1", 50, ["watched_file"])
    add(db, "cs2", gi, "added", "Game\thlx", 50, ["watched_file"])
    # VoXenCo: hl_nomi_attaccati nei dati di un'app, oggi escluso per pics/app/
    add(db, "steam", "app/5550140", "new_app", "Nuova app 5550140: The Hollowing Incremental [Game] dev=Max Teslia pub=VoXenCo",
        80, ["private_app", "hl_nomi_attaccati"], token="app:5550140", source="steam_apps")
    # Triton: registrati a 60 prima del controllo "già noto"
    db.set_state("hwids:libsdl-org/SDL:src/joystick/usb_ids.h",
                  json.dumps({"STEAM_TRITON": "0x1302", "STEAM_TRITON_BLE": "0x1303", "STEAM_CONTROLLER_DONGLE": "0x1142"}))
    for name, value in (("STEAM_TRITON", "0x1302"), ("STEAM_TRITON_BLE", "0x1303")):
        add(db, "valvehw", "libsdl-org/SDL/src/joystick/usb_ids.h", "hardware_id",
            f"Hardware Valve: nome nuovo in SDL: {name} ({value})", 60, ["hardware_id"], token=f"hwid:{name.lower()}",
            context=json.dumps({"nome": name, "id_usb": value}), source="hardware_ids")
    reevaluate(cfg, db)
    left = {r["text"]: (r["score"], r["change_type"]) for r in db.conn.execute("SELECT text, score, change_type FROM findings")}
    assert "Game\thlx" in [t for t in left]
    assert not [t for t in left if "net_limit" in t or "ForceUseBuildVersion" in t]
    assert not [t for t, v in left.items() if v[1] == "new_app"]                        # VoXenCo: niente più segnali
    triton = {t: s for t, (s, k) in left.items() if k == "hardware_id"}
    assert len(triton) == 2 and all(s == 35 and "già noto" in t for t, s in triton.items())
    reevaluate(cfg, db)                                                                 # una seconda volta: niente cambia
    assert {r["text"]: r["score"] for r in db.conn.execute("SELECT text, score FROM findings WHERE change_type = 'hardware_id'")} == triton
    db.close()


def test_cs2_and_dota_builds_stay_below_the_alert_threshold(tmp_path):
    """12/11/2026: 16 avvisi su 27 in un mese erano build nuove di CS2 e Dota 2: ora 45, sul sito ma senza notifica."""
    from hlxwatch.core.pipeline import Pipeline
    cfg, db = make(tmp_path)
    apps = {a["name"]: a for a in cfg.steam_watch_apps}
    assert apps["cs2"]["build_score"] == 45 and apps["dota2"]["build_score"] == 45
    assert "build_score" not in apps["alyx"]                                       # Alyx: avviso come prima
    for game, score in (("cs2", 55), ("dota2", 55), ("alyx", 55)):
        add(db, game, "depots/branches/public", "build", f"{game}: nuova build 1 sul branch 'public'", score,
            ["steam_build"], token=f"build:{game}", source="steam_pics")
    reevaluate(cfg, db)
    got = {r["game"]: r["score"] for r in db.conn.execute("SELECT game, score FROM findings WHERE change_type = 'build'")}
    assert got == {"cs2": 45, "dota2": 45, "alyx": 55}
    builds = [Finding(game=g, source="steam_pics", file_path="depots/branches/public", change_type="build", text="x",
                      score=s, keywords=["steam_build"]) for g, s in got.items()]
    for f in builds:
        f.is_new = True
    assert [f.game for f in Pipeline(cfg, db).select_alerts(builds)] == ["alyx"]
    db.close()


def test_frame_game_bindings_are_not_alerts(tmp_path):
    cfg, db = make(tmp_path)
    prof = "drivers/frame_controller/resources/input/frame_controller_profile.json"
    rules = watched_rules(cfg)
    assert not watched_line(rules, prof, '"app_key": "steam.app.650000",')
    assert not watched_line(rules, prof, '\t\t"binding_url" : "doomvfr_bindings_frame_controller.json"')
    assert watched_line(rules, prof, '"controller_type": "frame_controller",')
    add(db, "steamvr", prof, "added", '"app_key": "steam.app.650000"', 55, ["watched_file"])
    add(db, "steamvr", prof, "added", '"binding_url": "doomvfr_bindings_frame_controller.json"', 55, ["watched_file"])
    add(db, "steamvr", prof, "added", '"controller_type": "frame_controller",', 55, ["watched_file"])
    reevaluate(cfg, db)
    left = [r["text"] for r in db.conn.execute("SELECT text FROM findings WHERE game = 'steamvr'")]
    assert left == ['"controller_type": "frame_controller",']
    db.close()
