"""Il silenzio: giorni senza nomi HLX nuovi, pause di HLX e di Alyx, avviso record e avviso dopo l'annuncio."""
from datetime import date

from hlxwatch import silenzio

SEED = {"announced": "2019-11-21", "released": "2020-03-23", "names": [
    {"name": "hlvr", "day": "2018-01-01"}, {"name": "hlvr_a", "day": "2018-03-01"}, {"name": "hlvr_b", "day": "2019-06-13"},
    {"name": "soundstacks_hlvr", "day": "2020-03-04"}, {"name": "hlalyxpromotitle", "day": "2020-03-12"}]}


def reg(*traces):
    return {"traces": [{"key": k, "label": k, "first": d, "file": f} for k, d, f in traces]}


BASE = reg(("CHLXa", "2026-01-01", "a.txt"), ("CHLXb", "2026-03-01", "a.txt"), ("CHLXc", "2026-04-01", "a.txt"))


class Db:
    def __init__(self):
        self.s = {}

    def get_state(self, key, default=None):
        return self.s.get(key, default)

    def set_state(self, key, value):
        self.s[key] = value


class Rec:
    name = "prova"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True

    def send_groups(self, heading, groups):
        return True


class Cfg:
    def get(self, key, default=None):
        return default


def test_alyx_silence():
    a = silenzio.alyx_silence(SEED)
    assert a["prima_annuncio"] == 161 and a["dopo"] == 104 and a["ultimo"] == "2019-06-13"
    assert a["massima"] == 469 and a["totale"] == 265
    assert silenzio.alyx_silence(None) is None


def test_summary_normal_and_record():
    s = silenzio.summary(BASE, SEED, date(2026, 5, 1))
    assert s["giorni"] == 30 and s["massima"]["giorni"] == 59 and not s["record"] and s["ultime"] == ["CHLXc"]
    s = silenzio.summary(BASE, SEED, date(2026, 6, 15))
    assert s["giorni"] == 75 and s["record"]
    assert silenzio.summary({"traces": []}, SEED) is None
    assert silenzio.summary(reg(("a", "2026-01-01", ""), ("b", "2026-02-01", "")), SEED) is None   # una pausa sola


def test_record_warning_once_per_silence():
    db, n = Db(), Rec()
    assert silenzio.watch(Cfg(), db, [n], None, BASE, SEED, date(2026, 5, 1)) == []
    assert silenzio.watch(Cfg(), db, [n], None, BASE, SEED, date(2026, 6, 15)) == ["HLX Watch: il silenzio più lungo di HLX"]
    assert "75 giorni" in n.sent[0][1] and "161 giorni" in n.sent[0][1]
    assert silenzio.watch(Cfg(), db, [n], None, BASE, SEED, date(2026, 7, 15)) == []          # già avvisato
    more = reg(*[(t["key"], t["first"], t["file"]) for t in BASE["traces"]], ("CHLXd", "2026-07-20", "a.txt"))
    assert silenzio.watch(Cfg(), db, [n], None, more, SEED, date(2026, 7, 25)) == []          # finito il silenzio
    assert silenzio.watch(Cfg(), db, [n], None, more, SEED, date(2027, 1, 1))                 # uno nuovo: si riarma


def test_new_names_after_announcement():
    db, n = Db(), Rec()
    fast = {"avvio": {"annuncio": {"da": "2026-05-10T12:00:00Z", "giorno": 3}}}
    assert silenzio.watch(Cfg(), db, [n], fast, BASE, SEED, date(2026, 5, 12)) == []          # prima: niente di nuovo
    after = reg(*[(t["key"], t["first"], t["file"]) for t in BASE["traces"]],
                ("hlx_store_promo_title", "2026-05-20", "resource/steamvr_english.txt"))
    assert silenzio.watch(Cfg(), db, [n], fast, after, SEED, date(2026, 5, 21)) == ["HLX Watch: nomi nuovi dopo l'annuncio"]
    assert "hlx_store_promo_title" in n.sent[-1][1] and "104 giorni" in n.sent[-1][1] and "promozione" in n.sent[-1][1]
    assert "uscita potrebbe essere vicina" in n.sent[-1][1]
    assert silenzio.watch(Cfg(), db, [n], fast, after, SEED, date(2026, 5, 22)) == []         # una volta sola
    assert silenzio.watch(Cfg(), db, [n], None, after, SEED, date(2026, 5, 22)) == []         # senza modalità: niente
