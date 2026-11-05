import json
from datetime import timedelta

import yaml

from hlxwatch.build_notes import PENDING, BuildNotes, describe, find_commit
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.notifiers.base import finding_line
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow

LANG = "game/hlvr/pak01_dir/panorama/localization/hlvr_{}.txt"
PAK_PATCH = ("@@ -1,3 +1,3 @@\n-panorama/localization/hlvr_french.txt CRC:001 size:1\n"
             "+panorama/localization/hlvr_french.txt CRC:002 size:2\n"
             "-maps/a1.vpk CRC:003 size:3\n+maps/a1.vpk CRC:004 size:4\n+maps/hlx_nuova.vpk CRC:005 size:5\n"
             "-maps/vecchia.vpk CRC:006 size:6")


def test_translations_only():
    files = [{"filename": LANG.format(lang), "additions": 98} for lang in ("french", "italian", "german")]
    files += [{"filename": "game/hlvr/pak01_dir.txt", "patch": PAK_PATCH.split("\n-maps/a1")[0]},
              {"filename": "manifests/manifest_546561.txt"}, {"filename": "files.json"}]
    assert describe(files) == ["solo traduzioni (3 lingue)", "elenchi dei pacchetti di download"]


def test_english_new_files_and_programs():
    files = [{"filename": LANG.format("english"), "additions": 1}, {"filename": LANG.format("french"), "additions": 1},
             {"filename": "game/hlvr/pak01_dir.txt", "patch": PAK_PATCH},
             {"filename": "game/bin/win64/server_strings.txt"}, {"filename": "Protobufs/net.proto"},
             {"filename": "game/hlvr/steam.inf"}, {"filename": "game/core/tools.fgd"}]
    assert describe(files) == ["testi nuovi in inglese (+1 riga) e in 1 altra lingua",
                               "file del gioco: 1 nuovo (hlx_nuova.vpk), 1 tolto, 1 modificato",
                               "programmi del gioco (1 file)", "messaggi di rete (1 file Protobuf)",
                               "altri file: tools.fgd", "numero di versione"]


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def steam_build(db, game="alyx", buildid="26446291", branch="public", appid=546560, when=None):
    when = when or utcnow()
    build = BuildInfo(game=game, source="steam", ref=f"{branch}:{buildid}", date=when,
                      url=f"https://steamdb.info/patchnotes/{buildid}/", steam_buildid=buildid)
    bid = db.upsert_build(build)
    finding = Finding(game=game, source="steam_pics", file_path=f"depots/branches/{branch}", change_type="build",
                      text=f"nuova build {buildid}", score=55, keywords=["steam_build"],
                      token=f"build:{appid}:{branch}:{buildid}")
    finding.build = build
    db.add_finding(finding, bid)
    return finding


def tracking_commit(db, game="alyx", number="26446291", when=None, sha="abc1234", hlx=False):
    build = BuildInfo(game=game, source="github", ref=sha, date=when or utcnow(),
                      url=f"https://github.com/SteamTracking/GameTracking-X/commit/{sha}",
                      message=f"{number} | 3 files | M\tgame/x.txt")
    bid = db.upsert_build(build)
    if hlx:
        db.add_finding(Finding(game=game, source="github", file_path="game/x.txt", change_type="added",
                               text="hlx_nuovo", score=130, keywords=["hlx"]), bid)
    return bid


def test_find_commit_by_number_or_by_time_on_public(tmp_path):
    _, db = make(tmp_path)
    alyx = steam_build(db)
    tracking_commit(db)
    assert find_commit(db, alyx)["ref"] == "abc1234"
    now = utcnow()
    cs2 = steam_build(db, game="cs2", buildid="26445667", appid=730, when=now)
    tracking_commit(db, game="cs2", number="2000941", when=now - timedelta(hours=2), sha="vecchio")
    tracking_commit(db, game="cs2", number="2000942", when=now + timedelta(minutes=3), sha="giusto")
    assert find_commit(db, cs2)["ref"] == "giusto"
    beta = steam_build(db, game="cs2", buildid="26445999", branch="beta", appid=730, when=now)
    assert find_commit(db, beta) is None
    db.close()


class Client:
    def __init__(self):
        self.calls = 0

    def commit_detail(self, repo, sha, max_pages):
        self.calls += 1
        return {"files": [{"filename": LANG.format("italian"), "additions": 9}]}


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    headers: dict = {}

    def __init__(self, items):
        self.items = items

    def get(self, url, params=None, timeout=None):
        return Resp({"appnews": {"newsitems": self.items}})


class Fake:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def test_annotate_adds_the_note_with_official_notes(tmp_path):
    cfg, db = make(tmp_path)
    finding = steam_build(db)
    tracking_commit(db, hlx=True)
    old = int((utcnow() - timedelta(days=10)).timestamp())
    news = Session([{"date": old, "title": "October 26, 2026 Update"},
                    {"date": old - 86400 * 30, "title": "troppo vecchia"}])
    BuildNotes(cfg, db, Client(), news).annotate([finding])
    assert finding.note.startswith("Cosa cambia: solo traduzioni (1 lingua); 1 riga da avviso, elencata a parte")
    assert "Note ufficiali di Valve: «October 26, 2026 Update»" in finding.note
    line = finding_line(finding, "html")
    assert "\n    ↳ <i>Cosa cambia: solo traduzioni" in line and "«October" in line
    db.close()


def test_late_commit_gets_a_follow_up_message(tmp_path):
    cfg, db = make(tmp_path)
    finding = steam_build(db)
    notes = BuildNotes(cfg, db, Client(), Session([]))
    notes.annotate([finding])
    assert finding.note == "" and json.loads(db.get_state(PENDING))[0]["id"] == finding.id
    fake = Fake()
    assert notes.followups([fake]) == 0 and fake.sent == []          # il commit non c'è ancora: si aspetta
    tracking_commit(db)
    assert notes.followups([fake]) == 1
    title, text = fake.sent[0]
    assert title.startswith("HLX Watch: build 26446291 di ") and "solo traduzioni" in text
    assert "nessuna negli ultimi 14 giorni" in text
    assert json.loads(db.get_state(PENDING)) == []
    db.close()


def test_waiting_stops_after_a_day(tmp_path):
    cfg, db = make(tmp_path)
    finding = steam_build(db)
    db.set_state(PENDING, json.dumps([{"id": finding.id, "at": iso(utcnow() - timedelta(hours=25))}]))
    fake = Fake()
    assert BuildNotes(cfg, db, Client(), Session([])).followups([fake]) == 0
    assert fake.sent == [] and json.loads(db.get_state(PENDING)) == []
    db.close()


def test_other_alerts_are_untouched(tmp_path):
    cfg, db = make(tmp_path)
    client = Client()
    hlx = Finding(game="cs2", source="github", file_path="a.txt", change_type="added", text="hlx_x", score=130,
                  keywords=["hlx"])
    BuildNotes(cfg, db, client, Session([])).annotate([hlx])
    assert hlx.note == "" and client.calls == 0
    assert "↳" not in finding_line(hlx, "html")
    db.close()
