"""Fatti esterni della Cronologia: scritti a mano, con le fonti, segnati a parte."""
import json

from hlxwatch import timeline


def test_real_file_has_sources_for_every_fact():
    facts = json.loads(timeline.EXTERNAL.read_text(encoding="utf-8"))["fatti"]
    assert facts and all(f["fonti"] and all(s["url"].startswith("https://") for s in f["fonti"]) for f in facts)
    entries = timeline.external_entries()
    assert any(e["data"] == "2026-10-21" and "HLX" in e["titolo"] and e["esterno"] for e in entries)


def test_bad_or_missing_file(tmp_path):
    assert timeline.external_entries(tmp_path / "manca.json") == []
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"fatti": [{"data": "", "titolo": "senza data"}, {"data": "2026-01-02", "titolo": "ok"}]}),
                 encoding="utf-8")
    e = timeline.external_entries(p)
    assert [x["titolo"] for x in e] == ["ok"] and e[0]["anno"] == "2026" and e[0]["date"].startswith("2026-01-02")
