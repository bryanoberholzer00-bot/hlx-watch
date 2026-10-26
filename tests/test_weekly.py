from datetime import date, datetime

import yaml

from hlxwatch import weekly
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo
from hlxwatch.storage import Database
from hlxwatch.timeutil import tz


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw.setdefault("ai", {})["enabled"] = False          # niente chiamate a Claude nei test
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_last_week_and_label():
    assert weekly.last_week(date(2026, 10, 26)) == (date(2026, 10, 19), date(2026, 10, 25))     # lunedì
    assert weekly.last_week(date(2026, 10, 28)) == (date(2026, 10, 19), date(2026, 10, 25))     # mercoledì
    assert weekly.week_label(date(2026, 10, 19), date(2026, 10, 25)) == "19–25 ottobre"
    assert weekly.week_label(date(2026, 9, 28), date(2026, 10, 4)) == "28 settembre – 4 ottobre"


def test_sent_once_after_report_time(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    db.upsert_build(BuildInfo(game="dota2", source="github", ref="a",
                              date=datetime(2026, 10, 23, 18, 0, tzinfo=tz())))
    n = FakeNotifier()
    monkeypatch.setattr(weekly, "now_local", lambda: datetime(2026, 10, 26, 8, 30, tzinfo=tz()))
    assert weekly.maybe_weekly(cfg, db, [n], "09:05") is None and n.sent == []   # lunedì prima delle 9:05
    monkeypatch.setattr(weekly, "now_local", lambda: datetime(2026, 10, 26, 9, 50, tzinfo=tz()))
    text = weekly.maybe_weekly(cfg, db, [n], "09:05")
    assert len(n.sent) == 1 and n.sent[0][0] == "HLX Watch: riepilogo della settimana 19–25 ottobre"
    assert "Settimana tranquilla" in text and "• Dota 2: 1 commit" in text
    assert "search.html?since=2026-10-19&until=2026-10-25&min=50" in text
    assert weekly.maybe_weekly(cfg, db, [n], "09:05") is None and len(n.sent) == 1   # niente doppioni
    # se il lunedì GitHub non fa nessun controllo, parte al primo giro successivo
    monkeypatch.setattr(weekly, "now_local", lambda: datetime(2026, 11, 3, 2, 0, tzinfo=tz()))
    assert weekly.maybe_weekly(cfg, db, [n], "09:05") is not None and len(n.sent) == 2
    db.close()


def test_text_with_ai():
    data = {"per_gioco": [{"gioco": "Deadlock", "commit": 9, "build": 14}], "voci_rilevanti": 3,
            "alta_rilevanza": 1, "stringhe_mai_viste": 2}
    ai = {"sintesi": "Settimana di aggiornamenti per Deadlock.", "punti": ["Nuova proprietà mod != hlx"],
          "da_osservare": ["Le particelle GPU"], "novita_hlx": True}
    text = weekly.weekly_text(data, ai, date(2026, 10, 19), date(2026, 10, 25), "https://s/")
    assert text.startswith("Settimana di aggiornamenti per Deadlock.")
    assert "• Deadlock: 9 commit, 14 build" in text and "Punti principali:" in text and "Da tenere d'occhio:" in text
