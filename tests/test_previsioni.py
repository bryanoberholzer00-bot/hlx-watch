"""Il registro delle previsioni: esiti scritti a mano, scadenze che si decidono da sole, tracce HLX che corrispondono."""
from datetime import date, datetime, timezone

from hlxwatch import previsioni as pv
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database


def data(**extra):
    d = {"annuncio_hlx": None,
         "video": [{"id": "v1", "autore": "Tyler", "titolo": "HLX Files", "data": "2026-10-22", "url": "https://y/1"},
                   {"id": "v2", "autore": "Gabe", "titolo": "HLX News", "data": "2026-02-01", "url": "https://y/2"}],
         "voci": [{"id": "a", "video": "v1", "tipo": "previsione", "esito": "in_attesa", "testo": "Niente annuncio nel 2026",
                   "regola": "niente_annuncio_entro", "scadenza": "2026-12-31"},
                  {"id": "b", "video": "v1", "tipo": "fatto", "esito": "confermata", "testo": "Ritmo record"},
                  {"id": "c", "video": "v2", "tipo": "fatto", "esito": "non_trovata", "testo": "Temperatura",
                   "segnale": "(?i)temperature"},
                  {"id": "d", "video": "v2", "tipo": "fatto", "esito": "in_parte", "testo": "Ranger di ottobre"}]}
    d.update(extra)
    return d


class _Cfg:
    def get(self, key, default=None):
        return default


class _N:
    def __init__(self):
        self.sent = []


def _db(tmp_path):
    return Database(tmp_path / "db.sqlite")


def _trace(db, text, when, keywords=("hlx_prefix",), change="added"):
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref=text[:8].ljust(40, "0"), date=when))
    db.add_finding(Finding(game="cs2", source="github", file_path="game/core/x.txt", change_type=change, text=text,
                           token=text, score=110, keywords=list(keywords)), b, created_at=when)


def test_the_real_file_is_valid():
    d = pv.load()
    videos = {v["id"] for v in d["video"]}
    assert d["voci"] and all(v["video"] in videos for v in d["voci"])
    assert all(v["esito"] in pv.ESITI for v in d["voci"])
    assert len({v["id"] for v in d["voci"]}) == len(d["voci"])
    s = pv.summary(None, d, date(2026, 11, 8))
    assert s["n"] == len(d["voci"]) and {a["autore"] for a in s["autori"]} >= {"Tyler McVicker", "Gabe Follower"}


def test_deadline_waits_then_confirms_or_is_denied():
    before = {v["id"]: v for v in pv.evaluate(data(), None, date(2026, 11, 8))}
    assert before["a"]["esito"] == "in_attesa" and before["a"]["giorni"] == 53
    after = {v["id"]: v for v in pv.evaluate(data(), None, date(2027, 1, 1))}
    assert after["a"]["esito"] == "confermata" and "passata senza annuncio" in after["a"]["motivo"]
    denied = {v["id"]: v for v in pv.evaluate(data(annuncio_hlx="2026-12-10"), None, date(2027, 1, 1))}
    assert denied["a"]["esito"] == "smentita"
    late = {v["id"]: v for v in pv.evaluate(data(annuncio_hlx="2027-03-01"), None, date(2027, 3, 2))}
    assert late["a"]["esito"] == "confermata"                     # annunciato, ma dopo la scadenza


def test_signal_only_counts_hlx_traces_after_the_video(tmp_path):
    db = _db(tmp_path)
    _trace(db, "hlx_temperature_old", datetime(2026, 1, 10, tzinfo=timezone.utc))                 # prima del video
    _trace(db, "particle_temperature", datetime(2026, 5, 1, tzinfo=timezone.utc), keywords=("weather",))  # non HLX
    assert {v["id"]: v for v in pv.evaluate(data(), db, date(2026, 11, 8))}["c"]["esito"] == "non_trovata"
    _trace(db, "hlx_surface_temperature", datetime(2026, 6, 3, tzinfo=timezone.utc))
    c = {v["id"]: v for v in pv.evaluate(data(), db, date(2026, 11, 8))}["c"]
    assert c["esito"] == "da_rivedere" and c["tracce"][0]["giorno"] == "2026-06-03" and "03/06/2026" in c["motivo"]


def test_tally_per_author():
    s = pv.summary(None, data(), date(2026, 11, 8))
    by = {a["autore"]: a for a in s["autori"]}
    assert by["Tyler"]["quota"] == 100 and by["Tyler"]["controllate"] == 1       # l'attesa non conta
    assert by["Gabe"]["quota"] == 25 and by["Gabe"]["controllate"] == 2          # in parte a metà, non trovata a zero
    assert [v["id"] for v in s["video"][0]["voci"]] == ["a", "b"]                # il video più recente per primo
    assert s["attesa"][0]["id"] == "a"


def test_watch_learns_then_alerts_only_on_automatic_changes(tmp_path, monkeypatch):
    db = _db(tmp_path)
    sent = []
    monkeypatch.setattr("hlxwatch.notifiers.notify_text", lambda n, title, text: sent.append((title, text)) or True)
    assert pv.watch(_Cfg(), db, [_N()], data(), date(2026, 11, 8)) == [] and sent == []    # primo giro: impara
    assert pv.watch(_Cfg(), db, [_N()], data(), date(2026, 11, 9)) == []
    changed = pv.watch(_Cfg(), db, [_N()], data(), date(2027, 1, 1))
    assert [v["id"] for v in changed] == ["a"] and "previsione è cambiata" in sent[0][0] and "confermata" in sent[0][1]
    # un esito cambiato a mano nel file non manda avvisi
    d = data()
    d["voci"][1]["esito"] = "smentita"
    assert pv.watch(_Cfg(), db, [_N()], d, date(2027, 1, 2)) == []


def test_page_renders(tmp_path):
    from jinja2 import Environment, FileSystemLoader
    from hlxwatch.reports import site
    env = Environment(loader=FileSystemLoader(str(site.TEMPLATES if hasattr(site, "TEMPLATES") else
                                                  (site.Path(site.__file__).with_name("site_templates")))))
    env.globals.update(generated="oggi", annuncio=None)
    html = env.get_template("previsioni.html").render(p=pv.summary(None, pv.load(), date(2026, 11, 8)))
    assert "Il registro delle previsioni" in html and "Chi ci prende" in html and "Tyler McVicker" in html
    assert "mancano" in html
