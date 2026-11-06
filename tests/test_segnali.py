"""Prima di un annuncio: i segnali dei progetti passati e la colonna di HLX."""
from datetime import datetime, timezone

import yaml

from hlxwatch import segnali
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database


def test_past_days_before_announcement():
    rows = {r["key"]: r for r in segnali.past_table()}
    col = [p[0] for p in segnali.PROJECTS].index("deadlock")
    assert rows["sito"]["cells"][col]["days"] == 79           # playdeadlock.com
    assert rows["repo"]["cells"][col]["days"] == 14           # GameTracking-Deadlock
    assert rows["avviso"]["cells"][col]["days"] == 1247
    assert all("no" in c for c in rows["testi"]["cells"])     # testi tradotti: mai prima di un annuncio


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_hlx_column_from_archive(tmp_path):
    cfg, db = make(tmp_path)
    reg = {"traces": [{"label": "hlx.hrtf_basic", "first": "2021-03-04", "games": ["dota2"], "file": "a.vsndevts"},
                      {"label": "hlx_menu", "first": "2026-10-01", "games": ["cs2"],
                       "file": "game/hlx/panorama/layout/menu.vxml"}]}
    s = segnali.summary(db, reg)
    hlx = {r["key"]: r["hlx"] for r in s["rows"]}
    assert hlx["nome"]["day"] == "2021-03-04" and hlx["interfaccia"]["day"] == "2026-10-01"
    assert "no" in hlx["testi"] and "no" in hlx["sito"] and "no" in hlx["repo"]
    assert "Sito nei certificati" in s["missing"] and "Menu o schermate" not in s["missing"]
    # un sottodominio nuovo di half-life.com non basta; un dominio "possibile annuncio" sì
    bid = db.upsert_build(BuildInfo(game="valve", source="certs", ref="c", date=datetime.now(timezone.utc)))
    db.add_finding(Finding(game="valve", source="certs", file_path="beta.half-life.com", change_type="domain",
                           text="dominio nuovo: beta.half-life.com", score=40), bid)
    assert "no" in segnali.hlx_column(db, reg)["sito"]
    db.add_finding(Finding(game="valve", source="certs", file_path="halflife3.com", change_type="domain",
                           text="POSSIBILE ANNUNCIO: dominio nuovo halflife3.com", score=200,
                           keywords=["possible_announcement"]), bid)
    assert segnali.hlx_column(db, reg)["sito"]["fonte"] == "halflife3.com"
    db.close()
