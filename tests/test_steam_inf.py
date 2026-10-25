import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.github_tracking import GitHubTrackingSource, steam_inf_build
from hlxwatch.storage import Database

# patch reale di SteamTracking/GameTracking-Deadlock (aggiornamento del 22 ottobre 2026)
PATCH = """@@ -1,9 +1,9 @@
-ClientVersion=6815
-ServerVersion=6815
+ClientVersion=6817
+ServerVersion=6817
 ProductName=citadel
 appID=1422450
 ServerAppID=1422460
 ToolsAppID=211
-SourceRevision=11161138
+SourceRevision=11161671
 VersionDate=Oct 22 2026
-VersionTime=12:19:38
+VersionTime=13:30:07"""


def test_steam_inf_build():
    info = steam_inf_build(PATCH)
    assert info == {"version": "6817", "previous": "6815", "revision": "11161671", "date": ""}
    only_time = "@@ -9 +9 @@\n-VersionTime=12:19:38\n+VersionTime=13:30:07"
    assert steam_inf_build(only_time) is None                     # stessa versione: nessuna build
    assert steam_inf_build("+ClientVersion=1\n+SourceRevision=5")["previous"] == ""   # file nuovo


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    return cfg, db, GitHubTrackingSource(cfg, db)


def detail(path):
    return {"sha": "abc", "parents": [{"sha": "def"}],
            "files": [{"filename": path, "status": "modified", "patch": PATCH, "additions": 4, "deletions": 4}]}


def test_build_finding_only_where_enabled(tmp_path):
    cfg, db, src = make(tmp_path)
    matcher = KeywordMatcher(cfg.keywords)
    deadlock = next(r for r in cfg.get("github.repos") if r["name"] == "deadlock")
    assert deadlock.get("build_from_steam_inf") is True
    found = [f for f in src._analyze_commit(deadlock, detail("game/citadel/steam.inf"), matcher)
             if f.change_type == "build"]
    assert len(found) == 1
    f = found[0]
    assert f.text == "Deadlock: nuova build 6817 (prima 6815)" and f.token == "build:deadlock:6817"
    assert f.score == 45 and f.keywords == ["steam_build"] and "11161671" in f.context
    # Dota 2 ha già le build da Steam: niente doppioni
    dota = next(r for r in cfg.get("github.repos") if r["name"] == "dota2")
    assert not [f for f in src._analyze_commit(dota, detail("game/dota/steam.inf"), matcher)
                if f.change_type == "build"]
    db.close()
