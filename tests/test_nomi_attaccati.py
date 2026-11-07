"""hl_nomi_attaccati: i nomi di Half-Life scritti attaccati degli aggiornamenti di fine ottobre 2026, senza i falsi."""
import pytest

from hlxwatch.analyzers.keywords import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT, load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config(PACKAGE_ROOT / "config.actions.yaml")


@pytest.mark.parametrize("text, path", [
    ("GetAttributeTable_CRangerTrapCableHelper", "game/bin/win64/tools/hammer_strings.txt"),
    ("ranger_trap_cables", "game/bin/win64/tools/hammer_strings.txt"),
    ("g_vXenAnimation", "game/bin/win64/materialsystem2_strings.txt"),
    ("AVCQToolPaintingUI_XenAnimation", "game/bin/win64/tools/hammer_strings.txt"),
    ("ai_flyer_flanking_route_hint", "DumpSource2/entities/server.fgd"),
    ("info_combine_gunship_bomb_target(point)", "DumpSource2/entities/server.fgd"),
])
def test_real_strings_are_seen(cfg, text, path):
    res = KeywordMatcher(cfg.keywords).analyze_line(text, file_path=path)
    assert res and "hl_nomi_attaccati" in res.keywords and res.score >= 80


@pytest.mark.parametrize("text, path", [
    ("\tboxEntity01(string) // FIELD_STRING", "DumpSource2/entities/server.fgd"),   # "xEn" minuscolo
    ("Xenon lamp", "game/x.txt"),
    ("drow_ranger", "scripts/npc/heroes.txt"),
    ("chopper/gunship", "game/core/pak01_dir.txt"),              # suoni di Half-Life 2 nel motore
    ("Bullets.GunshipNearmiss", "game/core/soundevents.vsndevts"),
    ("._3XenXoJ5CQnSgv-rw02USz{color:red}", "www.dota2.com/public/css/dota_react/main.css"),
])
def test_noise_is_not(cfg, text, path):
    res = KeywordMatcher(cfg.keywords).analyze_line(text, file_path=path)
    assert res is None or "hl_nomi_attaccati" not in res.keywords


def test_ignored_where_half_life_is_normal(cfg):
    alyx = next(r for r in cfg.get("github.repos") if r["name"] == "alyx")
    assert "hl_nomi_attaccati" in alyx["ignore_keywords"]
    m = KeywordMatcher(cfg.keywords, ignore_ids=alyx["ignore_keywords"])
    assert m.analyze_line("HLVR_CM_XenFoliageEmitSound", file_path="game/hlvr/x.txt") is None


def test_fast_filter_still_on(cfg):
    assert KeywordMatcher(cfg.keywords).prefilter is not None      # niente flag interni nelle regole
