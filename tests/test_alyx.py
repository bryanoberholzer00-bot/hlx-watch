from datetime import timedelta

import yaml

from hlxwatch import alyx
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

HISTORY = """@@@aaa111|2017-06-04
+++ b/Strings/libclient.txt
+hlvr_weapon_energygun
+ACT_DI_ALYX_ZOMBIE_MELEE
+27CMarkupVolumeTagged_NavHLVR
+35CSchemaNonConstructibleClassBindingI27CMarkupVolumeTagged_NavHLVRE
-item_hlvr_removed_line
@@@bbb222|2019-06-13
+++ b/game/dac/bin/libclient_strings.txt
+hlvr_combine_barrier_cell_spacing
+hlvr_weapon_energygun
@@@ccc333|2020-03-12
+++ b/resource/steamvr_english.txt
+\t"HLAlyxPromoTitle"\t"New Environments Inspired By Half-Life: Alyx"
"""


def test_scan_history_first_seen_and_cleanup():
    found = alyx.scan_history(HISTORY.splitlines(True), "steamvr")
    assert found["hlvr_weapon_energygun"]["day"] == "2017-06-04"          # la prima comparsa, non la seconda
    assert found["cmarkupvolumetagged_navhlvr"]["path"] == "Strings/libclient.txt"
    assert "item_hlvr_removed_line" not in found                         # solo righe aggiunte
    assert not any("alyx_zombie" in k or "cschema" in k for k in found)  # Episodio 2 e simboli storpiati
    assert "hlalyxpromotitle" in found and len(found) == 4
    assert alyx.clean("tyu5htmg581yhlvrf4jollowx06fh83") is None           # base64 nei CSS di Steam
    assert alyx.clean("hlvr_default_3d") == "hlvr_default_3d"


def seed():
    names = [{"name": "hlvr_weapon_energygun", "day": "2017-06-04", "repo": "steamvr", "path": "a.txt"},
             {"name": "hlvr_combine_barrier_cell_spacing", "day": "2019-06-13", "repo": "underlords", "path": "b.txt"},
             {"name": "hlalyxpromotitle", "day": "2020-03-12", "repo": "steamvr", "path": "c.txt"}]
    return {"announced": "2019-11-21", "released": "2020-03-23", "names": names,
            "repos": {"steamvr": {"title": "SteamVR", "start": "2017-05-23", "names": 2},
                      "underlords": {"title": "Dota Underlords", "start": "2019-06-13", "names": 1}}}


def test_compare_numbers_and_links():
    reg = {"traces": [{"label": "hlx_combine_barrier_cell_spacing", "first": "2021-09-16", "games": {"dota2": "x"}},
                      {"label": "hlx.hrtf_basic", "first": "2021-03-04", "games": {"cs2": "x"}}]}
    c = alyx.compare(seed(), reg, "2026-10-31")
    assert c["alyx"]["pre"] == 2 and c["alyx"]["total"] == 3
    assert c["alyx"]["months_to_announce"] == 29 and c["alyx"]["at_archive_start"]
    assert c["hlx"]["months"] == 67 and c["hlx"]["total"] == 2 and c["hlx"]["first"] == "2021-03-04"
    assert c["links"] == [{"hlx": "hlx_combine_barrier_cell_spacing", "hlx_it": "16/09/2021",
                           "alyx": "hlvr_combine_barrier_cell_spacing", "alyx_it": "13/06/2019"}]
    assert c["alyx"]["curve"][-1] == 3 and c["hlx"]["curve"][-1] == 2
    assert c["years"]["2017"][0]["meaning"] == "la pistola"
    assert c["chart"]["alyx"].startswith("M") and c["chart"]["xticks"][1]["label"] == "1 anno"
    assert alyx.compare(None, reg, "2026-10-31") is None


def test_site_has_alyx_page(tmp_path, monkeypatch):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="c" * 40, date=utcnow() - timedelta(days=3)))
    db.add_finding(Finding(game="cs2", source="github", file_path="game/bin/win64/tools/hammer_strings.txt",
                           change_type="added", text="CHLXCoverPreviewHelper", token="CHLXCoverPreviewHelper",
                           score=115, keywords=["hlx_class"]), b)
    monkeypatch.setattr(alyx, "load_seed", lambda path=None: seed())
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "alyx.html").read_text(encoding="utf-8")
    assert "HLX e Half-Life: Alyx a confronto" in page and 'class="alyxchart"' in page
    assert "hlvr_weapon_energygun" in page and "la pistola" in page and "almeno" in page
    assert 'href="alyx.html"' in (out / "hlx.html").read_text(encoding="utf-8")
    db.close()
