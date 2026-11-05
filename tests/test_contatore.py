"""Contatore di Valve: SourceRevision dei steam.inf, ritmo, fasce orarie, lavoro visibile e avviso sulle ondate."""
import json
from datetime import datetime, timedelta

import yaml

from hlxwatch import contatore as c
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.storage import Database

INF = """ClientVersion=2000942
PatchVersion=1.41.9.6
ProductName=cs2
appID=730
SourceRevision=11206718
VersionDate=Nov 04 2026
VersionTime=16:01:59
"""


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


class FakeClient:
    def __init__(self, inf=INF, counts=None):
        self.inf, self.counts, self.files = inf, counts or {}, []

    def raw_file(self, repo, sha, path, max_bytes):
        self.files.append((repo, sha, path))
        return self.inf if path == "game/csgo/steam.inf" else ""

    def count_commits(self, repo, since, until):
        return self.counts.get(repo, 10)


def fill(db, start, days, per_day, step_hours=6):
    """Build ogni step_hours ore con un ritmo costante; restituisce l'ultimo numero."""
    rev, t = 1_000_000, start
    while t < start + timedelta(days=days):
        rev += int(per_day * step_hours / 24)
        c.add_point(db, "cs2", rev, t.strftime("%Y-%m-%dT%H:%M:%S"))
        t += timedelta(hours=step_hours)
    return rev


def test_parse_inf():
    assert c.parse_inf(INF) == (11206718, "2026-11-04T16:01:59")
    assert c.parse_inf("SourceRevision=abc\nVersionDate=Nov 04 2026\nVersionTime=16:01:59") is None
    assert c.parse_inf("SourceRevision=5\nVersionDate=?\n") is None
    assert c.num(11209448) == "11.209.448" and c.pct(1.58) == "+58%" and c.pct(0.9) == "-10%" and c.pct(None) == "—"


def test_seed_loads_once_and_history_is_one_counter(tmp_path):
    cfg, db = make(tmp_path)
    assert c.load_seed(db) > 4000 and c.load_seed(db) == 0
    pts = c.points(db)
    env = c.Envelope(pts)
    below = sum(1 for (t, rev, g), top in zip(pts, [max(p[1] for p in pts[:i + 1]) for i in range(len(pts))]) if rev < top)
    assert below < len(pts) * 0.02                       # un contatore solo: quasi sempre in salita
    assert env.rate(datetime(2017, 1, 1), datetime(2018, 1, 1)) < env.rate(datetime(2025, 1, 1), datetime(2026, 1, 1))
    db.close()


def test_rates_buckets_and_waves(tmp_path):
    cfg, db = make(tmp_path)
    start = datetime(2025, 1, 6)
    fill(db, start, 420, 2000, step_hours=3)
    # ultimi 60 giorni: il doppio, solo nelle ore di lavoro (build alle 10, 13, 16 dei giorni feriali)
    rev = db.conn.execute("SELECT MAX(rev) FROM valve_counter").fetchone()[0]
    d = start + timedelta(days=420)
    while d < start + timedelta(days=480):
        for h in (10, 13, 16):
            if d.weekday() < 5:
                rev += 1500
                c.add_point(db, "cs2", rev, d.replace(hour=h).strftime("%Y-%m-%dT%H:%M:%S"))
        d += timedelta(days=1)
    a = c.analyse(db)
    assert a["rapporto"] > 1.3 and a["record"]
    assert a["fasce"]["lavoro"]["rapporto"] > 1.5 and not a["fasce"]["lavoro"]["pochi"]
    assert a["fasce"]["weekend"]["pochi"]                # nel fine settimana nessuna build: pochi dati
    assert a["ondate"] and a["ondate"][0]["rapporto"] > 1.3
    assert a["mesi"] and all(m["mese"] >= "2017-01" for m in a["mesi"])
    text = c.wave_text(a, "https://x/")
    assert "ore di lavoro" in text and "pochi dati" in text and text.endswith("https://x/contatore.html")
    db.close()


def test_update_reads_steam_inf_and_visible_work(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient(counts={"SteamTracking/GameTracking-Deadlock": 90})
    assert c.update(cfg, db, client) == 1 and c.update(cfg, db, client) == 0      # stessa build: niente di nuovo
    assert ("SteamTracking/GameTracking-CS2", "master", "game/csgo/steam.inf") in client.files
    v = c.visible_work(db, client)
    assert len(v["repos"]) == 6 and v["totale"]["ora"] == 140 and v["rispetto_anno"] == 1.0
    assert c.visible_work(db, FakeClient(counts={"x": 1})) == v                    # una volta al giorno
    db.close()


def test_wave_alert_once_then_calm(tmp_path):
    cfg, db = make(tmp_path)
    n = FakeNotifier()
    now = datetime.now()
    a = {"rapporto": 1.5, "rapporto_anno": 1.5, "ora": 4000, "base": 2600, "record": True,
         "ultimo": {"quando": now.isoformat(timespec="minutes")}, "finestra": {"a": now.date().isoformat()},
         "fasce": {k: {"rapporto": 1.4, "pochi": k != "lavoro"} for k in c.BUCKETS}, "visibile": {"rispetto_anno": 1.2}}
    assert c.check_wave(db, a, [n], "https://x/") == "inizio"
    assert c.check_wave(db, a, [n], "https://x/") is None and len(n.sent) == 1
    assert n.sent[0][0] == c.TITLE_WAVE and "lavoro che non si vede" in n.sent[0][1]
    assert c.check_wave(db, dict(a, rapporto=1.2), [n], "https://x/") is None      # tra 15% e 30%: resta attiva
    assert c.check_wave(db, dict(a, rapporto=1.05), [n], "https://x/") == "fine" and n.sent[-1][0] == c.TITLE_CALM
    old = dict(a, ultimo={"quando": (now - timedelta(days=30)).isoformat(timespec="minutes")})
    assert c.check_wave(db, old, [n], "https://x/") is None                        # dati vecchi: niente avviso
    db.close()


def test_run_without_send_keeps_wave_state(tmp_path):
    cfg, db = make(tmp_path)
    a = c.run(cfg, db, [FakeNotifier()], client=FakeClient(), send=False)
    assert a and a["n_build"] > 4000 and db.get_state(c.STATE_WAVE) is None
    db.close()


def test_site_page(tmp_path):
    from hlxwatch.reports.site import build_site
    from hlxwatch.site_check import check_site
    cfg, db = make(tmp_path)
    c.load_seed(db)
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "contatore.html").read_text(encoding="utf-8")
    assert "Il contatore di Valve" in page and "<svg" in page and "annuncio di Half-Life: Alyx" in page
    assert "Le ondate più forti" in page and 'href="contatore.html"' in (out / "index.html").read_text(encoding="utf-8")
    data = json.loads((out / "bot" / "contatore.json").read_text(encoding="utf-8"))
    assert data["ultimo"]["rev"] > 11_000_000 and data["mesi"]
    assert not [p for p in check_site(out, need_preview=False) if "contatore" in p["testo"]]
    db.close()
