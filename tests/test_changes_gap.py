import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.steam_webapi import SteamNewAppsSource
from hlxwatch.storage import Database

PRIVATE = "informazioni non pubbliche (richiede token)"


class FakePics:
    def __init__(self, gap):
        self.changes_gap = gap
        self.asked = []

    def call_worker(self, appids, since=-1):
        self.asked.append(list(appids))
        apps = {str(a): {"_missing_token": True} for a in appids}
        if 300 in appids:
            apps["300"] = {"common": {"name": "HLX", "type": "game",
                                      "associations": {"0": {"type": "developer", "name": "Valve"}}}}
        return {"ok": True, "apps": apps}


def make(tmp_path, monkeypatch):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw.setdefault("steam", {}).setdefault("new_apps", {})["max_lookup_per_run"] = 2
    cfg = Config(raw, tmp_path / "config.yaml")
    monkeypatch.setattr(type(cfg), "steam_web_api_key", property(lambda self: None))   # solo PICS, niente rete
    db = Database(cfg.db_path)
    db.insert_apps([(100, None), (200, None), (300, None), (400, "Pubblica")], checked=False)
    for appid in (100, 200, 300):
        db.update_app_details(appid, None, None, None, None, None, False, PRIVATE)
    db.update_app_details(400, "Pubblica", "game", "Qualcuno", None, None, False, None)
    db.set_state("steam:appid_frontier", 400)
    return cfg, db


def test_no_gap_only_changed_private_apps(tmp_path, monkeypatch):
    cfg, db = make(tmp_path, monkeypatch)
    pics = FakePics(gap=False)
    SteamNewAppsSource(cfg, db, pics).run([200])
    assert pics.asked == [[200]]                                   # solo quella cambiata
    db.close()


def test_gap_rechecks_all_private_apps(tmp_path, monkeypatch):
    cfg, db = make(tmp_path, monkeypatch)
    pics = FakePics(gap=True)
    src = SteamNewAppsSource(cfg, db, pics)
    found = src.run([])                                            # elenco saltato: nessuna app cambiata nota
    assert pics.asked == [[300, 200]]                              # le più recenti per prime, max 2 per run
    assert db.get_state("steam:changes_gap_at") is not None
    assert [f.change_type for f in found] == ["new_app"] and "HLX" in found[0].text   # ha preso un nome
    pics.changes_gap = False
    src.run([])
    assert pics.asked[-1] == [100]                                  # il run dopo finisce il lavoro
    db.close()
