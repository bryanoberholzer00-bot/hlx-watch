from hlxwatch import fase


def test_classify_real_names():
    assert fase.classify("hlx.hrtf_basic") == "motore"
    assert fase.classify("// MPropertySuppressExpr = \"mod != hlx || !m_bRefract\"") == "motore"
    assert fase.classify("hlx_physics_particle_spawner") == "motore"
    assert fase.classify("\"- Thumper (HLX): causes antlions to run away briefly<br>\"") == "gioco"
    assert fase.classify("CHLXCoverPreviewHelper") == "gioco"
    assert fase.classify("item_hlvr_grenade_xen") == "gioco"
    assert fase.classify("cmarkupvolumetagged_navhlvr") == "gioco"
    assert fase.classify('"product"\t\t"HLX"') == "strumenti"
    assert fase.classify("hlvr/hlx") == "gioco"           # titolo dei tipi di gioco di Alyx e HLX in demoinfo2
    assert fase.classify("CHLXUserCmdPB") == "dubbio"


def test_same_piece_counts_once():
    assert fase.piece_key('// MPropertySuppressExpr = "mod != hlx"') == fase.piece_key("mod != hlx")
    assert fase.piece_key("GetAttributeTable_CHLXCoverPreviewHelper") == fase.piece_key("CHLXCoverPreviewHelper")
    assert fase.piece_key("CDummyHLXCover") == "hlxcover"


def test_summary_by_year_with_alyx():
    reg = {"traces": [
        {"label": "hlx.hrtf_basic", "first": "2021-03-04"},
        {"label": "mod != hlx", "first": "2023-03-07"},
        {"label": '// MPropertySuppressExpr = "mod != hlx"', "first": "2025-05-17"},
        {"label": '"product"\t\t"HLX"', "first": "2024-08-16"},
        {"label": '"component"\t\t"hlx"', "first": "2024-08-16"},
        {"label": "GetAttributeTable_CHLXCoverPreviewHelper", "first": "2026-09-23"},
        {"label": "CHLXCoverPreviewHelper", "first": "2026-09-23"},
    ]}
    alyx_seed = {"announced": "2019-11-21", "names": [{"name": "hlvr_weapon_shotgun", "day": "2017-10-25"},
                                                      {"name": "hlvr_default_3d", "day": "2016-12-11"},
                                                      {"name": "hlalyxpromotitle", "day": "2020-03-12"}]}
    s = fase.summary(reg, alyx_seed)
    assert s["n"] == 4 and s["total"] == {"gioco": 1, "motore": 2, "strumenti": 1, "dubbio": 0}
    assert [r["year"] for r in s["rows"]] == ["2021", "2023", "2024", "2026"]
    assert s["items"][-1]["label"] == "CHLXCoverPreviewHelper"           # il nome più corto dello stesso pezzo
    assert s["alyx"] == {"gioco": 1, "motore": 1, "strumenti": 0, "dubbio": 0, "total": 2}
    assert fase.summary({"traces": []}, None) is None
