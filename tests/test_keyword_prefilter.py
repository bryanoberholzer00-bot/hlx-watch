"""Il filtro unico delle parole chiave accelera senza cambiare i risultati."""
import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT

LINES = ["hlx", "HLX_KILL_MANHACK", "hLX", "t(hlx", "Half-Life 3", "half_life_3", "Half-Life: Alyx", "hl3", "#hl3",
         "mod != hlx", "Thumper (HLX)", "left 4 dead 3", "l4d3_map", "content\\hlx\\particles", "niente da vedere",
         "x" * 60, "r_hlx_fsr3 1", "CHLXUserCmdPB", "hlvr_default_3d", ""]
PATHS = ["", "bin/win64/client_strings.txt", "game/core/ai_base.fgd", "resource/csgo_english.txt"]


def matcher(**extra):
    kw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))["keywords"]
    kw.update(extra)
    return KeywordMatcher(kw)


def result(m, text, path):
    return [(x.keyword.id, x.start, x.end) for x in m.find(text, path)]


def test_same_matches_with_and_without_prefilter():
    fast, slow = matcher(), matcher()
    assert fast.prefilter
    slow.prefilter = None
    for path in PATHS:
        for text in LINES:
            assert result(fast, text, path) == result(slow, text, path), (text, path)
    assert result(fast, "Half-Life 3", "")                      # il filtro non scarta le righe buone


def test_backreference_disables_prefilter():
    kw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))["keywords"]
    kw["items"] = kw["items"] + [{"id": "doppia", "pattern": r"(ab)\1", "weight": 50}]
    m = KeywordMatcher(kw)
    assert m.prefilter is None
    assert [x.keyword.id for x in m.find("xxababxx")] == ["doppia"]
