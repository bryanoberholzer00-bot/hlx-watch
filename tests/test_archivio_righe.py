"""Archivio completo delle righe nuove (archivio_righe.py): raccolta durante la scansione, riempimento, ricerca, prove."""
from datetime import datetime, timezone

import yaml

from hlxwatch import archivio_righe as ar
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo
from hlxwatch.storage import Database

CONVARS = "DumpSource2/convars.txt"
DUMP = "game/dota/bin/win64/server_strings.txt"


def make(tmp_path, **archive):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw.setdefault("archivio_righe", {}).update({"dal": "2026-01-01", **archive})
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def build(db, game, sha, day):
    return db.upsert_build(BuildInfo(game=game, source="github", ref=sha * 40, date=datetime(2026, 11, day, tzinfo=timezone.utc),
                                     message="x", url=f"https://github.com/SteamTracking/GameTracking-X/commit/{sha * 40}"))


def test_pack_roundtrip():
    lines = [("+", "skill 1 (min: 0, max: 3)"), ("-", "vecchia riga"), ("+", "con\na capo")]
    assert ar.unpack(ar.pack(lines)) == [("+", "skill 1 (min: 0, max: 3)"), ("-", "vecchia riga"), ("+", "con a capo")]


def test_collector_keeps_only_wanted_files_and_added_lines_of_bulky_ones(tmp_path):
    cfg, db = make(tmp_path)
    c = ar.Collector(cfg)
    c.add(CONVARS, [("+", "skill 1 (min: 0, max: 3)"), ("-", "skill 1 (min: 1, max: 3)"), ("+", "   ")])
    c.add(DUMP, [("+", "SaveStreamedOutLevel write"), ("-", "riga tolta da un dump")])
    c.add("game/dota/resource/localization/dota_english.txt", [("+", "testo tradotto")])
    c.add("DumpSource2/.stringsignore", [("+", "x")])
    assert set(c.files) == {CONVARS, DUMP} and c.files[DUMP] == [("+", "SaveStreamedOutLevel write")]
    assert len(c.files[CONVARS]) == 2                     # comandi: anche le righe tolte
    bid = build(db, "dota2", "a", 18)
    assert c.save(db, bid) == 3 and c.files == {}
    assert ar.stats(db)["commit"] == 1
    found = ar.search(db, r"savestreamedout")
    assert len(found) == 1 and found[0]["game"] == "dota2" and found[0]["file"] == DUMP
    assert [r["text"] for r in ar.iter_lines(db, op="-")] == ["skill 1 (min: 1, max: 3)"]
    db.close()


def test_scan_feeds_archive(tmp_path):
    from hlxwatch.analyzers import KeywordMatcher
    from hlxwatch.sources.github_tracking import GitHubTrackingSource
    cfg, db = make(tmp_path)
    src = GitHubTrackingSource(cfg, db)
    src.archive.reset()
    src._extra_findings("dota2", CONVARS, [("+", "hud_showstreaming 0 (developmentonly)")], KeywordMatcher(cfg.keywords))
    assert src.archive.files == {CONVARS: [("+", "hud_showstreaming 0 (developmentonly)")]}
    db.close()


class FakeClient:
    def __init__(self):
        self.raw = []

    def commit_detail(self, repo, sha, max_pages=10):
        return {"sha": sha, "parents": [{"sha": "p" * 40}], "files": [
            {"filename": CONVARS, "status": "modified", "patch": "@@ -1,1 +1,2 @@\n a\n+skill 1 (min: 0, max: 3, gamedll)"},
            {"filename": DUMP, "status": "modified"},                         # senza patch: due versioni
            {"filename": "game/dota/pak01_dir.txt", "status": "modified", "patch": "@@ -1 +1 @@\n+x.vtex_c"},
        ]}

    def raw_file(self, repo, sha, path, max_bytes):
        self.raw.append(sha[:1])
        return "a\nb\n" if sha.startswith("p") else "a\nb\nSaveStreamedOutLevel write\n"


def test_fill_step_and_try_rules(tmp_path):
    cfg, db = make(tmp_path)
    build(db, "dota2", "b", 18)
    client = FakeClient()
    assert ar.fill_step(cfg, db, client, limit=5) == 1 and ar.fill_step(cfg, db, client, limit=5) == 0
    assert sorted(client.raw) == ["b", "p"]
    files = {r["file"] for r in ar.iter_lines(db)}
    assert files == {CONVARS, DUMP}                          # l'elenco dei file del pacchetto no
    rows = ar.try_rules(cfg, db, keywords=["single_player", "single_player_code"])
    assert len(rows) == 1 and rows[0]["righe"] == 2 and {"single_player", "single_player_code"} <= set(rows[0]["parole"])
    db.close()


def test_prune_drops_old_bulky_lines_first(tmp_path):
    cfg, db = make(tmp_path, max_mb=0.0001)
    old, new = build(db, "dota2", "c", 1), build(db, "dota2", "d", 20)
    for bid in (old, new):
        ar.store(db, bid, {DUMP: [("+", f"riga {i} {bid}") for i in range(300)], CONVARS: [("+", f"cmd_{bid} 1")]})
    assert ar.prune(cfg, db) >= 1
    left = {(r["build_id"], r["file"]) for r in ar.iter_lines(db)}
    assert (old, CONVARS) in left and (new, CONVARS) in left    # i comandi restano sempre
    assert (old, DUMP) not in left                              # i dump vecchi vanno per primi
    db.close()


def test_disabled(tmp_path):
    cfg, db = make(tmp_path, enabled=False)
    c = ar.Collector(cfg)
    c.add(CONVARS, [("+", "x")])
    assert c.files == {} and ar.fill_step(cfg, db, FakeClient()) == 0
    db.close()
