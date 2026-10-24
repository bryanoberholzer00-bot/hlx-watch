from datetime import timedelta

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.health import Health
from hlxwatch.models import BuildInfo
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


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
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    notifier = FakeNotifier()
    return cfg, db, notifier, Health(cfg, db, [notifier])


def test_alert_after_threshold_and_recovery(tmp_path):
    cfg, db, n, h = make(tmp_path)
    h.record("steam_pics", False, "timeout")
    h.record("steam_pics", False, "timeout")
    assert n.sent == []                                     # sotto soglia: silenzio
    h.record("steam_pics", False, "login anonimo fallito")
    assert len(n.sent) == 1 and "qualcosa non va" in n.sent[0][0]
    assert "Steam PICS" in n.sent[0][1] and "3 run" in n.sent[0][1] and "login anonimo fallito" in n.sent[0][1]
    h.record("steam_pics", False, "ancora")
    assert len(n.sent) == 1                                 # nessun doppione finché resta rotto
    assert h.summary()["steam_pics"]["ok"] is False
    h.record("steam_pics", True)
    assert len(n.sent) == 2 and "risolto" in n.sent[1][0]
    h.record("steam_pics", True)
    assert len(n.sent) == 2                                 # tutto ok: niente messaggi
    db.close()


def test_success_before_threshold_resets(tmp_path):
    cfg, db, n, h = make(tmp_path)
    for _ in range(2):
        h.record("github", False, "x")
    h.record("github", True)
    for _ in range(2):
        h.record("github", False, "x")
    assert n.sent == []                                     # mai 3 di fila
    db.close()


def test_immediate_alert(tmp_path):
    cfg, db, n, h = make(tmp_path)
    h.record("ai", False, "BadRequestError: Your credit balance is too low", immediate=True)
    assert len(n.sent) == 1 and "analisi AI" in n.sent[0][1]
    db.close()


def test_upstream_stale_and_back(tmp_path):
    cfg, db, n, h = make(tmp_path)
    db.upsert_build(BuildInfo(game="cs2", source="github", ref="old", date=utcnow() - timedelta(days=9)))
    h.check_upstream()
    assert len(n.sent) == 1 and "fermi" in n.sent[0][0]
    h.check_upstream()
    assert len(n.sent) == 1
    db.upsert_build(BuildInfo(game="cs2", source="github", ref="new", date=utcnow()))
    h.check_upstream()
    assert len(n.sent) == 2 and "ripartiti" in n.sent[1][0]
    db.close()


def test_disabled(tmp_path):
    cfg, db, n, h = make(tmp_path)
    cfg.raw["health"]["enabled"] = False
    for _ in range(5):
        h.record("github", False, "x")
    assert n.sent == []
    db.close()
