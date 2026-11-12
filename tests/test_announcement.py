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


def test_webpush_announcement_turns_on_the_mode_but_the_drill_does_not(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    sent = []
    monkeypatch.setattr("hlxwatch.notifiers.webpush.requests.post", lambda url, json=None, headers=None, timeout=None: (
        sent.append(json) or SimpleNamespace(status_code=200, json=lambda: {"sent": 1, "statuses": {}, "cursor": None}, text="")))
    f = Finding(game="steamtracking", source="discovery", file_path="SteamTracking/GameTracking-HalfLife3",
                change_type="new_repo", text="POSSIBILE ANNUNCIO: Nuovo repository", score=200,
                keywords=["new_repo", announcement.KEYWORD])
    n = WebPushNotifier("https://bot.example", "segreto", "https://site.example/")
    n.send_groups(announcement.HEADING, group_findings([f], cfg))
    assert sent[-1]["message"]["annuncio"] is True and "Nuovo repository" in sent[-1]["message"]["voci"][0]["x"]
    n.send_groups(announcement.DRILL_HEADING, group_findings([f], cfg))
    assert "annuncio" not in sent[-1]["message"]                    # la prova generale non accende niente
    db.close()


def test_alyx_after_announcement():
    seed = {"announced": "2019-11-21", "released": "2020-03-23", "names": [
        {"name": "hlvr", "day": "2016-12-11"}, {"name": "soundstacks_hlvr", "day": "2020-03-04"},
        {"name": "hlalyxpromotitle", "day": "2020-03-12"}, {"name": "hlalyxpromodesc", "day": "2020-03-12"}]}
    a = announcement.alyx_after(seed, 110)
    assert a["primo"] == 104 and a["prima"] == 1 and a["dopo"] == 3 and a["giorni_uscita"] == 123
    assert [(t["giorno"], t["nomi"]) for t in a["tappe"]] == [(104, 1), (112, 2)]
    assert a["fino_a"] == 1
    assert announcement.alyx_after(None) is None


def test_after_page(tmp_path):
    cfg, db = make(tmp_path)
    assert announcement.after_page(db, None, None) == {"attiva": False, "alyx": None}
    db.conn.execute("INSERT INTO builds (game, source, ref, date, created_at) VALUES ('cs2', 'github', 'r1', "
                    "'2026-11-05T20:00:00Z', '2026-11-05T20:00:00Z')")
    for text, when, score in (("prima", "2026-11-05T19:00:00Z", 120), ("dopo", "2026-11-05T21:00:00Z", 120),
                              ("debole", "2026-11-05T22:00:00Z", 10)):
        db.conn.execute("INSERT INTO findings (build_id, game, source, file_path, change_type, text, score, keywords, dedup_hash, "
                        "created_at) VALUES (1, 'cs2', 'github', 'a.txt', 'added', ?, ?, '[\"hlx\"]', ?, ?)", (text, score, text, when))
    mode = {"da": "2026-11-05T20:00:00.000Z", "fino": "2026-11-19T20:00:00.000Z", "giorno": 2, "voci": []}
    p = announcement.after_page(db, mode, None)
    assert p["attiva"] and p["giorno"] == 2 and [n["testo"] for n in p["novita"]] == ["dopo"]
    db.close()


def test_placeholder_names_count_only_for_valve(tmp_path, monkeypatch):
    """12/11/2026: "Untitled Telemetry App" (strumento per iRacing di Babaika) aveva 120 punti per la parola untitled."""
    from hlxwatch.sources.steam_webapi import name_rule
    cfg, db = make(tmp_path, monkeypatch)
    ncfg = cfg.get("steam.new_apps", {})
    assert name_rule(ncfg, "Untitled Telemetry App", "Babaika", "Babaika")[0] == 0
    assert name_rule(ncfg, "Untitled Project", "Valve", "Valve")[0] == 120
    assert name_rule(ncfg, "Untitled Project", None, None)[0] == 50                 # autore sconosciuto: senza avviso
    assert name_rule(ncfg, "HLX", None, None)[0] == 120
    assert name_rule(ncfg, "Half-Life Fan Remake", "Fans", "?")[0] == 60             # parola forte, altro autore
    assert name_rule(ncfg, "Test App 3", "Someone", None)[0] == 0 and name_rule(ncfg, "Pressure Valve", "Indie", None)[0] == 0
    db.insert_apps([(5000011, None), (5000012, None)], checked=False)
    db.set_state("steam:appid_frontier", 5000012)
    other = {"common": {"name": "Untitled Telemetry App", "type": "application",
                        "associations": {"0": {"type": "developer", "name": "Babaika"}}}}
    found = {f.token: f for f in SteamNewAppsSource(cfg, db, FakePics({5000011: other, 5000012: valve_app("Untitled")})).run([])}
    assert "app:5000011" not in found and found["app:5000012"].score == 120
    db.close()


def test_reevaluate_lowers_old_placeholder_alerts(tmp_path):
    import json
    from hlxwatch.models import BuildInfo
    from hlxwatch.reevaluate import reevaluate
    from hlxwatch.storage.db import utcnow
    cfg, db = make(tmp_path)
    for appid, text, kws in ((5544150, "Nuova app 5544150: Untitled Telemetry App [Application] dev=Babaika pub=Babaika",
                              ["suspicious_name", "private_app"]),
                             (5544151, "Nuova app 5544151: Untitled Thing [Game] dev=? pub=?", ["suspicious_name"]),
                             (5544152, "Nuova app 5544152: HLX [Game] dev=? pub=?", ["suspicious_name"])):
        b = db.upsert_build(BuildInfo(game="steam", source="steam", ref=f"app:{appid}", date=utcnow(), message=text))
        db.add_finding(Finding(game="steam", source="steam_apps", file_path=f"app/{appid}", change_type="new_app",
                               text=text, score=120, keywords=kws, token=f"app:{appid}"), b)
    reevaluate(cfg, db)
    left = {r["token"]: (r["score"], json.loads(r["keywords"])) for r in db.conn.execute(
        "SELECT token, score, keywords FROM findings WHERE change_type = 'new_app'")}
    assert "app:5544150" not in left                                   # nessun segnale rimasto: via
    assert left["app:5544151"] == (50, ["suspicious_name"]) and left["app:5544152"][0] == 120
    db.close()


def test_lessons_include_the_placeholder_case(tmp_path):
    from hlxwatch.lezioni import Checker, static_lessons
    cfg, db = make(tmp_path)
    lessons = [x for x in static_lessons() if x.get("controllo") == "app_nuova"]
    assert {x["id"] for x in lessons} >= {"app-untitled-di-altri", "app-untitled-di-valve", "app-hlx-privata"}
    assert all(Checker(cfg).check(x) is None for x in lessons)
    db.close()
