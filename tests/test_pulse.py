import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.pulse import PulseTracker
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database

SCHEMA = "DumpSource2/schemas/server/{}.h"


def make(tmp_path, repos=None):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    if repos:
        raw["github"]["repos"] = repos
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_check_added_scores_and_learning(tmp_path):
    cfg, db = make(tmp_path)
    t = PulseTracker(cfg, db)
    path = SCHEMA.format("CPulseCell_Outflow_PlayDynamicVCD")
    assert t.check_added("cs2", path) is None                     # prima dell'apprendimento: lo trova la scansione completa
    db.set_state("pulse:nodes", '["CPulseCell_PlaySequence"]')
    f = t.check_added("cs2", path)
    assert f is not None and f.change_type == "pulse" and f.score == 100 and f.token == "pulse:CPulseCell_Outflow_PlayDynamicVCD"
    assert t.check_added("dota2", path) is None                   # una volta sola, anche in un altro gioco
    plain = t.check_added("cs2", SCHEMA.format("CPulseCell_Timeline"))
    assert plain.score == 70 and plain.keywords == ["pulse_node"]
    hlx = t.check_added("cs2", SCHEMA.format("CPulseCell_HLX_Barnacle"))
    assert hlx.score >= 120 and "hlx" in hlx.keywords
    assert t.check_added("cs2", "DumpSource2/schemas/server/CBaseEntity.h") is None
    assert t.check_added("cs2", "game/core/x/CPulseCell_Foo.h") is None
    assert t.check_added("cs2", SCHEMA.format("CPulseCell_Timeline__CursorState_t")) is None     # tipo interno di un nodo
    db.close()


def test_commit_alert(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    db.set_state("pulse:nodes", '["CPulseCell_PlaySequence"]')
    src = GitHubTrackingSource(cfg, db)
    monkeypatch.setattr(src, "_fallback_diff", lambda *a, **k: [])          # niente download nei test
    repo_cfg = {"name": "cs2", "repo": "SteamTracking/GameTracking-CS2"}
    detail = {"sha": "a" * 40, "parents": [], "files": [
        {"filename": SCHEMA.format("CPulseCell_Outflow_PlayChoreoScene"), "status": "added"},
        {"filename": SCHEMA.format("CPulseCell_PlaySequence"), "status": "modified", "additions": 1}]}
    found = [f for f in src._analyze_commit(repo_cfg, detail, KeywordMatcher(cfg.keywords)) if f.change_type == "pulse"]
    assert [(f.text, f.score) for f in found] == [("CPulseCell_Outflow_PlayChoreoScene", 100)]
    db.close()


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")
def test_full_scan_learns_then_alerts(tmp_path):
    origin = tmp_path / "origin"
    (origin / "DumpSource2/schemas/server").mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    (origin / SCHEMA.format("CPulseCell_PlaySequence")).write_text("class CPulseCell_PlaySequence {}", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "uno")
    cfg, db = make(tmp_path, repos=[{"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "main",
                                     "clone_url": origin.as_uri()}])
    assert [f for f in FullScanSource(cfg, db).run(force=True) if f.change_type == "pulse"] == []
    assert db.get_state("pulse:nodes") == '["CPulseCell_PlaySequence"]'
    (origin / SCHEMA.format("CPulseCell_Outflow_ScriptedSequence")).write_text("class X {}", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "due")
    found = [f for f in FullScanSource(cfg, db).run(force=True) if f.change_type == "pulse"]
    assert [(f.text, f.score, f.source) for f in found] == [("CPulseCell_Outflow_ScriptedSequence", 100, "fullscan")]
    assert found[0].build.url.endswith("/blob/main/" + SCHEMA.format("CPulseCell_Outflow_ScriptedSequence"))
    assert [f for f in FullScanSource(cfg, db).run(force=True) if f.change_type == "pulse"] == []
    db.close()
