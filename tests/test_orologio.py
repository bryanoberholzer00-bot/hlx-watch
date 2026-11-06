"""L'orologio di Valve: il ritmo ora per ora dal contatore, i confronti e l'ora degli arrivi in Italia."""
import sqlite3
from datetime import datetime, timedelta, timezone

from hlxwatch import contatore as c
from hlxwatch import orologio as o


def env_from(points):
    return c.Envelope(sorted(points))


def office_week(start, weeks=4, day_rate=100, evening_rate=40):
    """Build ogni ora: dalle 9 alle 17 il contatore sale di day_rate all'ora, dalle 18 alle 21 di evening_rate."""
    pts, rev = [], 1000
    for d in range(7 * weeks):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for h in range(9, 22):
            pts.append((day.replace(hour=h), rev, "dota2"))
            rev += day_rate if h < 17 else evening_rate
    return pts


def test_grid_spreads_changes_over_the_hours():
    t = datetime(2026, 1, 5, 10)              # un lunedì
    env = env_from([(t, 100, "x"), (t + timedelta(hours=2), 300, "x")])
    ch, ho = o.grid(env, t - timedelta(days=1), t + timedelta(days=1))
    assert ho[0][10] == 1 and ho[0][11] == 1 and ch[0][10] == 100 and ch[0][11] == 100


def test_long_gaps_are_ignored():
    t = datetime(2026, 1, 5, 10)
    env = env_from([(t, 100, "x"), (t + timedelta(hours=o.GAP_HOURS + 1), 900, "x")])
    ch, ho = o.grid(env, t - timedelta(days=1), t + timedelta(days=1))
    assert sum(map(sum, ho)) == 0


def test_heatmap_day_and_evening_ratio():
    env = env_from(office_week(datetime(2026, 1, 5)))
    h = o.heatmap(env, datetime(2026, 1, 1), datetime(2026, 3, 1))
    assert h["inizio"] == 9 and h["fine"] == 17
    assert h["righe"][5]["celle"][12]["forza"] is None          # sabato: nessuna build, casella vuota
    r = o.ratios(env, datetime(2026, 1, 1), datetime(2026, 3, 1))
    assert round(r["sera"], 2) == 0.4 and r["weekend"] is None and r["pochi_weekend"]


def test_compare():
    base = {"sera": 0.40, "pochi_sera": False}
    assert o.compare({"sera": 0.55, "pochi_sera": False}, base, "sera") == "di più"
    assert o.compare({"sera": 0.25, "pochi_sera": False}, base, "sera") == "di meno"
    assert o.compare({"sera": 0.45, "pochi_sera": False}, base, "sera") == "come prima"
    assert o.compare({"sera": 0.90, "pochi_sera": True}, base, "sera") is None


def test_italian_offset():
    assert o.italian_offset(datetime(2026, 1, 15, 12, tzinfo=timezone.utc)) == 9
    assert o.italian_offset(datetime(2026, 3, 20, 12, tzinfo=timezone.utc)) == 8    # l'America cambia ora prima


class _Db:
    def __init__(self, dates):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE builds (game TEXT, source TEXT, date TEXT)")
        self.conn.executemany("INSERT INTO builds VALUES (?, ?, ?)", dates)


def test_arrivals_half_window_across_midnight():
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    rows = [("cs2", "github", f"2026-10-0{d}T{h:02d}:30:00Z") for d in range(1, 6) for h in (21, 22, 23, 0)]
    rows += [("cs2", "github", "2026-10-01T10:00:00Z"), ("steamclient", "github", "2026-10-01T10:00:00Z"),
             ("cs2", "github", "2020-01-01T10:00:00Z")]
    a = o.arrivals(_Db(rows), now)
    assert a["n"] == 21                       # né il client di Steam né gli aggiornamenti di più di un anno fa
    # 21-01 UTC in ottobre sono 23-03 in Italia (ora legale): la fascia più corta con metà degli arrivi dura 3 ore,
    # e la fine è esclusa ("tra le 0 e le 3")
    assert a["metà_da"] in (23, 0) and (a["metà_a"] - a["metà_da"]) % 24 == 3
