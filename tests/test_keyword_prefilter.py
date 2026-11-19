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


def test_level_transition_keyword():
    """18/11/2026: in Dota 2 i fori dei proiettili seguono il giocatore nel livello dopo (base.fgd)."""
    import yaml
    from hlxwatch.analyzers import KeywordMatcher
    from hlxwatch.config import PACKAGE_ROOT
    cfg = yaml.safe_load((PACKAGE_ROOT / "config.actions.yaml").read_text(encoding="utf-8"))
    m = KeywordMatcher(cfg["keywords"])
    line = ('\tmove_world_decals(boolean) : "Move World Decals With Player" : 0 : "When a player crosses into another '
            'level, the world decals inside this trigger (bullet holes etc.) move by the landmark offset "+')
    r = m.analyze_line(line, file_path="game/core/base.fgd")
    assert r is not None and "level_transition" in r.keywords and r.score >= 50
    r = m.analyze_line("@SolidClass base(Trigger) = trigger_changelevel :", file_path="game/core/base.fgd")
    assert r is not None and "level_transition" in r.keywords
    # nei dump di stringhe e nelle traduzioni no: il comando changelevel e "al livello successivo" degli eroi
    assert m.analyze_line("changelevel", file_path="game/csgo/bin/win64/server_strings.txt") is None
    r = m.analyze_line('"DOTA_Tooltip_x" "Upgrades to the next level"', file_path="game/dota/resource/localization/dota_english.txt")
    assert r is None or "level_transition" not in r.keywords
