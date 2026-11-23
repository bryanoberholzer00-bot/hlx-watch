"""Sistemi da gioco per giocatore singolo nei giochi multigiocatore (18/11/2026: streaming dei livelli in Dota 2)."""
from pathlib import Path

import pytest
import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT

CONVARS = "DumpSource2/convars.txt"


@pytest.fixture(scope="module", params=["config.actions.yaml", "config.example.yaml"])
def matcher(request):
    raw = yaml.safe_load((PACKAGE_ROOT / request.param).read_text(encoding="utf-8"))
    return KeywordMatcher(raw["keywords"])


@pytest.mark.parametrize("line,path", [
    ("A level unloading mid-game saves its entities to save/<level>.hl1/.hl2, which point_level_stream restores", CONVARS),
    ("hud_showstreaming 0 (developmentonly gamedll defensive)", CONVARS),
    ("skill 1 (min: 0, max: 3, gamedll clientdll archive replicated per_user)", CONVARS),
    ("Add a timestamp to the autosave or quicksave filename.", CONVARS),
    ("@PointClass base(Targetname) = trigger_autosave :", "game/core/base.fgd"),
    ('64 : "Enable motion on Physcannon grab" : 0', "game/core/base.fgd"),
])
def test_single_player_systems_alert(matcher, line, path):
    r = matcher.analyze_line(line, file_path=path)
    assert r is not None and "single_player" in r.keywords and r.score >= 50


@pytest.mark.parametrize("line,path", [
    ("bot_difficulty 1 (gamedll release commandline_enforced)", CONVARS),          # bot di CS2
    ("dota_survivors_difficulty_setting 0 (clientdll archive per_user)", CONVARS),  # modalità di Dota
    ("hammer_force_autosave (developmentonly defensive)", "DumpSource2/commands.txt"),
    ("skill_points 3", CONVARS),
    ("autosave", "game/bin/win64/engine2_strings.txt"),                             # dump dei binari
    ("Chapter 2", "game/dota/resource/localization/dota_english.txt"),             # traduzioni
    ("level stream", ""),                                                           # file sconosciuto: niente
])
def test_single_player_noise(matcher, line, path):
    r = matcher.analyze_line(line, file_path=path)
    assert r is None or "single_player" not in r.keywords


def test_scenes_on_site_without_alert(matcher):
    r = matcher.analyze_line("debug_ai_choreo_timing false (gamedll cheat)", file_path=CONVARS)
    assert r is not None and "single_player_scene" in r.keywords and r.score < 50


def test_path_includes_only_limits_that_keyword():
    m = KeywordMatcher({"items": [{"id": "a", "pattern": "foo", "weight": 60, "path_includes": [r"\.fgd$"]},
                                  {"id": "b", "pattern": "foo", "weight": 20}]})
    assert set(m.analyze_line("foo", file_path="x/base.fgd").keywords) == {"a", "b"}
    assert m.analyze_line("foo", file_path="x/y.txt").keywords == ["b"]


def test_config_yaml_local_matches_example():
    local = PACKAGE_ROOT / "config.yaml"
    if not local.exists():
        pytest.skip("config.yaml locale assente")
    items = {k["id"]: k for k in yaml.safe_load(local.read_text(encoding="utf-8"))["keywords"]["items"]}
    example = {k["id"]: k for k in yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))["keywords"]["items"]}
    assert items["single_player"]["pattern"] == example["single_player"]["pattern"]


@pytest.mark.parametrize("line,path,kw", [
    ("save_timestamp_autosaves false (developmentonly gamedll)", CONVARS, "single_player"),
    ("spawngroup_save_on_unload false (developmentonly gamedll clientdll replicated defensive)", CONVARS, "single_player"),
    ("Entities that leave their level (teleported or carried into another one) change to belonging to the level they're in",
     CONVARS, "single_player"),
    ("backtrack(boolean) // FIELD_BOOLEAN", "DumpSource2/entities/server.fgd", "single_player"),
    ("%s:  SaveStreamedOutLevel:  saved %s to %s [%d entities] in %.2f msec\n",
     "game/dota/bin/win64/server_strings.txt", "single_player_code"),
    ("CSpawnGroupMgrGameSystem::IsGameReadyToSave:  holding the save, %s\n",
     "game/dota/bin/win64/client_strings.txt", "single_player_code"),
    ("hud_showstreaming", "game/dota/bin/win64/server_strings.txt", "single_player_code"),
])
def test_single_player_wider(matcher, line, path, kw):
    r = matcher.analyze_line(line, file_path=path)
    assert r is not None and kw in r.keywords and r.score >= 50


@pytest.mark.parametrize("line,path", [
    ("Streamed Out: %0.2f MB:", "game/bin/win64/scenesystem_strings.txt"),                 # texture
    ("AudioProcessing::ProcessStream_AudioFrame", "game/bin/win64/engine2_strings.txt"),    # audio
    ("%s:  CNetworkClientSpawnGroup_LoadSaveGamePrerequisiteBase async started [%s]...\n",
     "game/csgo/bin/win64/client_strings.txt"),                                            # c'è da anni
    ("StreamEntitiesToFile", "game/csgo/bin/win64/server_strings.txt"),                     # salvataggi di sempre
    ("assetsystem_save_info_idle_seconds 3 (developmentonly)", CONVARS),                     # strumenti
])
def test_single_player_code_noise(matcher, line, path):
    r = matcher.analyze_line(line, file_path=path)
    assert r is None or not {"single_player", "single_player_code"} & set(r.keywords)
