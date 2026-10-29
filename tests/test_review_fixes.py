"""Test delle correzioni uscite dalla revisione del codice (ottobre 2026)."""
import json
from datetime import datetime, timezone

import yaml

from hlxwatch import timeline, weekly
from hlxwatch.analyzers.codenames import STATE_SEEN, CodenameTracker
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reevaluate import reevaluate
from hlxwatch.sources.archaeology import insert_commits
from hlxwatch.storage import Database
from hlxwatch.timeutil import tz


def make(tmp_path, monkeypatch=None):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw.setdefault("ai", {})["enabled"] = False
    if monkeypatch:
        monkeypatch.setattr(timeline, "SEED", tmp_path / "nessun_seme.json")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def add(db, **kw):
    f = Finding(**{"game": "cs2", "source": "github", "file_path": "a.txt", "change_type": "added",
                   "text": "x", "score": 50, "keywords": ["hlx"], **kw})
    db.add_finding(f, None)
    return f


# --- rivalutazione --------------------------------------------------------------------------------
def test_reevaluate_keeps_non_keyword_findings(tmp_path):
    cfg, db = make(tmp_path)
    add(db, game="steamtracking", source="discovery", change_type="new_repo", keywords=["new_repo"], score=70,
        text="Nuovo repository SteamTracking/GameTracking-Foo: dump", file_path="SteamTracking/GameTracking-Foo")
    add(db, change_type="codename", keywords=["codename_new"], score=120, text="mod != zeta", token="codename:zeta")
    add(db, change_type="added", keywords=["watched_file"], score=60, text='"product" "zeta"',
        file_path="game/core/tools/bugreporter/config.txt")
    add(db, change_type="build", keywords=["steam_build"], score=45, text="Deadlock: nuova build 1", token="build:deadlock:1")
    add(db, change_type="added", keywords=["hlx"], score=100, text="niente di che")        # senza parola chiave: via
    add(db, change_type="added", keywords=["hlx"], score=100, text="hlx_thumper", token="hlx_thumper")
    stats = reevaluate(cfg, db)
    assert stats["eliminate"] == 1 and stats["esaminate"] == 2
    kept = sorted(r[0] for r in db.conn.execute("SELECT change_type FROM findings"))
    assert kept == ["added", "added", "build", "codename", "new_repo"]
    db.close()


# --- nomi di progetto ------------------------------------------------------------------------------
def test_two_trackers_share_state(tmp_path):
    cfg, db = make(tmp_path)
    a, b = CodenameTracker(cfg, db), CodenameTracker(cfg, db)            # commit e scansione completa
    assert a.check("mod != alpha", "x.h", game="cs2", change_type="present") is None
    assert b.check("mod != beta", "y.h", game="dota2", change_type="present") is None
    saved = json.loads(db.get_state(STATE_SEEN))
    assert set(saved) == {"alpha", "beta"}                                # nessuno dei due sovrascrive l'altro
    db.set_state("codenames:learned", "1")
    assert a.check("mod != beta", "z.h", game="cs2") is None             # imparato dall'altro: niente avviso
    assert b.check("mod != alpha", "z.h", game="cs2") is None
    f = a.check("mod != gamma", "z.h", game="cs2")
    assert f is not None and f.token == "codename:gamma"
    assert b.check("mod != gamma", "w.h", game="dota2", change_type="present") is None   # una volta sola
    db.close()


def test_learning_phase_ignores_commit_lines(tmp_path):
    cfg, db = make(tmp_path)
    t = CodenameTracker(cfg, db)
    assert t.check("mod != delta", "x.h", game="cs2", change_type="added") is None    # commit: non imparato
    assert "delta" not in json.loads(db.get_state(STATE_SEEN) or "{}")
    t.finish_learning()
    f = CodenameTracker(cfg, db).check("mod != delta", "x.h", game="cs2")
    assert f is not None                                                            # segnalato dopo l'apprendimento
    db.close()


# --- cronologia --------------------------------------------------------------------------------------
def test_present_topic_is_not_removed(tmp_path, monkeypatch):
    cfg, db = make(tmp_path, monkeypatch)
    text = '// MPropertySuppressExpr = "mod != hlx"'
    b = BuildInfo(game="deadlock", source="github", ref="r1", date=datetime(2026, 3, 14, tzinfo=timezone.utc),
                  url="https://github.com/SteamTracking/GameTracking-Deadlock/commit/r1")
    rid = db.upsert_build(b)
    db.add_finding(Finding(game="deadlock", source="github", file_path="a.h", change_type="removed", text=text,
                           score=110, keywords=["hlx"], token="hlx"), rid)
    db.add_finding(Finding(game="cs2", source="fullscan", file_path="b.h", change_type="present", text=text,
                           score=110, keywords=["hlx"], token="hlx"), None)
    timeline.save_state(db, {"temi": {"t1": {"titolo": "mod != hlx", "spiegazione": "x", "rilevanza": "alta",
                                             "cerca": ""}},
                             "gruppi": {timeline.group_key(text): "t1"}, "tentativi": {}})
    e = timeline.build_entries(cfg, db)["entries"][0]
    assert e["rimosso"] is None                     # c'è ancora nel contenuto attuale di CS2
    db.close()


# --- riepilogo settimanale ---------------------------------------------------------------------------
class FlakyNotifier:
    name = "fake"

    def __init__(self, ok):
        self.ok, self.sent = ok, 0

    def send_text(self, title, text):
        self.sent += 1
        return self.ok


def test_weekly_retries_when_send_fails(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    monkeypatch.setattr(weekly, "now_local", lambda: datetime(2026, 10, 26, 9, 50, tzinfo=tz()))
    n = FlakyNotifier(ok=False)
    weekly.maybe_weekly(cfg, db, [n], "09:05")
    assert n.sent == 1 and db.get_state("weekly:last") is None             # fallito: non segnato
    n.ok = True
    weekly.maybe_weekly(cfg, db, [n], "09:05")
    assert n.sent == 2 and db.get_state("weekly:last") == "2026-10-19"     # riuscito: segnato
    weekly.maybe_weekly(cfg, db, [n], "09:05")
    assert n.sent == 2
    db.close()


def test_weekly_without_notifiers_marks_week(tmp_path, monkeypatch):
    cfg, db = make(tmp_path)
    monkeypatch.setattr(weekly, "now_local", lambda: datetime(2026, 10, 26, 9, 50, tzinfo=tz()))
    weekly.maybe_weekly(cfg, db, [], "09:05")
    assert db.get_state("weekly:last") == "2026-10-19"
    db.close()


# --- archeologia -------------------------------------------------------------------------------------
def test_archaeology_inserts_in_date_order(tmp_path):
    cfg, db = make(tmp_path)
    repos = {r["name"]: r for r in cfg.get("github.repos")}
    cs2 = {"sha": "c1", "date": "2023-03-06T10:00:00+00:00", "subject": "x",
           "changes": [{"path": "a.h", "sign": "+", "text": "mod != hlx"}]}
    dota = {"sha": "d1", "date": "2021-09-16T10:00:00+00:00", "subject": "y",
            "changes": [{"path": "b.h", "sign": "+", "text": "mod != hlx"}]}
    stats = {n: {"commit_con_righe": 0, "commit_gia_noti": 0, "voci": 0, "nuove": 0} for n in ("cs2", "dota2")}
    insert_commits(cfg, db, [(repos["cs2"], cs2), (repos["dota2"], dota)], stats)     # CS2 passato per primo
    rows = {r["game"]: r for r in db.conn.execute("SELECT game, is_new, created_at FROM findings")}
    assert rows["dota2"]["is_new"] == 1 and rows["cs2"]["is_new"] == 0              # la più vecchia è la nuova
    assert rows["dota2"]["created_at"].startswith("2021-09-16")
    assert stats["dota2"]["nuove"] == 1 and stats["cs2"]["nuove"] == 0
    db.close()
