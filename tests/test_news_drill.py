import json
from datetime import timedelta

import yaml

from hlxwatch import announcement
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import ALL_SOURCES, Pipeline
from hlxwatch.reports.bot_data import export_bot_data
from hlxwatch.reports.site import build_site
from hlxwatch.sources.steam_news import SteamNewsSource, clean, new_half_life
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeSession:
    """Risponde come ISteamNews/GetNewsForApp, con i post messi in `posts[appid]`."""

    def __init__(self):
        self.headers = {}
        self.posts: dict[int, list] = {}
        self.broken: set[int] = set()
        self.calls = []

    def get(self, url, params=None, timeout=None):
        appid = int(params["appid"])
        self.calls.append(params)
        if appid in self.broken:
            return Resp(500, {})
        return Resp(200, {"appnews": {"appid": appid, "newsitems": list(self.posts.get(appid, []))}})


def post(gid, title, contents="", days_ago=0.0, tags=None, feed="steam_community_announcements"):
    return {"gid": str(gid), "title": title, "contents": contents, "feedname": feed, "tags": tags or [],
            "date": int((utcnow() - timedelta(days=days_ago)).timestamp())}


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.headings = []

    def send_groups(self, heading, groups):
        self.headings.append((heading, [f.text for g in groups for f in g.findings]))
        return True

    def send_text(self, title, text):
        return True


def make(tmp_path, apps=None):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["steam"]["watch_apps"] = [{"appid": 546560, "name": "alyx", "title": "Half-Life: Alyx",
                                   "ignore_keywords": ["half_life", "hl_universe"]}]
    raw["steam_news"]["apps"] = apps or [{"appid": 546560},
                                         {"appid": 593110, "name": "steamclient", "title": "Client Steam", "patchnotes": False}]
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_new_half_life_ignores_released_titles():
    assert new_half_life("Half-Life 3", "")
    assert new_half_life("Announcing Half-Life: Citadel", "")
    assert new_half_life("A new update", "The HLX team says hi")
    assert new_half_life("Half-Life 2: Episode Three", "")
    for title in ("Half-Life: Alyx - October 26, 2026 Update", "Half-Life 2: Episode Two - Update",
                  "Half-Life 25th Anniversary Update", "Half-Life 2 - Update", "October 26, 2026 Update",
                  "Half-Life: Source", "Half-Life 2: Lost Coast"):
        assert not new_half_life(title, "Fixed a crash in Half-Life: Alyx when loading saves"), title
    assert clean("[h1]Ciao[/h1] [url=https://x.y]link[/url]\n{STEAM_CLAN_IMAGE}/a.png testo") == "Ciao link\ntesto"


def test_news_first_run_records_then_reports(tmp_path):
    cfg, db = make(tmp_path)
    s = FakeSession()
    src = SteamNewsSource(cfg, db, session=s)
    s.posts[546560] = [post(1, "September 25, 2026 Update", tags=["patchnotes"], days_ago=30)]
    s.posts[593110] = [post(10, "Steam Client Update - October 21st", tags=["patchnotes"], days_ago=8)]
    assert src.run() == []                                       # primo giro: solo registrati
    assert s.calls[0]["feeds"] == "steam_community_announcements" and s.calls[0]["maxlength"] == 0

    s.posts[546560].insert(0, post(2, "October 26, 2026 Update", "[list][*]Add Foveated Rendering on desktop[/list]",
                                   tags=["patchnotes"]))
    s.posts[593110] += [post(11, "Steam Client Update - October 28th", tags=["patchnotes"]),     # nota: scartata
                        post(12, "Introducing Steam Frame", "Our new headset"),                # post vero: tenuto
                        post(13, "Steam Summer Sale recap", days_ago=10)]                      # vecchio: senza avviso
    found = src.run()
    texts = {f.text: f for f in found}
    assert set(texts) == {"October 26, 2026 Update", "Introducing Steam Frame"}
    alyx = texts["October 26, 2026 Update"]
    assert alyx.game == "alyx" and alyx.score == 40 and alyx.change_type == "news" and "possible_announcement" not in alyx.keywords
    assert alyx.build.url == "https://store.steampowered.com/news/app/546560/view/2"
    assert "note di aggiornamento" in alyx.context and "Foveated" in alyx.context
    rows = {r["text"] for r in db.conn.execute("SELECT text FROM findings")}
    assert "Steam Summer Sale recap" in rows and "Steam Client Update - October 28th" not in rows
    assert src.run() == []                                       # niente doppioni
    db.close()


def test_news_announcement_and_errors(tmp_path):
    cfg, db = make(tmp_path)
    s = FakeSession()
    src = SteamNewsSource(cfg, db, session=s)
    src.run()
    s.posts[593110] = [post(20, "Half-Life 3", "Coming soon to Steam", tags=["patchnotes"])]   # anche se è una "nota"
    s.broken.add(546560)
    found = src.run()
    assert len(found) == 1 and src.errors and src.errors[0].startswith("alyx")
    f = found[0]
    assert f.score == 200 and "possible_announcement" in f.keywords and f.text.startswith(announcement.TEXT_PREFIX)
    assert announcement.announcement_heading(found) == announcement.HEADING
    db.close()


def test_news_follows_apps_added_by_the_announcement_plan(tmp_path):
    cfg, db = make(tmp_path)
    announcement.add_watch(db, cfg, 9999990, "Half-Life Citadel")
    apps = {a["appid"]: a for a in SteamNewsSource(cfg, db).apps()}
    assert apps[546560]["name"] == "alyx" and apps[546560]["ignore_keywords"] == ["half_life", "hl_universe"]
    assert apps[593110]["title"] == "Client Steam" and not apps[593110]["patchnotes"]
    assert apps[9999990]["auto"]
    s = FakeSession()
    src = SteamNewsSource(cfg, db, session=s)
    src.run()
    s.posts[9999990] = [post(30, "Developer update", "News from the team")]
    found = src.run()
    assert [f.score for f in found] == [120]
    assert cfg.game_title("steamclient") == "Client Steam"
    db.close()


def test_pipeline_runs_news_and_records_health(tmp_path):
    cfg, db = make(tmp_path)
    assert "news" in ALL_SOURCES
    p = Pipeline(cfg, db)
    s = FakeSession()
    p.news.session = s
    p.run_scan(sources=("news",), notify=False)
    s.posts[593110] = [post(40, "Introducing Half-Life 3")]
    result = p.run_scan(sources=("news",), notify=False)
    assert result.status["steam_news"] is None
    assert [f.text for f in result.alerts] == [announcement.TEXT_PREFIX + "Introducing Half-Life 3"]
    db.close()


def test_drill_sends_marks_state_and_reaches_the_app(tmp_path):
    cfg, db = make(tmp_path)
    n = FakeNotifier()
    assert announcement.run_drill(cfg, db, [n])
    heading, texts = n.headings[0]
    assert heading == announcement.DRILL_HEADING and texts == [announcement.DRILL_TEXT]
    # le notifiche del sito saltano il silenzio per "prova" e mettono il titolo per "annuncio"
    assert "prova" in heading.lower() and "annuncio" in heading.lower()
    assert db.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0       # niente nell'archivio
    state = announcement.drill_state(db)
    assert state["ok"] and state["canali"] == ["fake"]

    (tmp_path / "out").mkdir()
    export_bot_data(db, cfg, tmp_path / "out", "https://example/")
    first = json.loads((tmp_path / "out" / "bot" / "ultime.json").read_text(encoding="utf-8"))[0]
    assert first["a"] == 1 and first["p"] == 1 and first["u"] == "https://example/metodologia.html#prova"

    site = build_site(db, cfg, tmp_path / "site")
    assert "prova generale dell'annuncio</a>: 20" in (site / "index.html").read_text(encoding="utf-8")
    assert 'id="prova"' in (site / "metodologia.html").read_text(encoding="utf-8")
    assert "Annunci di Steam" in (site / "metodologia.html").read_text(encoding="utf-8")

    db.set_state(announcement.DRILL_STATE, json.dumps({**state, "at": iso(utcnow() - timedelta(hours=13))}))
    export_bot_data(db, cfg, tmp_path / "out", "https://example/")
    ultime = json.loads((tmp_path / "out" / "bot" / "ultime.json").read_text(encoding="utf-8"))
    assert not any(u.get("p") for u in ultime)                    # dopo 12 ore la prova sparisce dall'app
    db.close()
