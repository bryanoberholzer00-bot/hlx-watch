import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reevaluate import false_removals, reevaluate
from hlxwatch.reports.site import build_site
from hlxwatch.reports.site_extras import source_status
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow


def make(tmp_path, repos=None, min_score=50):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    if repos:
        raw["github"]["repos"] = repos
    raw.setdefault("full_scan", {})["min_score"] = min_score
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")
def test_full_scan_ignores_pure_score_changes(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("maps/hlx_test_level.vpk\n", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "uno")
    repos = [{"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "main", "clone_url": origin.as_uri()}]
    cfg, db = make(tmp_path, repos)
    first = [f for f in FullScanSource(cfg, db).run(force=True) if f.file_path == "a.txt"]
    assert [f.change_type for f in first] == ["present"]
    score = first[0].score

    # soglia alzata sopra il punteggio della riga (come quando si abbassa il peso di una parola chiave):
    # la riga è ancora nei file, quindi non è "tolta"
    db.close()
    cfg_high, db = make(tmp_path, repos, min_score=score + 1)
    assert [f for f in FullScanSource(cfg_high, db).run(force=True) if f.file_path == "a.txt"] == []
    # soglia di nuovo bassa: la riga c'era già, quindi non è "aggiunta"
    db.close()
    cfg, db = make(tmp_path, repos)
    assert [f for f in FullScanSource(cfg, db).run(force=True) if f.file_path == "a.txt"] == []

    # tolta davvero dai file: questa sì è una voce
    (origin / "a.txt").write_text("niente\n", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "due")
    gone = [f for f in FullScanSource(cfg, db).run(force=True) if f.file_path == "a.txt"]
    assert [f.change_type for f in gone] == ["removed"]
    db.close()


def test_reevaluate_drops_contradictory_removals(tmp_path):
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="steamclient", source="fullscan", ref="tree:abc", date=utcnow()))
    other = db.upsert_build(BuildInfo(game="steamclient", source="fullscan", ref="tree:def", date=utcnow()))
    path = "ClientExtracted/controller_base/chord_neptune.vdf"
    db.add_finding(Finding(game="steamclient", source="fullscan", file_path=path, change_type="added",
                           text='"controller_type"\t\t"controller_neptune"', score=35, keywords=["valve_hardware"]), b)
    db.add_finding(Finding(game="steamclient", source="fullscan", file_path=path, change_type="removed",
                           text='"controller_type" "controller_neptune"', score=35, keywords=["valve_hardware"]), b)
    # la stessa riga tolta in un'altra versione dei file è legittima
    db.add_finding(Finding(game="steamclient", source="fullscan", file_path=path, change_type="removed",
                           text='"controller_type" "controller_neptune"', score=35, keywords=["valve_hardware"]), other)
    assert len(false_removals(db)) == 1
    stats = reevaluate(cfg, db)
    assert stats["tolte_false"] == 1
    kinds = sorted((r["build_id"], r["change_type"]) for r in db.conn.execute("SELECT build_id, change_type FROM findings"))
    assert kinds == [(b, "added"), (other, "removed")]
    db.close()


def test_status_shows_fast_check_and_only_unusual_steam_gaps(tmp_path):
    cfg, db = make(tmp_path)
    now = utcnow()
    db.set_state("steam:changes_gap_at", iso(now - timedelta(hours=1)))
    db.set_state("steam:changes_gap_hours", "4.6")                   # dopo 4-5 ore: normale, non si mostra
    assert not source_status(cfg, db)["changes_gap_recent"]
    db.set_state("steam:changes_gap_hours", "1.2")                   # dopo poco più di un'ora: insolito
    st = source_status(cfg, db, {"ultimo_ok": iso(now), "minuti": 3, "fermo": False})
    assert st["changes_gap_recent"] and st["changes_gap_hours"] == 1.2 and st["fast"]["minuti"] == 3

    db.set_state("steam:changes_gap_hours", "5")
    out = build_site(db, cfg, tmp_path / "site")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Steam ha saltato" not in html
    db.close()


def test_site_renders_fast_check_status(tmp_path, monkeypatch):
    import hlxwatch.reports.site as site
    cfg, db = make(tmp_path)
    monkeypatch.setattr(site, "fast_status", lambda cfg: {"ultimo_ok": "2026-10-30T17:30:00Z", "minuti": 5, "fermo": False})
    html = (build_site(db, cfg, tmp_path / "a") / "index.html").read_text(encoding="utf-8")
    assert "Controllo veloce dell'annuncio" in html and "ultimo giro riuscito 2026-10-30 18:30" in html
    monkeypatch.setattr(site, "fast_status", lambda cfg: {"ultimo_ok": None, "minuti": None, "fermo": True,
                                                          "irraggiungibile": True})
    html = (build_site(db, cfg, tmp_path / "b") / "index.html").read_text(encoding="utf-8")
    assert "il bot non risponde" in html
    db.close()
