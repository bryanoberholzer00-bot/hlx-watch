import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.storage import Database

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def make_cfg(tmp_path: Path, origin: Path) -> Config:
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["github"]["repos"] = [{"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "main",
                               "clone_url": origin.as_uri()}]
    return Config(raw, tmp_path / "config.yaml")


def test_full_scan_present_then_added_and_removed(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "fgd").mkdir()
    (origin / "fgd" / "ai.fgd").write_text('"- Thumper (HLX): causes antlions to run away"\nnpc_generic\n', encoding="utf-8")
    (origin / "big_strings.txt").write_text("\n".join(f"riga {i}" for i in range(50000)) + "\nhlx_old_convar\n",
                                            encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "uno")

    cfg = make_cfg(tmp_path, origin)
    db = Database(cfg.db_path)
    src = FullScanSource(cfg, db)

    first = src.run(force=True)
    assert {f.change_type for f in first} == {"present"}
    assert {f.token for f in first} >= {"hlx_old_convar"}
    assert any(f.file_path == "fgd/ai.fgd" for f in first)
    assert all(f.score >= 50 for f in first)           # npc_generic non supera la soglia

    # nessun cambiamento: niente voci
    assert src.run(force=True) == []

    (origin / "big_strings.txt").write_text("hlx_new_convar\nmaps/l4d3_test.vpk\n", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "due")
    second = src.run(force=True)
    kinds = {(f.change_type, f.token) for f in second}
    assert ("added", "hlx_new_convar") in kinds
    assert ("removed", "hlx_old_convar") in kinds
    assert any(f.change_type == "added" and "l4d3" in f.keywords for f in second)

    # l'intervallo giornaliero: senza force non riparte subito
    assert src.run() == []
    db.close()
