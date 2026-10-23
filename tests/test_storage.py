from datetime import datetime, timezone

from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database


def test_dedup_and_search(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    build = BuildInfo(game="cs2", source="github", ref="abc", date=datetime.now(timezone.utc), url="u")
    bid = db.upsert_build(build)
    assert db.upsert_build(build) == bid  # idempotente

    f1 = Finding(game="cs2", source="github", file_path="a.txt", change_type="added", text='hlx_x "1"',
                 score=110, keywords=["hlx_prefix"], token="hlx_x")
    f2 = Finding(game="dota2", source="github", file_path="b.txt", change_type="changed", text='hlx_x "2"',
                 score=110, keywords=["hlx_prefix"], token="hlx_x")
    db.add_finding(f1, bid)
    db.add_finding(f2, bid)
    assert f1.is_new and not f2.is_new   # stesso token: la seconda non è "nuova"

    rows = db.search_findings(q="hlx_x")
    assert len(rows) == 2
    assert db.count_findings(only_new=True) == 1
    stats = db.stats()
    assert stats["findings"] == 2 and stats["seen_strings"] == 1 and stats["builds"] == 1

    assert db.upsert_steam_build(730, "cs2", "public", "123", None) is True
    assert db.upsert_steam_build(730, "cs2", "public", "123", None) is False

    db.set_state("k", 5)
    assert db.get_state("k") == "5"

    db.insert_apps([(100, None), (101, None)], checked=False)
    db.update_app_details(100, None, None, None, None, None, False, "informazioni non pubbliche (richiede token)")
    db.update_app_details(101, "Gioco", "game", "Qualcuno", None, None, False, None)
    assert db.apps_without_details(10) == []
    assert db.reset_private_apps([100, 101, 999]) == 1     # solo la privata torna da controllare
    assert db.apps_without_details(10) == [100]
    db.close()
