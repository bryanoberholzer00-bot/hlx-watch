import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT


def load():
    return yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))


def test_universe_terms_in_shared_files():
    m = KeywordMatcher(load()["keywords"])
    res = m.analyze_line("activities ACT_ANTLION_JUMP_START", file_path="DumpSource2/schemas/client/BaseActivity_t.h")
    assert res is not None and res.keywords == ["hl_universe"] and res.score == 45 and res.token == "ACT_ANTLION_JUMP_START"
    # un'entità FGD nuova supera la soglia di notifica grazie al bonus
    fgd = m.analyze_line('@NPCClass base(BaseNPC) = npc_antlion_guard : "Antlion Guard"', file_path="game/core/ai_basenpc.fgd")
    assert fgd is not None and fgd.score >= 50 and "fgd_entity" in fgd.bonuses
    assert m.analyze_line("combine_soldier", file_path="x.h") is not None
    assert m.analyze_line("CombineTextures", file_path="x.h") is None             # "combine" da solo no
    assert m.analyze_line("freeman", file_path="x.h") is None                     # solo il nome completo
    assert m.analyze_line("gordon freeman", file_path="x.h") is not None
    assert m.analyze_line("Xenon", file_path="x.h") is None


def test_universe_ignored_in_cosmetics_and_alyx():
    cfg = load()
    m = KeywordMatcher(cfg["keywords"])
    for path in ("game/csgo/pak01_dir/scripts/items/items_game.txt", "game/csgo/pak01_dir/resource/csgo_english.txt",
                 "game/dota/pak01_dir/resource/localization/items_english.txt", "game/csgo/readonly_tools_asset_info.txt",
                 "game/citadel/pak01_dir.txt", "manifests/manifest_381450.txt"):
        assert m.analyze_line('"Headcrab Sticker"', file_path=path) is None, path
    assert m.analyze_line('"Headcrab Sticker"', file_path="game/csgo/scripts/misc.txt") is not None
    alyx = next(r for r in cfg["github"]["repos"] if r["name"] == "alyx")
    assert "hl_universe" in alyx["ignore_keywords"]
    res = KeywordMatcher(cfg["keywords"], ignore_ids=alyx["ignore_keywords"]).analyze_line("npc_headcrab", file_path="a.fgd")
    assert res is None or "hl_universe" not in res.keywords          # resta al più la parola generica npc_
    for name in ("steamclient", "steamvr", "steamworks", "steamhelp"):
        repo = next(r for r in cfg["github"]["repos"] if r["name"] == name)
        assert "hl_universe" in repo["ignore_keywords"], name
