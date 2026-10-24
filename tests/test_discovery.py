import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import Pipeline
from hlxwatch.repos import followed_repos, tracked_repos
from hlxwatch.sources.discovery import RepoDiscovery
from hlxwatch.storage import Database


def repo(name, desc="", created="2026-11-01T10:00:00Z"):
    return {"full_name": f"SteamTracking/{name}", "name": name, "description": desc, "created_at": created,
            "default_branch": "master", "html_url": f"https://github.com/SteamTracking/{name}"}


class FakeClient:
    def __init__(self, repos):
        self.repos = repos

    def get_json(self, path, params=None):
        return list(self.repos) if (params or {}).get("page", 1) == 1 else []


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_baseline_then_new_repos(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient([repo("GameTracking-CS2"), repo("SteamTracking")])
    disc = RepoDiscovery(cfg, db, client)
    assert disc.run() == []                                  # primo giro: solo registrazione

    client.repos += [repo("GameTracking-HLX", "📥 Game Tracker: HLX"), repo("SteamSomethingTool", "uno strumento"),
                     repo("GameTracking-Half-Life-3", "📥 Game Tracker: Half-Life 3")]
    found = {f.file_path: f for f in disc.run()}
    assert set(found) == {"SteamTracking/GameTracking-HLX", "SteamTracking/SteamSomethingTool",
                          "SteamTracking/GameTracking-Half-Life-3"}
    assert found["SteamTracking/SteamSomethingTool"].score == 70           # repository qualsiasi: avviso normale
    assert found["SteamTracking/GameTracking-HLX"].score >= 120            # nome hlx: priorità alta
    assert found["SteamTracking/GameTracking-Half-Life-3"].score >= 140    # "Half-Life 3" per esteso
    assert all(f.is_new and f.change_type == "new_repo" for f in found.values())

    followed = {r["repo"]: r for r in followed_repos(db)}
    assert set(followed) == {"SteamTracking/GameTracking-HLX", "SteamTracking/GameTracking-Half-Life-3"}
    assert followed["SteamTracking/GameTracking-HLX"]["name"] == "hlx"
    assert followed["SteamTracking/GameTracking-HLX"]["since"] == "2026-11-01T10:00:00Z"
    names = [r["name"] for r in tracked_repos(cfg, db)]
    assert "hlx" in names and "cs2" in names                  # i seguiti si aggiungono a quelli del config

    assert disc.run() == []                                   # già noti: niente doppioni
    alerts = Pipeline(cfg, db).select_alerts(list(found.values()))
    assert len(alerts) == 3                                   # tutti sopra la soglia delle notifiche
    db.close()


def test_disabled(tmp_path):
    cfg, db = make(tmp_path)
    cfg.raw["repo_discovery"]["enabled"] = False
    assert RepoDiscovery(cfg, db, FakeClient([repo("GameTracking-X")])).run() == []
    db.close()
