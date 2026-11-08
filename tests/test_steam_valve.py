"""La pagina di Valve su Steam: prodotti contati e mostrati, avvisi per prodotti nascosti, nuovi o in aumento."""
import json
from datetime import datetime, timedelta, timezone

from hlxwatch.sources import steam_valve as sv


def snap(all_n, all_ids, up_n, up_ids):
    return {"at": "2026-11-08T10:00:00+00:00", "tutti": {"dichiarati": all_n, "app": all_ids},
            "arrivo": {"dichiarati": up_n, "app": up_ids}}


def test_first_reading_is_silent():
    assert sv.changes(None, snap(5, ["1", "2", "3", "4"], 1, ["9"])) == []


def test_hidden_product_like_november_2025():
    old = snap(54, [str(i) for i in range(54)], 4, ["a", "b", "c", "d"])
    new = snap(54, [str(i) for i in range(54)], 5, ["a", "b", "c", "d"])
    kinds = [c["tipo"] for c in sv.changes(old, new)]
    assert kinds == ["nascosto", "sale"]
    assert "conta 5 prodotti in arrivo ma ne mostra 4" in sv.changes(old, new)[0]["testo"]
    assert sv.changes(new, new) == []                                   # lo stesso nascosto: niente di nuovo


def test_new_app_once_even_if_in_both_lists():
    old = snap(2, ["1", "2"], 0, [])
    new = snap(3, ["1", "2", "77"], 1, ["77"])
    c = sv.changes(old, new)
    assert [(x["tipo"], x.get("appid")) for x in c] == [("nuova", "77"), ("sale", None)]


class _Resp:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data


class _Session:
    def __init__(self):
        self.up = ["1422450"]

    def get(self, url, params=None, headers=None, timeout=None):
        if "appdetails" in url:
            return _Resp({params["appids"]: {"data": {"name": "Half-Life X", "type": "game"}}})
        ids = self.up if params.get("sort") == "upcoming" else ["730", "570"] + self.up
        html = "".join(f'<a data-ds-appid=\\"{i}\\">' for i in ids)
        return _Resp({"success": 1, "total_count": len(ids), "results_html": html})


class _Db:
    def __init__(self):
        self.s = {}

    def get_state(self, k, d=None):
        return self.s.get(k, d)

    def set_state(self, k, v):
        self.s[k] = v


class _Rec:
    name = "prova"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append(text)
        return True

    def send_groups(self, h, g):
        return True


def test_check_every_6_hours_and_alert_with_name():
    db, n, s = _Db(), _Rec(), _Session()
    t0 = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
    assert sv.check(None, db, [n], s, t0) == [] and n.sent == []
    assert sv.check(None, db, [n], s, t0 + timedelta(hours=2)) is None   # troppo presto
    s.up = ["1422450", "999"]
    found = sv.check(None, db, [n], s, t0 + timedelta(hours=7))
    assert [c["tipo"] for c in found] == ["nuova", "sale"]
    assert "Half-Life X (game)" in n.sent[0] and "store.steampowered.com/app/999" in n.sent[0]
    summ = sv.summary(db)
    assert summ["arrivo"] == 2 and summ["tutti"] == 4 and len(summ["storia"]) == 2
    assert json.loads(db.s[sv.STATE])["arrivo"]["app"] == ["1422450", "999"]
