import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.health import Health
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.outcomes import engine_merge_index, merge_partners, outcome_register
from hlxwatch.reports.bot_data import export_bot_data
from hlxwatch.reports.site import build_site
from hlxwatch.reports.site_extras import source_status
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def build(db, game, sha, when, message="123 | 4 files | M x"):
    return db.upsert_build(BuildInfo(game=game, source="github", ref=sha * 40, date=when, message=message,
                                     url=f"https://github.com/SteamTracking/GameTracking-X/commit/{sha * 40}"))


def add(db, bid, game, path, kind, text, token=None, keywords=("hlx",), score=100, **extra):
    f = Finding(game=game, source="github", file_path=path, change_type=kind, text=text, token=token, score=score,
                keywords=list(keywords), **extra)
    db.add_finding(f, bid)
    return f


def d(y, m, day):
    return datetime(y, m, day, tzinfo=timezone.utc)


def fill(db):
    path = "game/core/pak01_dir/soundevents/soundevents_diagnostics.vsndevts"
    b1, b2 = build(db, "cs2", "a", d(2023, 12, 14)), build(db, "cs2", "b", d(2026, 1, 21))
    add(db, b1, "cs2", path, "added", '_builtins._voicegraph = "hlx.hrtf_basic"', token="hlx.hrtf_basic")
    add(db, b2, "cs2", path, "removed", '_builtins._voicegraph = "hlx.hrtf_basic"', token="hlx.hrtf_basic")
    add(db, b2, "cs2", path, "renamed", '_builtins._voicegraph = "core.hrtf_basic"', keywords=("hlx_renamed",),
        old_text='_builtins._voicegraph = "hlx.hrtf_basic"', token="rename:x", context="rinominata: il prefisso hlx è diventato 'core'")
    b3, b4 = build(db, "dota2", "c", d(2021, 9, 16)), build(db, "dota2", "e", d(2021, 12, 15))
    add(db, b3, "dota2", "game/dota/bin/x_strings.txt", "added", "hlx_combine_barrier_cell_spacing", token="hlx_combine_barrier_cell_spacing")
    add(db, b4, "dota2", "game/dota/bin/x_strings.txt", "removed", "hlx_combine_barrier_cell_spacing", token="hlx_combine_barrier_cell_spacing")
    b5, b6 = build(db, "cs2", "f", d(2026, 9, 23)), build(db, "dota2", "g", d(2026, 10, 7))
    hammer = "game/bin/win64/tools/hammer_strings.txt"
    add(db, b5, "cs2", hammer, "added", "CHLXBaseProjectileEmitterHelper", token="CHLXBaseProjectileEmitterHelper", keywords=("hlx_class",))
    add(db, b6, "dota2", hammer, "added", "CHLXBaseProjectileEmitterHelper", token="CHLXBaseProjectileEmitterHelper", keywords=("hlx_class",))
    add(db, b5, "cs2", "game/core/ai_basenpc.fgd", "added", '"- Thumper (HLX): causes antlions to run away"', token="HLX")
    add(db, b5, "cs2", "game/bin/win64/particles_strings.txt", "added", "mod != hlx", token="hlx")
    add(db, b5, "cs2", "game/csgo/bin/win64/modtools_strings.txt", "added", "hlx_physics_particle_spawner",
        token="hlx_physics_particle_spawner")
    db.set_snapshot("fullscan:cs2", "x", f"{hammer}\tCHLXBaseProjectileEmitterHelper\ngame/core/ai_basenpc.fgd\t"
                                        '"- Thumper (HLX): causes antlions to run away"')


def test_outcome_register(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    reg = outcome_register(cfg, db)
    by = {t["label"]: t for t in reg["traces"]}
    assert by["hlx.hrtf_basic"]["status"] == "rinominata" and "motore comune" in by["hlx.hrtf_basic"]["detail"]
    assert by["hlx_combine_barrier_cell_spacing"]["status"] == "sparita"
    assert by["CHLXBaseProjectileEmitterHelper"]["status"] == "presente"
    assert list(by["CHLXBaseProjectileEmitterHelper"]["games"]) == ["cs2", "dota2"]
    assert by["hlx_physics_particle_spawner"]["status"] == "manuale" and "probabile rinomina" in by["hlx_physics_particle_spawner"]["detail"]
    # il token "hlx" da solo non unisce righe diverse
    assert '"- Thumper (HLX): causes antlions to run away"' in by and "mod != hlx" in by
    assert reg["moved"] == {"motore comune": 1} and reg["total"] == len(reg["traces"])
    assert [t["first"] for t in reg["traces"]] == sorted(t["first"] for t in reg["traces"])
    db.close()


def test_engine_merge(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    idx = engine_merge_index(db)
    assert merge_partners(idx, "dota2", "CHLXBaseProjectileEmitterHelper", "", "2026-10-07") == ["cs2"]
    assert merge_partners(idx, "cs2", "CHLXBaseProjectileEmitterHelper", "", "2026-09-23", {"dota2": "Dota 2"}) == ["Dota 2"]
    assert merge_partners(idx, "cs2", "CHLXBaseProjectileEmitterHelper", "", "2027-06-01") == []      # troppo lontano
    assert merge_partners(idx, "steamvr", "CHLXBaseProjectileEmitterHelper", "", "2026-10-07") == []   # non è un gioco del motore
    db.close()


def test_site_esiti_feed_and_merge_label(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    db.set_state("health:upstream_alerted", "1")          # prima faceva fallire lo stato delle fonti
    assert source_status(cfg, db)["problems"] == []
    out = build_site(db, cfg, tmp_path / "site")
    esiti = (out / "esiti.html").read_text(encoding="utf-8")
    assert "Dove è finita la tecnologia HLX" in esiti and "motore comune" in esiti
    feed = ET.parse(out / "feed.xml").getroot()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = feed.findall("a:entry", ns)
    assert entries and all(e.find("a:id", ns).text.startswith("tag:hlx-watch") for e in entries)
    assert 'rel="alternate" type="application/atom+xml"' in (out / "index.html").read_text(encoding="utf-8")
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    emitter = [r for r in data if r.get("token") == "CHLXBaseProjectileEmitterHelper"]
    assert {tuple(r.get("fm", [])) for r in emitter} == {("Counter-Strike 2",), ("Dota 2",)}
    assert "Dati aperti" in (out / "metodologia.html").read_text(encoding="utf-8")
    db.close()


def test_format_check_alerts_once_and_recovers(tmp_path):
    cfg, db = make(tmp_path)
    n = FakeNotifier()
    h = Health(cfg, db, [n])
    now = utcnow()
    for i in range(4):
        build(db, "cs2", chr(ord("a") + i), now - timedelta(days=i), message="Some new commit style")
    h.check_format()
    h.check_format()
    assert [t for t, _ in n.sent] == ["HLX Watch: formato di SteamTracking cambiato"]
    build(db, "cs2", "z", now, message="26281801 | 199 files | M x")
    h.check_format()
    assert n.sent[-1][0] == "HLX Watch: formato di SteamTracking di nuovo riconosciuto"
    db.close()


def test_bot_data_marks_announcement(tmp_path):
    cfg, db = make(tmp_path)
    bid = db.upsert_build(BuildInfo(game="steamtracking", source="github", ref="repo:x", date=utcnow()))
    add(db, bid, "steamtracking", "SteamTracking/GameTracking-HalfLife3", "new_repo", "POSSIBILE ANNUNCIO: Nuovo repository",
        keywords=("new_repo", "possible_announcement"), score=200, token="repo:x")
    add(db, bid, "steamtracking", "SteamTracking/GameTracking-Foo", "new_repo", "Nuovo repository Foo",
        keywords=("new_repo",), score=70, token="repo:y")
    (tmp_path / "out").mkdir()
    export_bot_data(db, cfg, tmp_path / "out", "https://example/")
    ultime = json.loads((tmp_path / "out" / "bot" / "ultime.json").read_text(encoding="utf-8"))
    assert [(u["k"], u.get("a")) for u in ultime] == [("repo:y", None), ("repo:x", 1)] or \
        sorted((u["k"], u.get("a")) for u in ultime) == [("repo:x", 1), ("repo:y", None)]
    db.close()
