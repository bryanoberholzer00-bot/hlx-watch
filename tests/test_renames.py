import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.renames import find_renames
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database

VERIFIED = [   # le cinque rinomine verificate nei file di SteamTracking il 2026-10-29
    ('_builtins._voicegraph = "hlx.hrtf_basic"', '_builtins._voicegraph = "core.hrtf_basic"', "core"),
    ("scripts/physics_particles.vdata!hlx_physics_particle_spawner",
     "scripts/physics_particles.vdata!generic_physics_particle_spawner", "generic"),
    ("r_hlx_fsr3_min_reactiveness", "r_citadel_fsr3_min_reactiveness", "citadel"),
    ('type = "hlx_2d_basic"', 'type = "citadel_2d_basic"', "citadel"),
    ("hlx_group_soundevent_cluster_default_3d_test =", "group_soundevent_cluster_default_3d_test =", ""),
]


def test_verified_renames():
    for old, new, prefix in VERIFIED:
        assert find_renames([old], ["altro", new, "ancora"]) == [(old, new, prefix)]


def test_not_renames():
    assert find_renames(["hlx", "mod != hlx", "hlx_x"], ["mod != dota", "csgo", "foo_x"]) == []   # troppo corti
    assert find_renames(["r_hlx_fsr3_min_reactiveness"], ["r_fsr3_other_setting"]) == []
    assert find_renames(["r_hlx_fsr3_min_reactiveness"], ["r_hlx2_fsr3_min_reactiveness"]) == []   # resta HLX
    assert find_renames(["r_citadel_fsr3"], ["r_core_fsr3"]) == []                                   # niente HLX


def test_commit_rename_finding(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    src = GitHubTrackingSource(cfg, db)
    m = KeywordMatcher(cfg.keywords)
    path = "game/core/pak01_dir/soundevents/soundevents_diagnostics.vsndevts"
    lines = [("-", '\t\t_builtins._voicegraph = "hlx.hrtf_basic"'), ("+", '\t\t_builtins._voicegraph = "core.hrtf_basic"'),
             ("+", "\t\tvsnd_duration = 0.2")]
    out = [f for f in src._extra_findings("cs2", path, lines, m) if f.change_type == "renamed"]
    assert len(out) == 1
    f = out[0]
    assert f.text == '_builtins._voicegraph = "core.hrtf_basic"' and f.old_text == '_builtins._voicegraph = "hlx.hrtf_basic"'
    assert f.score == 100 and f.keywords == ["hlx_renamed"] and "'core'" in f.context
    assert src._extra_findings("cs2", "DumpSource2/.stringsignore", lines, m) == []     # filtro di SteamTracking
    from hlxwatch.notifiers.base import finding_line
    assert 'hlx.hrtf_basic" → _builtins._voicegraph = "core.hrtf_basic' in finding_line(f, "html").replace("&quot;", '"')
    cfg.raw["renames"] = {"enabled": False}
    assert [f for f in GitHubTrackingSource(cfg, db)._extra_findings("cs2", path, lines, m) if f.change_type == "renamed"] == []
    db.close()
