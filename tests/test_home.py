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
