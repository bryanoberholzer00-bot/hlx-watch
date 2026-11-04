import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.codenames import CodenameTracker, detect
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_detect_places():
    assert detect('// MPropertySuppressExpr = "mod != csgo && mod != hlx"') == [
        ("mod", "csgo", "espressione mod != / == degli strumenti"), ("mod", "hlx", "espressione mod != / == degli strumenti")]
    assert [n for _, n, _ in detect(r"G:\dev\source2\main\content\hlx\materials\x.exr")] == ["hlx"]
    br = "game/core/tools/bugreporter/config.txt"
    assert [n for _, n, _ in detect('\t\t"product"\t\t"Half-Life VR"', br)] == ["half-life vr"]
    assert [n for _, n, _ in detect('\t"RPG"', br)] == ["rpg"]
    assert detect('\t"RPG"', "game/other.txt") == []                 # le sezioni contano solo nel bug reporter
    assert detect('\t\t"component"\t\t"other"', br) == []           # parola generica
    assert [n for _, n, _ in detect('\t\t"mod"\t"hl3"', "game/core/pak01_dir/source1import_txtmap.txt")] == ["hl3"]
    assert [n for _, n, _ in detect('"mod"\t"left4dead2_source2"')] == ["left4dead2_source2"]
    # messaggio di rete dei comandi del giocatore: nei .proto e nei dump dei binari
    assert [n for p, n, _ in detect("message CHLXUserCmdPB {") if p == "usercmd"] == ["hlx"]
    assert [n for p, n, _ in detect("message CCitadelUserCmdPB {") if p == "usercmd"] == ["citadel"]
    assert [n for p, n, _ in detect("message CDota2UserCmdPB {") if p == "usercmd"] == ["dota2"]
    assert [n for p, n, _ in detect("message CSGOUserCmdPB {") if p == "usercmd"] == ["csgo"]
    assert [n for p, n, _ in detect(".?AVCCitadelUserCmdPB@@") if p == "usercmd"] == ["citadel"]
    assert [n for p, n, _ in detect("CZetaUserCmdPB::ByteSizeLong") if p == "usercmd"] == ["zeta"]
    assert [p for p, n, _ in detect("message CBaseUserCmdPB {")] == []          # quello comune a tutti i giochi
    assert [p for p, n, _ in detect("message CUserCmdBasePB { message CDemoUserCmd")] == []
    # rami di compilazione nei dump delle DLL: il nome dopo buildworker\ (anche con / e maiuscole)
    bw = detect(r"C:\buildworker\csgo_rel_win64\build\src\engine2\x.cpp", "game/bin/win64/engine2_strings.txt")
    assert bw == [("buildworker", "csgo_rel_win64", "ramo di compilazione nei percorsi della macchina di build (buildworker)")]
    assert [n for _, n, _ in detect("/home/BuildWorker/dota_rel_linux64/build/src")] == ["dota_rel_linux64"]
    assert detect(r"C:\buildworker\build\src") == []                     # 'build' è una parola generica


def test_learning_then_alert(tmp_path):
    cfg, db = make(tmp_path)
    t = CodenameTracker(cfg, db)
    assert t.check("mod != hlx", "a.h", game="cs2") is None            # noto dal config
    assert t.check("mod != zeta", "a.h", game="cs2", change_type="present") is None   # apprendimento: imparato in silenzio
    t.finish_learning()
    assert CodenameTracker(cfg, db).check("mod != zeta", "a.h", game="cs2") is None    # ricordato tra un run e l'altro
    f = CodenameTracker(cfg, db).check("mod != omega && mod != hlx", "a.h", game="dota2")
    assert f is not None and f.change_type == "codename" and f.token == "codename:omega" and f.score == 120
    assert CodenameTracker(cfg, db).check("mod != omega", "b.h", game="cs2") is None   # una volta sola
    db.close()


def test_watched_file_lines(tmp_path):
    cfg, db = make(tmp_path)
    src = GitHubTrackingSource(cfg, db)
    matcher = KeywordMatcher(cfg.keywords)
    lines = [("+", '\t"Zeta"'), ("+", "\t{"), ("+", '\t\t"product"\t\t"zeta"'), ("+", '\t\t"component"\t\t"hlx"'),
             ("-", '\t\t"version"\t\t"4"'), ("+", "\t}")]
    db.set_state("codenames:learned", "1")
    src.codenames = type(src.codenames)(cfg, db)
    out = src._extra_findings("cs2", "game/core/tools/bugreporter/config.txt", lines, matcher)
    kinds = [(f.change_type, f.keywords[0]) for f in out]
    assert ("codename", "codename_new") in kinds                      # "Zeta" mai visto
    watched = [f for f in out if f.keywords == ["watched_file"]]
    assert [f.text for f in watched] == ['"Zeta"', '"product"\t\t"zeta"', '"version"\t\t"4"']    # niente parentesi né righe con hlx
    assert all(f.score == 60 for f in watched) and watched[2].change_type == "removed"
    assert src._extra_findings("cs2", "game/csgo/x.txt", [("+", "qualsiasi riga")], matcher) == []
    db.close()


def test_hardware_places():
    lighthouse = "drivers/lighthouse/bin/win32/driver_lighthouse_strings.txt"
    assert detect("Valve Deckard", lighthouse)[0][:2] == ("valve_device", "deckard")
    assert detect("Valve Index HMD", lighthouse)[0][:2] == ("valve_device", "index")
    assert detect("Valve Corporation", lighthouse) == []                 # parola generica
    assert detect("Valve Deckard", "bin/win64/vrclient_strings.txt") == []   # solo nei driver
    assert detect("  the Valve Deckard", lighthouse) == []               # solo a inizio riga
    neptune = detect("(t[(t.k_eControllerType_SteamControllerNeptune = 4)] =", "Scripts/WebUI/steammobile_android.js")
    assert [x[:2] for x in neptune] == [("controller_type_valve", "steamcontrollerneptune")]
    assert detect("k_ESteamInputType_SteamDeckController")[0][:2] == ("controller_type_valve", "steamdeckcontroller")
    assert detect("k_EControllerType_ZotacZone")[0][:2] == ("controller_type", "zotaczone")
    assert detect("k_ESteamInputType_Count") == []


def test_hardware_place_scores(tmp_path):
    cfg, db = make(tmp_path)
    cfg.raw.setdefault("codenames", {})["place_scores"] = {"valve_device": 80, "controller_type": 60,
                                                          "controller_type_valve": 80}
    t = CodenameTracker(cfg, db)
    t.check("k_EControllerType_PS4Controller", "a.js", game="steamclient", change_type="present")
    t.finish_learning()
    t = CodenameTracker(cfg, db)
    assert t.check("k_EControllerType_PS4Controller", "a.js", game="steamclient") is None      # imparato
    assert t.check("k_EControllerType_SteamControllerNeptune", "a.js", game="steamclient").score == 80
    assert t.check("k_EControllerType_ZotacZone", "a.js", game="steamclient").score == 60
    db.close()


def test_many_new_names_in_one_file_become_one_alert():
    from hlxwatch.analyzers.codenames import group_new_names
    from hlxwatch.models import Finding

    def name(n, score):
        return Finding(game="steamclient", source="github", file_path="a.js", change_type="codename", text=f"x {n}",
                       score=score, keywords=["codename_new"], token=f"codename:{n}")
    many = [name("xbox360controller", 60), name("steamcontrollerneptune", 80), name("ps4controller", 60),
            name("switchprocontroller", 60)]
    other = Finding(game="steamclient", source="github", file_path="a.js", change_type="added", text="hlx", score=100)
    out = group_new_names([*many, other], 4)
    assert len(out) == 2 and out[1] is other
    g = out[0]
    assert g.score == 80 and "codename_group" in g.keywords and "steamcontrollerneptune" in g.text
    assert "4 nomi mai visti" in g.text
    assert group_new_names(many[:3], 4) == many[:3]            # tre nomi: restano separati
