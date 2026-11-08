"""La serata di un evento: le date in serate.json, la fascia in cima alle pagine e la sezione del registro."""
from datetime import date, datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from hlxwatch import previsioni, serata

S = [{"id": "prova", "nome": "The Game Awards 2026", "luogo": "Peacock Theater, Los Angeles",
      "inizio": "2026-12-10T17:00:00Z", "spettacolo": "2026-12-11T00:30:00Z", "fine": "2026-12-11T08:00:00Z",
      "previsione": "gr4", "fonte": "https://example/tga"}]
TEMPLATES = Path(serata.__file__).parent / "reports" / "site_templates"


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_the_real_file_is_valid_and_its_prediction_exists():
    real = serata.load()
    assert real and all(s["inizio"] < s["spettacolo"] < s["fine"] for s in real)
    ids = {v["id"] for v in previsioni.load()["voci"]}
    assert all(s.get("previsione") in ids for s in real if s.get("previsione"))
    c = serata.current(at("2026-11-08T12:00:00"))
    assert c["id"] == "tga-2026" and c["spettacolo_locale"] == "venerdì 11 dicembre alle 1:30"
    assert c["giorno_evento"] == "giovedì 10 dicembre" and c["inizio_locale"] == "giovedì 10 dicembre alle 18:00"


def test_before_during_after():
    far = serata.current(at("2026-11-08T12:00:00"), S)
    assert far["stato"] == "prima" and far["giorni"] == 33 and not far["fascia"]
    week = serata.current(at("2026-12-04T12:00:00"), S)
    assert week["giorni"] == 7 and week["fascia"]
    assert serata.current(at("2026-12-11T01:00:00"), S)["stato"] == "in_corso"
    assert serata.current(at("2026-12-11T10:00:00"), S)["stato"] == "dopo"
    assert serata.current(at("2026-12-14T10:00:00"), S) is None


def _env(**glob):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    env.globals.update({"generated": "oggi", "annuncio": None, **glob})
    return env


def test_banner_on_every_page_only_in_the_last_week():
    page = lambda when, **g: _env(serata=serata.current(at(when), S), **g).get_template("previsioni.html").render(
        p=previsioni.summary(None, previsioni.load(), date(2026, 11, 8)))
    assert 'class="fascia-serata' not in page("2026-11-08T12:00:00")
    week = page("2026-12-04T12:00:00")
    assert "fascia-serata prima" in week and "mancano 7 giorni" in week and "venerdì 11 dicembre alle 1:30" in week
    assert "stanotte alle 1:30" in page("2026-12-10T12:00:00")
    assert "In corso: The Game Awards 2026" in page("2026-12-11T01:00:00")
    assert "la serata è finita" in page("2026-12-11T10:00:00")
    # con un possibile annuncio resta solo la fascia rossa
    red = page("2026-12-11T01:00:00", annuncio={"giorno": 1, "voci": [{"x": "Half-Life 3"}]})
    assert 'class="fascia-serata' not in red and "fascia-annuncio" in red


def test_section_in_the_register():
    html = _env(serata=serata.current(at("2026-11-08T12:00:00"), S)).get_template("previsioni.html").render(
        p=previsioni.summary(None, previsioni.load(), date(2026, 11, 8)))
    assert 'id="serata"' in html and "mancano 33 giorni" in html and "ogni 10 minuti" in html
    assert 'href="#gr4"' in html                                 # la previsione che si decide quella notte
    after = _env(serata=serata.current(at("2026-12-11T10:00:00"), S), ).get_template("previsioni.html").render(
        p=previsioni.summary(None, previsioni.load(), date(2026, 12, 11)))
    assert "La serata è finita." in after
