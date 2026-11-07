"""Notte silenziosa: di notte i messaggi Telegram arrivano senza suono, tranne le tracce HLX e i possibili annunci."""
from datetime import datetime, timezone

import hlxwatch.notifiers.telegram as tg
from hlxwatch.models import Finding
from hlxwatch.notifiers import _quiet
from hlxwatch.notifiers.base import AlertGroup
from hlxwatch.notifiers.telegram import TelegramNotifier, is_quiet


class Resp:
    status_code = 200
    text = ""

    def json(self):
        return {}


def at(hour_utc, month=11):
    return datetime(2026, month, 10, hour_utc, 30, tzinfo=timezone.utc)


def test_is_quiet_across_midnight():
    q = (23, 8)
    assert is_quiet(q, at(22))          # 23:30 in Italia (UTC+1)
    assert is_quiet(q, at(3))           # 4:30
    assert not is_quiet(q, at(7))       # 8:30
    assert not is_quiet(q, at(21))      # 22:30
    assert is_quiet(q, at(21, month=7))  # d'estate (UTC+2) 23:30
    assert not is_quiet(None, at(3))
    assert is_quiet((1, 6), at(2)) and not is_quiet((1, 6), at(12))


def test_quiet_config():
    class Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, key, default=None):
            return default if self.v == "manca" else self.v
    assert _quiet(Cfg("manca")) == (23, 8)
    assert _quiet(Cfg([22, 7])) == (22, 7)
    assert _quiet(Cfg(False)) is None and _quiet(Cfg([])) is None and _quiet(Cfg([8, 8])) is None


def finding(keywords, fid=1):
    return Finding(game="cs2", source="github", file_path="a.txt", change_type="added", text="x", score=80,
                   keywords=keywords, id=fid)


def test_silent_at_night_except_hlx(monkeypatch):
    sent = []
    monkeypatch.setattr(tg.requests, "post", lambda url, json=None, timeout=None: sent.append(json) or Resp())
    monkeypatch.setattr(tg, "is_quiet", lambda quiet, now=None: True)
    n = TelegramNotifier("t", "1", (23, 8))
    n.send_text("HLX Watch: prova", "riga")
    assert sent[-1]["disable_notification"] is True
    n.send_groups("novità", [AlertGroup("cs2", "Counter-Strike 2", [finding(["valve_hardware"])], 80, 1)])
    assert sent[-1]["disable_notification"] is True
    n.send_groups("novità", [AlertGroup("cs2", "Counter-Strike 2", [finding(["valve_hardware"]), finding(["hlx"], 2)], 120, 2)])
    assert "disable_notification" not in sent[-1]
    n.send_text("HLX Watch: dopo", "riga")              # il suono torna spento per i messaggi dopo
    assert sent[-1]["disable_notification"] is True


def test_loud_by_day(monkeypatch):
    sent = []
    monkeypatch.setattr(tg.requests, "post", lambda url, json=None, timeout=None: sent.append(json) or Resp())
    monkeypatch.setattr(tg, "is_quiet", lambda quiet, now=None: False)
    TelegramNotifier("t", "1", (23, 8)).send_text("HLX Watch: prova", "riga")
    assert "disable_notification" not in sent[-1]
