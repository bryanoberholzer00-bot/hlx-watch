import csv
import sqlite3
from datetime import datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.export import export_all, restore_db
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    bid = db.upsert_build(BuildInfo(game="cs2", source="github", ref="abc", date=datetime.now(timezone.utc),
                                    url="https://github.com/SteamTracking/GameTracking-CS2/commit/abc"))
    db.add_finding(Finding(game="cs2", source="github", file_path="convars.txt", change_type="added",
                           text='hlx_test "1", con virgola', score=125, keywords=["hlx_prefix"], token="hlx_test"), bid)
    return cfg, db


def test_export_and_restore_roundtrip(tmp_path):
    cfg, db = make(tmp_path)
    info = export_all(cfg, db, tmp_path / "out", "https://github.com/x/hlx-watch", "https://x.github.io/hlx-watch/")
    assert info["findings"] == 1
    with (tmp_path / "out" / "findings.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["token"] == "hlx_test" and rows[0]["testo"] == 'hlx_test "1", con virgola'
    assert rows[0]["commit"].endswith("/commit/abc")
    assert "metodologia.html" in (tmp_path / "out" / "README.md").read_text(encoding="utf-8")

    target = tmp_path / "restored" / "hlxwatch.sqlite"
    restore_db(tmp_path / "out" / "hlxwatch.sqlite.gz", target)
    con = sqlite3.connect(target)
    assert con.execute("SELECT token FROM findings").fetchone()[0] == "hlx_test"
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    db.close()


def test_methodology_page_follows_config(tmp_path):
    cfg, db = make(tmp_path)
    cfg.raw["keywords"]["items"].append({"id": "parola_di_prova", "pattern": "xyzzy", "weight": 77})
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "metodologia.html").read_text(encoding="utf-8")
    assert "parola_di_prova" in page and ">77<" in page
    assert "SteamTracking/GameTracking-CS2" in page
    assert "/raw/data/findings.csv" in page
    db.close()
