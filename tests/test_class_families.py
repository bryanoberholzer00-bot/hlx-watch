from types import SimpleNamespace

import yaml

from hlxwatch.analyzers.class_families import ClassFamilyTracker, family
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.reevaluate import reevaluate
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def fake_git(schemas, strings):
    def git(args):
        if args[0] == "ls-tree":
            return SimpleNamespace(returncode=0, stdout="\n".join(schemas).encode())
        return SimpleNamespace(returncode=0, stdout="\n".join(strings).encode())
    return git


REPO = {"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "master"}
BASE = ["CBaseEntity", "CBasePlayerPawn", "CCitadelPlayer", "CMathCounter_API", "CALG_AES", "CQ8l", "CP00924",
        "CDOTA_BaseNPC"]


def test_family_rule():
    assert family("CHLXCoverPreviewHelper") == "CHLX"
    assert family("CCitadelPlayer") == "CCitadel"
    assert family("CMathCounter_API") == "CMath"
    assert family("C_DOTA_BaseNPC") == "CDOTA"
    for noise in ("CALG_AES", "CQ8l", "CP00924", "CHERYw", "CURLOPT_PIPEWAIT", "CPU", "Cancel"):
        assert family(noise) is None, noise


def scan(cfg, db, schemas, strings):
    t = ClassFamilyTracker(cfg, db)
    t.collect(REPO, fake_git(schemas, strings))
    return t.finish()


def test_new_family_needs_two_names_and_is_reported_once(tmp_path):
    cfg, db = make(tmp_path)
    assert scan(cfg, db, ["DumpSource2/schemas/server/CBaseEntity.h"], BASE) == []      # prima: impara
    # un nome solo: in attesa, nessuna voce
    assert scan(cfg, db, [], BASE + ["CHLXCoverPreviewHelper"]) == []
    # il secondo nome della stessa famiglia: voce (con HLX vale di più per le parole chiave)
    found = scan(cfg, db, [], BASE + ["CHLXCoverPreviewHelper", "CHLXBaseProjectileEmitterHelper"])
    assert [f.token for f in found] == ["classfam:chlx"] and found[0].score > 55
    assert "CHLXCoverPreviewHelper" in found[0].text
    assert scan(cfg, db, [], BASE + ["CHLXCoverPreviewHelper", "CHLXBaseProjectileEmitterHelper"]) == []
    # famiglia nuova senza parole chiave: 55 punti; da 5 nomi 80
    found = scan(cfg, db, [], BASE + ["CZephyrDoor", "CZephyrLift"])
    assert found[0].score == 55
    big = [f"CAuroraThing{i}Helper" for i in "ABCDE"]
    assert scan(cfg, db, [], BASE + big)[0].score == 80
    reevaluate(cfg, db)                                        # la rivalutazione non le tocca
    assert db.conn.execute("SELECT COUNT(*) FROM findings WHERE change_type = 'class_family'").fetchone()[0] == 3
    db.close()


def test_family_known_in_another_game_is_not_new(tmp_path):
    cfg, db = make(tmp_path)
    scan(cfg, db, [], BASE)
    t = ClassFamilyTracker(cfg, db)
    t.collect({"name": "dota2", "repo": "x/y"}, fake_git([], ["CCitadelHero", "CCitadelAbility"]))
    assert t.finish() == []                                    # CCitadel è già nota (da CS2)
    db.close()
