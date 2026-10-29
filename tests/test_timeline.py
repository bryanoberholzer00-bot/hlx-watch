import json
from datetime import datetime, timezone
from types import SimpleNamespace

import yaml

from hlxwatch import timeline
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database


def make(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "SEED", tmp_path / "nessun_seme.json")     # nei test si parte da zero
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def add(db, game, text, change_type, when, score=110, sha="a"):
    b = BuildInfo(game=game, source="github", ref=f"{game}{sha}", date=when,
                  url=f"https://github.com/SteamTracking/GameTracking-X/commit/{game}{sha}")
    bid = db.upsert_build(b)
    f = Finding(game=game, source="github", file_path="DumpSource2/x.h", change_type=change_type, text=text,
                score=score, keywords=["hlx"], token="hlx")
    db.add_finding(f, bid)


class FakeClient:
    def __init__(self, result):
        self.calls = 0
        self.result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.result))])


def test_group_key_merges_games_and_numbers():
    assert timeline.group_key('// MPropertySuppressExpr = "mod != hlx"') == timeline.group_key('//  mpropertysuppressexpr = "MOD != hlx" ')
    assert timeline.group_key('<clip exportframe="12" exportscene="d:/a/b">') == timeline.group_key('<clip exportframe="7" exportscene="d:/c">')


def test_classify_once_and_build_entries(tmp_path, monkeypatch):
    cfg, db = make(tmp_path, monkeypatch)
    add(db, "cs2", '// MPropertySuppressExpr = "mod != hlx"', "added", datetime(2026, 7, 1, tzinfo=timezone.utc), score=130)
    add(db, "dota2", '// MPropertySuppressExpr = "mod != hlx"', "added", datetime(2026, 6, 1, tzinfo=timezone.utc), score=130, sha="b")
    add(db, "cs2", "scripts/x.vdata!hlx_spawner", "removed", datetime(2026, 7, 9, tzinfo=timezone.utc), score=120, sha="c")
    add(db, "alyx", ";hlx>", "added", datetime(2020, 5, 15, tzinfo=timezone.utc), sha="d")
    client = FakeClient({"indizi": [
        {"id": "nuovo", "titolo": "mod != hlx", "spiegazione": "Opzioni riservate a hlx.", "rilevanza": "alta",
         "cerca": "mod != hlx", "gruppi": ["G1"]},
        {"id": "nuovo", "titolo": "Spawner rimosso", "spiegazione": "Rimosso da CS2.", "rilevanza": "media",
         "cerca": "hlx_spawner", "gruppi": ["G2"]},
        {"id": "nuovo", "titolo": "Rumore", "spiegazione": "Coincidenza.", "rilevanza": "rumore",
         "cerca": "", "gruppi": ["G3"]},
    ]})
    assert timeline.update_timeline(cfg, db, client) == 3 and client.calls == 1
    assert timeline.update_timeline(cfg, db, client) == 0 and client.calls == 1      # già classificati: niente AI
    data = timeline.build_entries(cfg, db)
    assert [e["titolo"] for e in data["entries"]] == ["Spawner rimosso", "mod != hlx"]
    mod = data["entries"][1]
    assert mod["data"] == "2026-06-01" and mod["giochi"] == ["Counter-Strike 2", "Dota 2"] and mod["righe"] == 2
    assert mod["fonte"].endswith("/commit/dota2b") and not mod["rimosso"]
    assert data["entries"][0]["rimosso"] == "2026-07-09"
    assert [e["titolo"] for e in data["noise"]] == ["Rumore"]
    cfg.raw["timeline"]["nascondi"] = ["t2"]
    assert [e["titolo"] for e in timeline.build_entries(cfg, db)["entries"]] == ["mod != hlx"]
    cfg.raw["timeline"]["correzioni"] = {"t1": {"spiegazione": "Testo corretto a mano.", "rilevanza": "bassa"}}
    e = timeline.build_entries(cfg, db)["entries"][0]
    assert e["spiegazione"] == "Testo corretto a mano." and e["rilevanza"] == "bassa" and e["titolo"] == "mod != hlx"
    db.close()


def test_fullscan_added_rows_are_dated_by_first_seen(tmp_path, monkeypatch):
    """Una riga 'added' trovata dalla scansione completa (parola chiave nuova) non prende la data della scansione."""
    cfg, db = make(tmp_path, monkeypatch)
    b = BuildInfo(game="cs2", source="fullscan", ref="tree:abc", date=datetime(2026, 10, 29, tzinfo=timezone.utc),
                  url="https://github.com/SteamTracking/GameTracking-CS2/tree/abc")
    bid = db.upsert_build(b)
    f = Finding(game="cs2", source="fullscan", file_path="game/bin/win64/tools/hammer_strings.txt", change_type="added",
                text="CHLXCoverPreviewHelper", score=115, keywords=["hlx_class"], token="CHLXCoverPreviewHelper")
    db.add_finding(f, bid)
    assert [(r["game"], r["text"]) for r in db.present_without_first_seen()] == [("cs2", "CHLXCoverPreviewHelper")]
    row = {"game": "cs2", "file_path": f.file_path, "text": f.text, "change_type": "added", "source": "fullscan",
           "build_url": b.url, "build_date": "2026-10-29T00:00:00+00:00", "created_at": "2026-10-29T00:00:00+00:00"}
    assert timeline._event(db, row)["date"] is None                       # ancora da datare: niente data falsa
    from hlxwatch.sources.first_seen import line_hash
    db.set_first_seen("cs2", f.file_path, line_hash(f.text), f.text, "10f3693c38", "2026-09-23T00:05:28+00:00", False)
    ev = timeline._event(db, row)
    assert ev["date"] == "2026-09-23T00:05:28+00:00" and ev["url"].endswith("/commit/10f3693c38")
    assert db.present_without_first_seen() == []
    db.close()
