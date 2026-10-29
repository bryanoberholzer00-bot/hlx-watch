import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.orphans import parse_classes
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")

CORE = """@BaseClass = Targetname
[
]
@PointClass base(Targetname) = info_target : "A target"
[
]
@NPCClass base(BaseNPC) editormodel("x.vmdl")
\tmetadata
\t{
\t}
= npc_vehicledriver : "NPC used to drive a target vehicle."
[
]
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "x")


def make_origin(tmp_path: Path, name: str, files: dict) -> Path:
    origin = tmp_path / name
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    write(origin, files)
    return origin


def make(tmp_path, repos):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["github"]["repos"] = [{"name": n, "repo": f"SteamTracking/GameTracking-{n}", "branch": "main",
                               "clone_url": o.as_uri(), "appid": 1} for n, o in repos.items()]
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_parse_classes_multiline():
    c = parse_classes(CORE)
    assert c["info_target"] == ("Point", "A target") and c["targetname"][0] == "Base"
    assert c["npc_vehicledriver"] == ("NPC", "NPC used to drive a target vehicle.")


def test_orphans_learn_then_alert(tmp_path):
    hammer = "x\n.?AUCAttr_m_b@?1??GetAttributeTable_CMoverHelper@CMoverHelper@@\n"
    cs2 = make_origin(tmp_path, "cs2", {"game/core/base.fgd": CORE,
                                        "DumpSource2/entities/server.fgd": "@PointClass = info_target\n[\n]\n",
                                        "game/bin/win64/tools/hammer_strings.txt": hammer})
    dota = make_origin(tmp_path, "dota2", {"game/core/base.fgd": CORE,
                                           "DumpSource2/entities/server.fgd": "@PointClass = info_target\n[\n]\n"})
    cfg, db = make(tmp_path, {"cs2": cs2, "dota2": dota})
    src = FullScanSource(cfg, db)
    assert [f for f in src.run(force=True) if f.change_type == "orphan"] == []     # primo giro: impara
    assert "npc_vehicledriver" in db.get_state("orphans:entities")
    assert "info_target" not in db.get_state("orphans:entities")                   # implementata in C++

    write(cs2, {"game/core/ai_basenpc.fgd": '@PointClass base(Targetname) = ai_goal_cover : "Cover goal for hlx soldiers"\n[\n]\n'
                                           '@PointClass base(Targetname) = info_used : "used"\n[\n]\n',
                "game/bin/win64/tools/hammer_strings.txt": hammer + "GetAttributeTable_CHLXCoverPreviewHelper@x\n"})
    write(dota, {"DumpSource2/entities/server.fgd": "@PointClass = info_target\n[\n]\n@PointClass = info_used\n[\n]\n"})
    found = {f.token: f for f in src.run(force=True) if f.change_type == "orphan"}
    assert set(found) == {"orphan:ai_goal_cover", "hammerhelper:CHLXCoverPreviewHelper"}   # info_used ha la sua classe
    cover = found["orphan:ai_goal_cover"]
    assert cover.score >= 120 and "orphan_entity" in cover.keywords and "hlx" in cover.keywords   # descrizione con hlx
    assert cover.file_path == "game/core/ai_basenpc.fgd" and cover.build.url.endswith("/blob/main/game/core/ai_basenpc.fgd")
    assert found["hammerhelper:CHLXCoverPreviewHelper"].score >= 115
    assert [f for f in src.run(force=True) if f.change_type == "orphan"] == []     # una volta sola
    db.close()


def test_orphans_skipped_when_a_repo_fails(tmp_path):
    cs2 = make_origin(tmp_path, "cs2", {"game/core/base.fgd": CORE, "DumpSource2/entities/server.fgd": "@PointClass = info_target\n"})
    cfg, db = make(tmp_path, {"cs2": cs2, "dota2": tmp_path / "manca"})
    FullScanSource(cfg, db).run(force=True)
    assert db.get_state("orphans:entities") is None                               # un repo fallito: niente confronto
    db.close()


def test_watched_fgd_ignores_reformatting(tmp_path):
    cfg, db = make(tmp_path, {})
    src = GitHubTrackingSource(cfg, db)
    matcher = KeywordMatcher(cfg.keywords)
    lines = [("-", '\thinttype(choices) : "Hint" : 0 = '), ("+", '\thinttype(intchoices) : "Hint" : 0 = '),
             ("-", "\tinput Foo(void) : \"x\""), ("+", "\t input  Foo(void) : \"x\""),
             ("+", '\twait_until_seen_by_player(boolean) : "Wait until seen by player" : 0'),
             ("-", '\tinput BecomeRagdoll(void) : "go limp"'), ("-", "\tmetadata"), ("-", '\t\tapi_classes = [ "base_npc" ]')]
    out = src._extra_findings("cs2", "game/core/ai_defaultnpc.fgd", lines, matcher)
    watched = [(f.change_type, f.text) for f in out if f.keywords == ["watched_file"]]
    assert watched == [("added", 'wait_until_seen_by_player(boolean) : "Wait until seen by player" : 0'),
                       ("removed", 'input BecomeRagdoll(void) : "go limp"')]
    assert all(f.score == 55 for f in out if f.keywords == ["watched_file"])
    assert src._extra_findings("cs2", "game/csgo/csgo.fgd", lines, matcher) == []   # solo le FGD condivise
    base = [f.score for f in src._extra_findings("cs2", "game/core/base.fgd", lines, matcher) if f.keywords == ["watched_file"]]
    assert base == [40, 40]                                                        # base.fgd: sul sito, senza notifica
    db.close()
