import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT, Config


def load():
    return yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))


def test_class_prefix_rule():
    m = KeywordMatcher(load()["keywords"])
    for line, token in (("class CHLXPlayerPawn : public CBasePlayerPawn", "CHLXPlayerPawn"), ("CHLX_Thumper", "CHLX_Thumper"),
                        ("struct CHLXInputState", "CHLXInputState"), ("C_HLX_Weapon", "C_HLX_Weapon")):
        res = m.analyze_line(line, file_path="DumpSource2/schemas/client/x.h")
        assert res is not None and "hlx_class" in res.keywords and res.token == token and res.score >= 115, line
    # un file nuovo con una classe HLX nel nome: regola forte, quindi i bonus valgono
    res = m.analyze_line("DumpSource2/schemas/client/CHLXPlayer.h", file_path="DumpSource2/schemas/client/CHLXPlayer.h",
                         change_type="file_added")
    assert res is not None and "new_file" in res.bonuses
    for line in ("SCHLX", "chlx_lower", "CHLX", "MCHLXX", "CCitadelPlayerPawn", "CHLXx"):
        res = m.analyze_line(line, file_path="x.h")
        assert res is None or "hlx_class" not in res.keywords, line


def test_extra_repo_titles(tmp_path):
    raw = load()
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    assert cfg.game_title("underlords") == "Dota Underlords"
    assert cfg.game_title("dota2test") == "Dota 2 Test"
    assert {r["name"] for r in cfg.get("archaeology.extra_repos")} == {"underlords", "artifact", "artifact_beta", "dota2test"}
