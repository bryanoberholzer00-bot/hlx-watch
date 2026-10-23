import yaml
import pytest

from hlxwatch.analyzers import KeywordMatcher, analyze_patch, diff_by_sets, parse_patch
from hlxwatch.config import PACKAGE_ROOT


@pytest.fixture(scope="module")
def matcher() -> KeywordMatcher:
    cfg = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    return KeywordMatcher(cfg["keywords"])


def test_hlx_convar_scores_high(matcher):
    res = matcher.analyze_line('    "hlx_fsr3_min_reactiveness" "0.5"', file_path="game/csgo/cfg/convars.txt")
    assert res is not None
    assert res.score >= 100
    assert res.token == "hlx_fsr3_min_reactiveness"
    assert "hlx_prefix" in res.keywords
    assert "convar" in res.bonuses


def test_path_match_and_bonus(matcher):
    res = matcher.analyze_line("maps/hlx_apartment_01.vpk", file_path="game/csgo/pak01_dir.txt")
    assert res is not None and res.score >= 130
    assert res.token == "maps/hlx_apartment_01.vpk"
    assert "path" in res.bonuses


def test_citadel_is_not_hlx(matcher):
    assert matcher.analyze_line("citadel_npc_trooper_health 100", file_path="x.txt") is None


def test_citadel_with_hlx_still_matches(matcher):
    res = matcher.analyze_line("citadel/scripts/hlx_bridge.vdata", file_path="x.txt")
    assert res is not None and res.score >= 100


def test_half_life_alyx_excluded(matcher):
    assert matcher.analyze_line("Half-Life: Alyx - Final Hours", file_path="x.txt") is None
    assert matcher.analyze_line("half_life_next_project", file_path="x.txt") is not None


def test_generic_keyword_is_low(matcher):
    res = matcher.analyze_line("npc_combine_soldier", file_path="x.txt")
    assert res is not None and res.score < 50


def test_fgd_entity_bonus(matcher):
    res = matcher.analyze_line('@NPCClass base(BaseNPC) = npc_hlx_scanner : "Scanner"', file_path="hlvr.fgd")
    assert res is not None
    assert res.token == "npc_hlx_scanner"
    assert "fgd_entity" in res.bonuses


def test_codename(matcher):
    res = matcher.analyze_line('"project" "TF"', file_path="x.txt")
    assert res is not None and "codename:tf" in res.keywords
    assert matcher.analyze_line("tfoo", file_path="x.txt") is None


def test_mixed_case_is_ignored(matcher):
    for line in ("=hLX", "L=HLx", "xx HlX yy"):
        assert matcher.analyze_line(line, file_path="game/bin/win64/phonon_strings.txt") is None, line
    for line in ("mod != hlx", "HLX_ENABLED", "Hlx", "hlx_FooBar_Thing"):
        assert matcher.analyze_line(line, file_path="x.txt") is not None, line


def test_binary_garbage_is_ignored(matcher):
    for line in ("tF\\tF", "4\\tF", "tf\\ff\\ff\\ff", "f\\tf\\ti\\tf\\ti", "tA\\tB\\tC\\tD\\tE\\tF\\tG"):
        assert matcher.analyze_line(line, file_path="game/bin/win64/v8_strings.txt") is None, line
    # un match alto passa comunque, anche in una riga brutta
    res = matcher.analyze_line("\\x00\\x00 hlx_core \\x00\\x00", file_path="engine2.dll (strings)")
    assert res is not None and res.token == "hlx_core"


def test_ignore_ids():
    cfg = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    m = KeywordMatcher(cfg["keywords"], ignore_ids=["half_life"])
    assert m.analyze_line("half_life_thing", file_path="x.txt") is None


PATCH = """@@ -10,4 +10,5 @@ context
 unchanged line
-old_value "1"
+old_value "2"
+hlx_new_convar "1"
-npc_removed_thing
 another
"""


def test_parse_and_analyze_patch(matcher):
    hunks = parse_patch(PATCH)
    assert len(hunks) == 1 and len(hunks[0]) == 6
    findings = analyze_patch(PATCH, game="cs2", source="github", file_path="game/convars.txt", matcher=matcher)
    types = {(f.change_type, f.token) for f in findings}
    assert ("added", "hlx_new_convar") in types
    assert ("removed", "npc_removed_thing") in types
    added = next(f for f in findings if f.token == "hlx_new_convar")
    assert added.line_no == 12
    assert "+hlx_new_convar" in added.context


def test_changed_pairing(matcher):
    patch = "@@ -1,2 +1,2 @@\n-hlx_speed 10\n+hlx_speed 20\n unchanged\n"
    findings = analyze_patch(patch, game="cs2", source="github", file_path="x.txt", matcher=matcher)
    assert len(findings) == 1
    assert findings[0].change_type == "changed"
    assert findings[0].old_text == "hlx_speed 10"


def test_diff_by_sets():
    changes = diff_by_sets(["a", "b", "c"], ["a", "c", "d", "d"])
    kinds = sorted((c.change_type, c.text) for c in changes)
    assert kinds == [("added", "d"), ("added", "d"), ("removed", "b")]
