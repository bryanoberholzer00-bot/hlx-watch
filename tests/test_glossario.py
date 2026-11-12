"""Glossario (hlxwatch/glossario.py, glossario.html)."""
import re

import yaml

from hlxwatch import glossario
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.reports.site import build_site
from hlxwatch.site_check import check_site
from hlxwatch.storage import Database


def test_terms_are_well_formed():
    ids = [t["id"] for t in glossario.TERMS]
    assert len(ids) == len(set(ids)), "id doppi"
    assert all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)
    groups = {k for k, _ in glossario.GROUPS}
    assert all(t["gruppo"] in groups and t["nome"] and len(t["testo"]) > 40 for t in glossario.TERMS)
    # i rimandi interni (#id) puntano a parole che esistono
    for t in glossario.TERMS:
        for ref in re.findall(r'href="#([a-z0-9-]+)"', t["testo"]):
            assert ref in ids, (t["id"], ref)
    data = glossario.data()
    assert data["count"] == len(ids) and sum(len(g["terms"]) for g in data["groups"]) == len(ids)
    # le parole che il sito usa negli avvisi e nelle etichette ci sono
    assert {"fusione", "solo-in", "mai-vista", "almeno-dal", "tipo-prova", "termometro", "possibile-annuncio"} <= set(ids)


def test_page_menu_and_links(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "hlxwatch.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "glossario.html").read_text(encoding="utf-8")
    assert all(f'<dt id="{t["id"]}">' in page for t in glossario.TERMS)
    assert 'id="gl-q"' in page and "Fusione del motore" in page
    assert 'href="glossario.html"' in (out / "index.html").read_text(encoding="utf-8")            # nel menu
    assert 'href="glossario.html"' in (out / "metodologia.html").read_text(encoding="utf-8")
    assert not [p for p in check_site(out, need_preview=False) if "glossario" in p["testo"].lower()]
    db.close()
