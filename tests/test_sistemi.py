"""Di cosa è fatto HLX: tracce per sistema e la sezione "HLX è un gioco VR?"."""
from hlxwatch import sistemi


def test_system_rules_on_real_traces():
    cases = {
        "mod != hlx || !m_bRefract": "particelle",
        r"dev\source2\main\content\hlx\materials\particle\test\test_shlit_alpha.exr": "particelle",
        '"- Thumper (HLX): causes antlions to run away briefly<br>"': "nemici",
        "CHLXCoverPreviewHelper": "nemici",
        "hlx_physics_particle_spawner": "fisica",
        "hlx_configurable_lightweight_body_entity": "fisica",
        "hlx.hrtf_basic": "suono",
        "hlx_2d_basic": "suono",
        "CHLXBaseProjectileEmitterHelper": "armi",
        "hlx_combine_barrier_cell_spacing": "mondo",
        "r_hlx_fsr3_min_reactiveness": "grafica",
        "CHLXUserCmdPB": "rete",
        '"product"\t\t"HLX"': "strumenti",
        "hlvr/hlx": "strumenti",
        "hlx_qualcosa_di_strano": "altro",
    }
    for label, want in cases.items():
        assert sistemi.system_of(label) == want, label


def reg(*items):
    return {"traces": [{"label": label, "first": first, "games": games, "status_label": "presente oggi",
                        "slug": label.lower()} for label, first, games in items]}


def test_summary_counts_pieces_once_and_compares_with_alyx():
    r = reg(("mod != hlx", "2023-03-07", ["cs2"]), ('// MPropertySuppressExpr = "mod != hlx"', "2025-05-17", ["dota2"]),
            ("CHLXCoverPreviewHelper", "2026-09-23", ["cs2"]), ("hlx_combine_barrier_cell_spacing", "2021-09-16", ["dota2"]))
    alyx = {"announced": "2019-11-21", "names": [
        {"name": "hlvr_weapon_shotgun", "day": "2017-10-25", "repo": "steamvr"},
        {"name": "hlvr_combine_barrier_cell_spacing", "day": "2019-06-13", "repo": "underlords"},
        {"name": "hlvr_hand_pose", "day": "2020-01-01", "repo": "dota2"}]}      # dopo l'annuncio: non conta
    s = sistemi.summary(r, alyx)
    assert s["n"] == 3                                          # mod != hlx e la versione commentata: un pezzo
    part = next(x for x in s["systems"] if x["key"] == "particelle")
    assert part["n"] == 1 and sorted(part["items"][0]["games"]) == ["cs2", "dota2"]
    assert s["years"] == ["2021", "2023", "2026"] and s["new_last"] == ["Nemici e intelligenza artificiale"]
    v = s["vr"]
    assert v["hlx_vr"] == [] and v["alyx"]["n"] == 2 and v["alyx"]["vr"] == 0 and v["alyx"]["steamvr"] == 1
    assert [p["label"] for p in v["alyx"]["renamed"]] == ["hlx_combine_barrier_cell_spacing"]
    assert v["counts"]["schermo"] + v["counts"]["vr"] + v["counts"]["neutro"] == len(sistemi.CLUES)


def test_vr_words():
    for word in ("hlx_vr_hand", "teleport_dest", "item_hlx_gravity_glove", "SteamVR", "snap_turn"):
        assert sistemi.VR_WORDS.search(word), word
    for word in ("hlvr_weapon_shotgun".replace("hlvr", ""), "cover", "driver", "server", "everything"):
        assert not sistemi.VR_WORDS.search(word), word


def test_empty_register():
    assert sistemi.summary({"traces": []}, None) is None


def test_engine_lists_counted_in_full_scan_and_merged(tmp_path):
    from hlxwatch.storage import Database
    db = Database(tmp_path / "x.sqlite")
    a = sistemi.EngineLists()
    for text in ("mod != hlx", '// MPropertySuppressExpr = "mod != hlx"', "mod != hlx || !m_bRefract",
                 "mod == csgo", "mod != dota && mod != hlx"):
        a.add("game/core/tools/x.txt", text)
    a.add("tools/bugreporter/config.txt", '"product"\t\t"HLX"')
    a.add("tools/bugreporter/config.txt", '"component"\t\t"hlx"')
    a.add("other.txt", '"product" "Altro"')                         # solo nel file del bug reporter
    a.save(db, "cs2")
    b = sistemi.EngineLists()
    b.add("x.txt", "mod == csgo")
    b.add("x.txt", "mod != csgo || x")
    b.save(db, "deadlock")
    L = sistemi.engine_lists(db)
    assert {r["mod"]: r["n"] for r in L["mods"]} == {"hlx": 3, "csgo": 2, "dota": 1}   # la versione commentata conta 1
    assert L["products"] == ["HLX"] and L["components"] == ["hlx"]
    assert not L["deadlock_in_mods"] and not L["deadlock_in_bug"]
    assert sistemi.engine_lists(Database(tmp_path / "vuoto.sqlite")) is None
    db.close()


def test_twins_days_and_cards():
    rows = sistemi.twins({"traces": [{"label": "CHLXUserCmdPB", "slug": "chlxusercmdpb"}]})
    by = {t["hlx"]: t for t in rows}
    assert by["CHLXUserCmdPB"]["days"] == 0 and by["CHLXUserCmdPB"]["slug"] == "chlxusercmdpb"
    assert by["hlx_2d_basic"]["days"] > 900                     # Deadlock lo ha avuto due anni e mezzo dopo
    assert by["r_hlx_fsr3_min_reactiveness"]["rel"] == "passato da HLX a Deadlock"


def test_path_systems_come_first():
    assert sistemi.system_of("HLX_Weapon_Shotgun", "game/hlx/resource/localization/hlx_english.txt") == "testi"
    assert sistemi.system_of("hlx_menu", "game/hlx/panorama/layout/mainmenu.vxml") == "interfaccia"
    assert sistemi.system_of("HLX_Weapon_Shotgun", "game/bin/win64/client_strings.txt") == "armi"


def test_new_system_note_in_alerts(tmp_path):
    import yaml
    from hlxwatch.config import PACKAGE_ROOT, Config
    from hlxwatch.models import BuildInfo, Finding
    from hlxwatch.storage import Database
    from datetime import datetime, timezone
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    bid = db.upsert_build(BuildInfo(game="cs2", source="github", ref="a", date=datetime.now(timezone.utc)))

    def hlx(token, path):
        f = Finding(game="cs2", source="github", file_path=path, change_type="added", text=token, score=120,
                    keywords=["hlx_prefix"], token=token)
        db.add_finding(f, bid)
        return f
    first = [hlx("hlx_physics_particle_spawner", "game/bin/win64/assetpreview_strings.txt")]
    assert sistemi.annotate_new_systems(cfg, db, first) == []          # primo giro: impara, senza note
    assert "fisica" in sistemi.seen_systems(cfg, db)
    again = hlx("hlx_physics_rope", "game/bin/win64/x_strings.txt")
    menu = hlx("hlx_mainmenu_title", "game/hlx/panorama/layout/mainmenu.vxml")
    menu2 = hlx("hlx_pausemenu_title", "game/hlx/panorama/layout/pause.vxml")
    assert sistemi.annotate_new_systems(cfg, db, [again, menu, menu2]) == ["interfaccia"]
    assert not getattr(again, "note", None) and not getattr(menu2, "note", None)   # una nota sola per sistema
    assert "Interfaccia" in menu.note and "segnale forte" in menu.note
    assert sistemi.annotate_new_systems(cfg, db, [hlx("hlx_options_title", "a/panorama/b.vxml")]) == []
    db.close()
