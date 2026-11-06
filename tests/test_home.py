"""Pagina iniziale: i riquadri delle risposte e le ultime tracce."""
import yaml

from hlxwatch import home
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.storage import Database


def test_answers(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    db = Database(Config(raw, tmp_path / "config.yaml").db_path)
    reg = {"traces": [{"label": f"mod != hlx || x{i}", "first": f"2025-0{i}-01", "games": ["cs2"], "slug": f"t{i}"}
                      for i in range(1, 4)] + [{"label": "CHLXCoverPreviewHelper", "first": "2026-09-23", "games": ["cs2"],
                                                "slug": "cover"}]}
    r = home.answers(db, reg, None)
    assert r["sistemi"]["n"] == 4 and r["sistemi"]["top"][0]["name"] == "Particelle ed effetti"
    assert r["sistemi"]["top"][0]["pct"] == 100 and "Interfaccia" in r["sistemi"]["empty"]
    assert r["traces"][0]["label"] == "CHLXCoverPreviewHelper" and r["traces"][0]["icon"] == "👾"
    assert r["segnali"]["total"] == 6 and r["segnali"]["have"] == 2          # nome in codice e primo avviso
    assert {p["project"] for p in r["passato"]} >= {"Deadlock", "Steam Deck", "Steam Frame"}
    db.close()


def test_visit_data_uses_first_inserted_row(tmp_path):
    from datetime import datetime, timezone
    from hlxwatch.models import BuildInfo, Finding
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    db = Database(Config(raw, tmp_path / "config.yaml").db_path)
    bid = db.upsert_build(BuildInfo(game="cs2", source="github", ref="a", date=datetime.now(timezone.utc)))
    f = Finding(game="cs2", source="fullscan", file_path="a", change_type="added", text="CHLXCoverPreviewHelper",
                score=120, keywords=["hlx_class"], token="CHLXCoverPreviewHelper")
    db.add_finding(f, bid, created_at=datetime(2026, 10, 29, 14, 11, tzinfo=timezone.utc))
    later = Finding(game="cs2", source="github", file_path="a", change_type="added", text="CHLXCoverPreviewHelper",
                    score=120, keywords=["hlx_class"], token="CHLXCoverPreviewHelper")
    db.add_finding(later, bid, created_at=datetime(2026, 9, 23, tzinfo=timezone.utc))   # riga datata al commit
    v = home.visit_data(db, {"traces": [{"key": "chlxcoverpreviewhelper", "label": "CHLXCoverPreviewHelper",
                                         "slug": "c"}]})
    assert v["tracce"][0]["f"].startswith("2026-10-29")
    db.close()
