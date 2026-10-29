from types import SimpleNamespace

import yaml

from hlxwatch import announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import Pipeline
from hlxwatch.models import Finding
from hlxwatch.notifiers.base import group_findings
from hlxwatch.notifiers.webpush import WebPushNotifier
from hlxwatch.repos import followed_repos, tracked_repos
from hlxwatch.sources.discovery import RepoDiscovery
from hlxwatch.sources.steam_pics import SteamPicsSource
from hlxwatch.sources.steam_webapi import SteamNewAppsSource
from hlxwatch.storage import Database


def make(tmp_path, monkeypatch=None):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    if monkeypatch:
        monkeypatch.setattr(type(cfg), "steam_web_api_key", property(lambda self: None))
    return cfg, Database(cfg.db_path)


def repo(name, desc=""):
    return {"full_name": f"SteamTracking/{name}", "name": name, "description": desc, "created_at": "2026-12-01T10:00:00Z",
            "default_branch": "master", "html_url": f"https://github.com/SteamTracking/{name}"}


class FakeGitHub:
    def __init__(self, repos):
        self.repos = repos

    def get_json(self, path, params=None):
        return list(self.repos) if (params or {}).get("page", 1) == 1 else []


def test_looks_like_hlx():
    for name in ("GameTracking-HalfLife3", "GameTracking-HLX", "Half-Life 3", "HALF-LIFE: Something", "hl3"):
        assert announcement.looks_like_hlx(name), name
    for name in ("GameTracking-TF2", "Counter-Strike 2", "SteamVR", "hl30 server", None):
        assert not announcement.looks_like_hlx(name), name


def test_new_hlx_repo_is_one_big_alert_and_ignores_hlx(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeGitHub([repo("GameTracking-CS2")])
    disc = RepoDiscovery(cfg, db, client)
    disc.run()                                                          # primo giro: solo registrazione
    client.repos += [repo("GameTracking-HalfLife3", "Game Tracker: Half-Life 3"), repo("GameTracking-Foo")]
    found = {f.file_path: f for f in disc.run()}
    hl3 = found["SteamTracking/GameTracking-HalfLife3"]
    assert hl3.score >= 200 and announcement.KEYWORD in hl3.keywords and hl3.text.startswith("POSSIBILE ANNUNCIO: ")
    other = found["SteamTracking/GameTracking-Foo"]
    assert announcement.KEYWORD not in other.keywords and other.score == 70
    followed = {r["repo"]: r for r in followed_repos(db)}
    ignored = followed["SteamTracking/GameTracking-HalfLife3"]["ignore_keywords"]
    assert {"hlx", "hlx_class", "hl3", "half_life"} <= set(ignored)
    assert "ignore_keywords" not in followed["SteamTracking/GameTracking-Foo"]
    # nel repository del gioco una riga HLX non è una voce; nello stesso tempo il resto delle regole lavora
    game = next(r for r in tracked_repos(cfg, db) if r["repo"].endswith("HalfLife3"))
    matcher = KeywordMatcher(cfg.keywords, ignore_ids=game.get("ignore_keywords", []))
    assert matcher.analyze_line("weapon_hlx_gravitygun", file_path="game/hlx/scripts/x.txt") is None
    assert KeywordMatcher(cfg.keywords).analyze_line("weapon_hlx_gravitygun", file_path="x.txt") is not None
    # titolo dedicato della notifica
    alerts = Pipeline(cfg, db).select_alerts(list(found.values()))
    assert announcement.announcement_heading(alerts) == announcement.HEADING
    assert announcement.announcement_heading([other]) is None
    db.close()


class FakePics:
    changes_gap = False

    def __init__(self, apps):
        self.apps = apps

    def call_worker(self, appids, since=-1):
        return {"ok": True, "apps": {str(a): self.apps.get(a, {"_missing_token": True}) for a in appids}}


def valve_app(name):
    return {"common": {"name": name, "type": "game", "associations": {"0": {"type": "developer", "name": "Valve"}}}}


def test_valve_half_life_app_is_announced_and_watched(tmp_path, monkeypatch):
    cfg, db = make(tmp_path, monkeypatch)
    db.insert_apps([(5000001, None), (5000002, None), (5000003, None)], checked=False)
    db.set_state("steam:appid_frontier", 5000003)
    pics = FakePics({5000001: valve_app("Half-Life 3"), 5000002: valve_app("Counter-Strike 3"),
                     5000003: {"common": {"name": "Half-Life Fan Remake", "type": "game",
                                          "associations": {"0": {"type": "developer", "name": "Fans"}}}}})
    found = {f.token: f for f in SteamNewAppsSource(cfg, db, pics).run([])}
    hl3 = found["app:5000001"]
    assert announcement.KEYWORD in hl3.keywords and hl3.score >= 200 and "seguite su Steam" in hl3.context
    assert announcement.KEYWORD not in found["app:5000002"].keywords                  # Valve ma non Half-Life
    assert "app:5000003" not in found or announcement.KEYWORD not in found["app:5000003"].keywords   # non di Valve
    extra = announcement.extra_watch(db)
    assert [(a["appid"], a["name"], a["title"]) for a in extra] == [(5000001, "halflife3", "Half-Life 3")]
    watch = SteamPicsSource(cfg, db).watch_apps()
    assert 5000001 in [int(a["appid"]) for a in watch] and 730 in [int(a["appid"]) for a in watch]
    assert announcement.add_watch(db, cfg, 5000001, "Half-Life 3") is None                      # una volta sola
    db.close()


def test_webpush_announcement_is_urgent_with_its_title(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"sent": 1, "statuses": {"201": 1}, "cursor": None},
                               text="")

    monkeypatch.setattr("hlxwatch.notifiers.webpush.requests.post", fake_post)
    f = Finding(game="steamtracking", source="discovery", file_path="SteamTracking/GameTracking-HalfLife3",
                change_type="new_repo", text="POSSIBILE ANNUNCIO: Nuovo repository", score=200,
                keywords=["new_repo", announcement.KEYWORD])
    WebPushNotifier("https://bot.example", "segreto", "https://site.example/").send_groups(
        announcement.HEADING, group_findings([f], cfg))
    assert sent[0]["message"]["urgente"] is True and sent[0]["message"]["title"] == announcement.HEADING
    db.close()
