import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.links import duplicates_label
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import apply_first_seen, export_findings, first_seen_days
from hlxwatch.sources.first_seen import line_hash
from hlxwatch.storage import Database
from datetime import datetime, timezone

from hlxwatch.timeutil import now_local

HAMMER = "game/bin/win64/tools/hammer_strings.txt"


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_hlx_fused(tmp_path):
    cfg, db = make(tmp_path)
    m = KeywordMatcher(cfg.keywords)

    def hit(text, path=HAMMER):
        r = m.analyze_line(text, file_path=path, change_type="added")
        return (r.token, r.score) if r and "hlx_fused" in r.keywords else None

    assert hit("CDummyHLXCover::GenOrientationFlags_t") == ("CDummyHLXCover", 110)
    assert hit("hlxcoverpreview") == ("hlxcoverpreview", 110)
    assert hit("CHLXCoverPreviewHelper") is None                    # lo prende hlx_class
    for noise in ("shlx", "shlxq", "rKGevVadHLXYttKJ5MNCs3T", "IkHLXWqRauuu", "u2HLXVKeo", "hlxp", "hlx_cover", "hLxcoverpreview"):
        assert hit(noise) is None, noise
    db.close()


def test_duplicates_label():
    assert duplicates_label("a.txt", ["a.txt"]) == "+ un'altra riga nello stesso file"
    assert duplicates_label("a.txt", ["b.txt", "c.txt", "c.txt"]) == "+ altri 2 file nello stesso commit"
    assert duplicates_label("a.txt", ["b.txt", "a.txt", "a.txt"]) == \
        "+ un altro file nello stesso commit, + altre 2 righe nello stesso file"
    assert duplicates_label("a.txt", ["b.txt", "a.txt"], short=True) == "(+1 file, +1 riga)"
    assert duplicates_label("a.txt", [], short=True) == ""


def test_site_uses_first_seen_for_fullscan_rows(tmp_path):
    cfg, db = make(tmp_path)
    b = BuildInfo(game="cs2", source="fullscan", ref="tree:abc", date=datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    bid = db.upsert_build(b)
    for text in ("CHLXCoverPreviewHelper", "CHLXBaseProjectileEmitterHelper"):
        db.add_finding(Finding(game="cs2", source="fullscan", file_path=HAMMER, change_type="added", text=text,
                               score=115, keywords=["hlx_class"], token=text), bid)
    db.set_first_seen("cs2", HAMMER, line_hash("CHLXCoverPreviewHelper"), "CHLXCoverPreviewHelper",
                      "10f3693c38", "2026-09-23T00:05:28Z", False)
    db.set_first_seen("cs2", HAMMER, line_hash("CHLXBaseProjectileEmitterHelper"), "CHLXBaseProjectileEmitterHelper",
                      "6526f854b5", "2023-06-07T00:00:00Z", True)
    rows = {r["token"]: r for r in export_findings(db)}
    assert rows["CHLXCoverPreviewHelper"]["day"] == "2026-09-23" and not rows["CHLXCoverPreviewHelper"]["al"]
    assert rows["CHLXBaseProjectileEmitterHelper"]["day"] == "2023-06-07" and rows["CHLXBaseProjectileEmitterHelper"]["al"]
    assert rows["CHLXCoverPreviewHelper"]["seen"] == now_local().strftime("%Y-%m-%d")   # "vista il": giorno della scansione
    # le voci dei commit tengono la data del commit
    item = apply_first_seen({"source": "github", "change_type": "added", "game": "cs2", "file_path": HAMMER,
                             "text": "CHLXCoverPreviewHelper", "day": "2026-10-01"}, first_seen_days(db))
    assert item["day"] == "2026-10-01"
    db.close()
