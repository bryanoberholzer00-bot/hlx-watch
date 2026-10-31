from datetime import timedelta

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site, bundle_rows, glance
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_bundle_rows_merges_same_commit_and_file():
    rows = [
        {"build_id": 1, "file_path": "a.txt", "change_type": "added", "token": "CHLXOne", "score": 110, "severity": "high"},
        {"build_id": 1, "file_path": "a.txt", "change_type": "added", "token": "GetAttributeTable_CHLXOne", "score": 115,
         "severity": "high", "is_new": True},
        {"build_id": 1, "file_path": "b.txt", "change_type": "added", "token": "CHLXOne", "score": 110, "severity": "high"},
        {"build_id": None, "file_path": "a.txt", "change_type": "added", "token": "x", "score": 100, "severity": "high"},
    ]
    out = bundle_rows(rows)
    assert len(out) == 3
    assert out[0]["related"] == ["GetAttributeTable_CHLXOne"] and out[0]["score"] == 115 and out[0]["is_new"]


def test_glance_flags_problems():
    termo = {"indice": 10}
    fresh = {"last_scan": iso(utcnow() - timedelta(hours=1)), "repos": [], "problems": []}
    assert glance(termo, fresh)["ok"]
    old = {"last_scan": iso(utcnow() - timedelta(hours=30)), "repos": [{"title": "Deadlock", "stale": True}],
           "problems": [{"source": "steam_pics"}], "fast": {"irraggiungibile": True}}
    g = glance(termo, old)
    assert not g["ok"] and len(g["problems"]) == 4


def test_home_page_has_glance_menu_and_compact_list(tmp_path):
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="a" * 40, date=utcnow() - timedelta(days=2)))
    for i in range(20):
        db.add_finding(Finding(game="cs2", source="github", file_path=f"game/bin/win64/tools/f{i}.txt", change_type="added",
                               text=f"CHLXHelper{i}", token=f"CHLXHelper{i}", score=115, keywords=["hlx_class"]), b)
    html = (build_site(db, cfg, tmp_path / "site") / "index.html").read_text(encoding="utf-8")
    assert 'class="colpo"' in html and "Ultima traccia HLX forte" in html
    assert 'id="nav-btn"' in html and "Altro &#9662;" in html
    # 20 voci in "Alta rilevanza" (15 in vista) e le stesse 20 in "Ultime voci" (12 in vista)
    assert html.count('<li class="voce') == 40
    assert "Mostra tutte (5 in più)" in html and "Mostra tutte (8 in più)" in html
    db.close()


def test_brief_pages_and_link_preview(tmp_path):
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="b" * 40, date=utcnow() - timedelta(days=3)))
    db.add_finding(Finding(game="cs2", source="github", file_path="game/bin/win64/tools/hammer_strings.txt", change_type="added",
                           text="CHLXCoverPreviewHelper", token="CHLXCoverPreviewHelper", score=115,
                           keywords=["hlx_class"]), b)
    out = build_site(db, cfg, tmp_path / "site")
    it = (out / "hlx.html").read_text(encoding="utf-8")
    en = (out / "en.html").read_text(encoding="utf-8")
    assert "Half-Life 3 è stato annunciato?" in it and "<b>No.</b>" in it and "CHLXCoverPreviewHelper" in it
    assert '<html lang="en">' in en and "Has Half-Life 3 been announced?" in en and "Counter-Strike 2" in en
    assert 'property="og:image"' in it and "anteprima.png?v=" in it
    assert 'href="hlx.html"' in (out / "index.html").read_text(encoding="utf-8")
    from PIL import Image
    with Image.open(out / "anteprima.png") as img:
        assert img.size == (1200, 630)
    db.close()


def test_brief_shows_announcement():
    from hlxwatch.reports.brief import brief_data
    termo = {"indice": 100, "livello": "annuncio", "etichetta": "POSSIBILE ANNUNCIO", "colore": "#B71C1C",
             "annuncio": True, "ultima_traccia": None}
    data = brief_data(None, termo, {"picchi": [], "inizio": "2021-01-01"}, {"traces": [], "counts": {}})
    assert data["level_en"] == "POSSIBLE ANNOUNCEMENT" and data["last"] is None and data["first"] is None
