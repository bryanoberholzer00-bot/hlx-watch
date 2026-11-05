"""Di cosa è fatto HLX: tracce per sistema e la sezione "HLX è un gioco VR?"."""
from hlxwatch import sistemi


def test_system_rules_on_real_traces():
    cases = {
        "mod != hlx || !m_bRefract": "particelle",
        "dev\source2\main\content\hlx\materials\particle\test\test_shlit_alpha.exr": "particelle",
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
