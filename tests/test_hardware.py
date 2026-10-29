from datetime import datetime, timezone

import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.codenames import CodenameTracker, detect
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_vr_device_place_learns_on_its_own(tmp_path):
    cfg, db = make(tmp_path)
    assert detect('\t"controller_type": "frame_hmd",') == [("vr_device", "frame_hmd", "tipo di dispositivo di SteamVR (controller_type)")]
    db.set_state("codenames:learned", "1")            # installazione esistente: apprendimento concluso prima del posto nuovo
    t = CodenameTracker(cfg, db)
    assert t.check('"controller_type": "deckard"', "drivers/deckard/resources/input_profile.json", game="steamvr") is None
    t.check('"controller_type": "deckard"', "drivers/deckard/resources/input_profile.json", game="steamvr", change_type="present")
    assert t.check("mod != zeta2", "a.h", game="cs2") is not None      # i posti vecchi restano attivi
    t.finish_learning()                                                 # fine della scansione completa
    t2 = CodenameTracker(cfg, db)
    assert t2.check('"controller_type": "deckard"', "x.json", game="steamvr") is None    # imparato in silenzio
    f = t2.check('"controller_type": "roy_controller"', "drivers/deckard/resources/input_profile.json", game="steamvr")
    assert f is not None and f.score == 60 and f.token == "codename:roy_controller"     # punteggio del posto
    assert t2.check("mod != omega", "a.h", game="cs2").score == 120
    db.close()


def test_valve_driver_files_are_watched(tmp_path):
    cfg, db = make(tmp_path)
    src = GitHubTrackingSource(cfg, db)
    m = KeywordMatcher(cfg.keywords)
    lines = [("+", '\t\t"Name" : "Knuckles EV1.5"'), ("+", "\t}")]
    for path in ("drivers/indexcontroller/resources/firmware/manifest.vrfirmware", "drivers/deckard/resources/input_profile.json",
                 "drivers/frame_hmd/driver.vrdrivermanifest", "drivers/frame_controller/resources/input/frame_controller_profile.json"):
        out = [f for f in src._extra_findings("steamvr", path, lines, m) if f.keywords == ["watched_file"]]
        assert [(f.text, f.score) for f in out] == [('"Name" : "Knuckles EV1.5"', 55)], path
    for path in ("drivers/htc/resources/input/vive_profile.json",                       # non Valve
                 "drivers/frame_controller/resources/input/beat_saber_bindings_frame_controller.json"):   # comandi di un gioco
        assert [f for f in src._extra_findings("steamvr", path, lines, m) if f.keywords == ["watched_file"]] == []
    db.close()


def test_valve_hardware_keyword(tmp_path):
    cfg, db = make(tmp_path)
    m = KeywordMatcher(cfg.keywords)
    r = m.analyze_line("Status LED brightness control disabled: only supported on galileo", file_path="ClientExtracted/steamui/x.js")
    assert r is not None and r.keywords == ["valve_hardware"] and 45 <= r.score < 50
    assert m.analyze_line("Ibex Controller Test", file_path="ClientExtracted/x.js").keywords == ["valve_hardware"]
    camel = m.analyze_line("ClientJobPairIbexController", file_path="bin/steamui_strings.txt")
    assert camel is not None and camel.keywords == ["valve_hardware_class"]
    assert m.analyze_line("CNeptuneInput_Update", file_path="bin/x_strings.txt") is not None
    assert m.analyze_line("Neptunes", file_path="x.js") is None
    assert m.analyze_line("galileo statue", file_path="game/dota/pak01_dir.txt") is None              # file dei giochi
    assert m.analyze_line("Steam Frame is ready", file_path="ClientExtracted/x.js") is None             # prodotto uscito
    assert m.analyze_line("ibexample", file_path="x.js") is None
    db.close()


class FakeClient:
    def __init__(self, files):
        self.files = files                                  # (sha, path) -> testo
        self.calls = 0

    def get_json(self, path, params=None):
        self.calls += 1
        return [{"sha": params["sha"]}, {"sha": "p" * 40}]

    def raw_file(self, repo, sha, path, max_bytes):
        return self.files.get((sha, path), "")


def test_backfill_renames(tmp_path):
    cfg, db = make(tmp_path)
    sha, path = "a" * 40, "game/core/pak01_dir/soundevents/soundevents_diagnostics.vsndevts"
    when = datetime(2026, 1, 21, tzinfo=timezone.utc)
    bid = db.upsert_build(BuildInfo(game="cs2", source="github", ref=sha, date=when, url="https://x/commit/" + sha))
    db.add_finding(Finding(game="cs2", source="github", file_path=path, change_type="removed",
                           text='_builtins._voicegraph = "hlx.hrtf_basic"', score=100, keywords=["hlx"]), bid, created_at=when)
    src = GitHubTrackingSource(cfg, db)
    src.client = FakeClient({("p" * 40, path): '\t\t_builtins._voicegraph = "hlx.hrtf_basic"\n\t\tx = 1',
                             (sha, path): '\t\t_builtins._voicegraph = "core.hrtf_basic"\n\t\tx = 1'})
    assert src.backfill_renames() == 1
    row = db.conn.execute("SELECT * FROM findings WHERE change_type = 'renamed'").fetchone()
    assert row["text"] == '_builtins._voicegraph = "core.hrtf_basic"' and row["old_text"].endswith('"hlx.hrtf_basic"')
    assert row["created_at"].startswith("2026-01-21") and row["build_id"] == bid
    calls = src.client.calls
    assert src.backfill_renames() == 0 and src.client.calls == calls      # una volta sola
    db.close()
