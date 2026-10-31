import json
from datetime import timedelta

import yaml

from hlxwatch import noise
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import Pipeline
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.notifiers.base import group_findings
from hlxwatch.notifiers.telegram import TelegramNotifier
from hlxwatch.reports.site import build_site
from hlxwatch.reports.termometro import history, history_svg
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def add(db, game, text, keywords, path="game/citadel/resource/localization/citadel_english.txt", change="added",
        score=60, days_ago=0):
    b = db.upsert_build(BuildInfo(game=game, source="github", ref=f"sha{text}", date=utcnow() - timedelta(days=days_ago)))
    return db.add_finding(Finding(game=game, source="github", file_path=path, change_type=change, text=text, score=score,
                                  keywords=list(keywords)), b)


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeBot:
    def __init__(self, votes):
        self.votes = votes

    def get(self, url, timeout=None, headers=None):
        return Resp({"voti": self.votes})


def test_vote_mutes_the_same_kind_but_never_hlx(tmp_path):
    cfg, db = make(tmp_path)
    voted = add(db, "deadlock", "Half-Life mention in a tooltip", ["half_life"])
    hlx = add(db, "deadlock", "hlx_cover_helper", ["hlx", "half_life"])
    rules = noise.refresh(cfg, db, session=FakeBot({str(voted.id): "2026-10-31T10:00:00Z", str(hlx.id): "2026-10-31"}))
    assert len(rules) == 1                                         # il voto sulla traccia HLX non conta
    p = Pipeline(cfg, db)
    same = Finding(game="deadlock", source="github", file_path="game/citadel/resource/localization/citadel_italian.txt",
                   change_type="added", text="another Half-Life line", score=60, keywords=["half_life"])
    other_game = Finding(game="cs2", source="github", file_path="game/csgo/resource/csgo_english.txt",
                         change_type="added", text="Half-Life line", score=60, keywords=["half_life"])
    strong = Finding(game="deadlock", source="github", file_path="game/citadel/resource/localization/citadel_english.txt",
                     change_type="added", text="hlx_new", score=120, keywords=["hlx"])
    for f in (same, other_game, strong):
        f.is_new = True
    assert p.select_alerts([same, other_game, strong]) == [other_game, strong]
    # voto annullato: torna ad avvisare
    noise.refresh(cfg, db, session=FakeBot({}))
    assert p.select_alerts([same]) == [same]
    # bot che non risponde: restano le regole di prima
    noise.refresh(cfg, db, session=FakeBot({str(voted.id): "x"}))

    class Down:
        def get(self, *a, **k):
            raise OSError("giù")
    assert noise.refresh(cfg, db, session=Down()) is None and noise.rules(db)
    html = (build_site(db, cfg, tmp_path / "site") / "index.html").read_text(encoding="utf-8")
    assert "silenziati con il pulsante 👎" in html and "Deadlock" in html
    db.close()


def test_telegram_message_has_numbered_noise_buttons(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    a = add(db, "cs2", "valve_hardware_line", ["valve_hardware"], path="a.txt")
    b = add(db, "cs2", "hlx_line", ["hlx"], path="b.txt", score=120)
    sent = []
    import hlxwatch.notifiers.telegram as tg
    monkeypatch.setattr(tg.requests, "post", lambda url, json=None, timeout=None: sent.append(json) or Resp({}, 200))
    TelegramNotifier("t", "1").send_groups("HLX Watch: 2 novità", group_findings([a, b], cfg))
    text, markup = sent[-1]["text"], sent[-1].get("reply_markup")
    assert "1. " in text and "2. " in text
    assert [x["callback_data"] for row in markup["inline_keyboard"] for x in row] == [f"r:{a.id}"]   # HLX: niente 👎
    db.close()


def test_history_and_page(tmp_path):
    cfg, db = make(tmp_path)
    for i in range(6):
        add(db, "cs2", f"CHLXHelper{i}", ["hlx_class"], path="game/bin/win64/server_strings.txt", score=115, days_ago=3)
    h = history(cfg, db, start=(utcnow().date() - timedelta(days=30)).isoformat())
    assert len(h["serie"]) == 31 and h["massimo"] >= 40 and len(h["picchi"]) == 1
    assert h["picchi"][0]["traccia"].startswith("CHLXHelper")
    assert "<polyline" in history_svg(h)
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "termometro.html").read_text(encoding="utf-8")
    assert "Termometro HLX" in page and "<svg" in page and "Deadlock annunciato" in page
    data = json.loads((out / "bot" / "termometro_storico.json").read_text(encoding="utf-8"))
    assert data["serie"][-1]["giorno"] == utcnow().date().isoformat()
    db.close()
