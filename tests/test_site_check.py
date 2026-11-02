import json

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.site_check import REQUIRED, STATE, check_site, run_site_check
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

PAGE = ('<!doctype html><html><head><title>t</title></head><body id="top">'
        '<a class="termo" href="termometro.html"><b>{termo}</b><span>Calma</span></a>{body}</body></html>')


def make_site(root, termo=5, body=""):
    for name in REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".html"):
            path.write_text(PAGE.format(termo=termo, body=body if name == "index.html" else ""), encoding="utf-8")
        elif name.endswith((".json", ".webmanifest")):
            path.write_text("{}", encoding="utf-8")
        elif name.endswith(".xml"):
            path.write_text("<feed/>", encoding="utf-8")
        else:
            path.write_text("x", encoding="utf-8")
    (root / "bot" / "termometro.json").write_text(json.dumps({"indice": 5, "ultima_traccia": {"data": "2026-10-07"}}),
                                                  encoding="utf-8")
    return root


def texts(problems):
    return [p["testo"] for p in problems]


def test_clean_site_has_no_problems(tmp_path):
    make_site(tmp_path, body='<a href="hlx.html#top">ok</a><a href="https://example.com/x">esterno</a>'
                             '<a href="#top">su</a><img src="favicon.png?v=2">')
    (tmp_path / "favicon.png").write_bytes(b"png")
    assert check_site(tmp_path, need_preview=False) == []


def test_missing_file_and_unreadable_json_are_grave(tmp_path):
    make_site(tmp_path)
    (tmp_path / "search.html").unlink()
    (tmp_path / "bot" / "ultime.json").write_text("{rotto", encoding="utf-8")
    problems = check_site(tmp_path, need_preview=True)
    assert all(p["grave"] for p in problems)
    joined = " ".join(texts(problems))
    assert "search.html" in joined and "anteprima.png" in joined and "bot/ultime.json" in joined


def test_cut_page_is_grave(tmp_path):
    make_site(tmp_path)
    (tmp_path / "hlx.html").write_text("<html><body><p>a metà", encoding="utf-8")
    problems = check_site(tmp_path, need_preview=False)
    assert any(p["grave"] and "tagliate" in p["testo"] and "hlx.html" in p["testo"] for p in problems)


def test_broken_links_and_sections_are_reported_but_not_grave(tmp_path):
    make_site(tmp_path, body='<a href="manca.html">x</a><a href="hlx.html#nessuna">y</a>'
                             '<a href="../fuori.html">z</a><a href="${url}">da script</a>')
    problems = check_site(tmp_path, need_preview=False)
    assert problems and not any(p["grave"] for p in problems)
    joined = " ".join(texts(problems))
    assert "index.html → manca.html" in joined and "hlx.html#nessuna" in joined and "../fuori.html" in joined
    assert "${url}" not in joined


def test_template_leftovers_are_reported(tmp_path):
    make_site(tmp_path, body="<p>Ultima traccia: None</p><script>var a = '{{x}}'; var b = undefined;</script>")
    problems = texts(check_site(tmp_path, need_preview=False))
    assert len(problems) == 1 and "None" in problems[0] and "{{" not in problems[0]


def test_thermometer_must_match_the_widget(tmp_path):
    make_site(tmp_path, termo=54, body='<span>Ultima traccia HLX forte</span> <b>2026-10-29</b>')
    problems = " ".join(texts(check_site(tmp_path, need_preview=False)))
    assert "il widget dice 5" in problems and "index.html (54)" in problems
    assert "2026-10-29 sulla pagina iniziale, 2026-10-07 nel widget" in problems


def make_db(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


class Fake:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def test_alerts_only_when_a_problem_appears_and_when_it_goes_away(tmp_path):
    _, db = make_db(tmp_path)
    site = make_site(tmp_path / "sito", body='<a href="manca.html">x</a>')
    fake = Fake()
    first = run_site_check(db, site, [fake], need_preview=False)
    assert not first["ok"] and not first["grave"] and len(fake.sent) == 1
    assert "pubblicato lo stesso" in fake.sent[0][1]
    run_site_check(db, site, [fake], need_preview=False)          # stesso problema: nessun nuovo avviso
    assert len(fake.sent) == 1
    (site / "search.html").unlink()                                # problema nuovo e grave: avviso
    result = run_site_check(db, site, [fake], need_preview=False)
    assert result["grave"] and len(fake.sent) == 2 and "NON è stato pubblicato" in fake.sent[1][1]
    make_site(site)                                                # tutto a posto: avviso di ripristino
    assert run_site_check(db, site, [fake], need_preview=False)["ok"]
    assert len(fake.sent) == 3 and "a posto" in fake.sent[2][0]
    assert json.loads(db.get_state(STATE))["ok"] is True
    db.close()


def test_generated_site_passes_the_check(tmp_path):
    cfg, db = make_db(tmp_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="abc123", date=utcnow()))
    db.add_finding(Finding(game="cs2", source="github", file_path="game/hlx.txt", change_type="added",
                           text="CHLXBaseProjectileEmitterHelper", token="CHLXBaseProjectileEmitterHelper",
                           score=130, keywords=["hlx_class"], is_new=True), b)
    out = build_site(db, cfg, tmp_path / "sito")
    assert texts(check_site(out)) == []
    db.close()


def test_command_tells_the_workflow_whether_to_publish(tmp_path, monkeypatch):
    from hlxwatch.cli import main
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    out = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    site = make_site(tmp_path / "sito")
    (site / "anteprima.png").write_bytes(b"png")
    assert main(["--config", str(path), "controlla-sito", "--site", str(site), "--no-notify"]) == 0
    (site / "index.html").unlink()
    assert main(["--config", str(path), "controlla-sito", "--site", str(site), "--no-notify"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["pubblica=true", "pubblica=false"]
