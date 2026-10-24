import yaml

from hlxwatch.analysis import collect_input, is_generic
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.first_seen import FirstSeenResolver, line_hash
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

LINE = '"- Thumper (HLX): causes antlions to run away briefly<br>"'


class FakeGitHub:
    """20 commit del file; la riga compare dal commit 13 (indice 13) in poi."""

    def __init__(self, first_index=13, n=20):
        self.commits = [{"sha": f"sha{i:02d}", "commit": {"committer": {"date": f"2025-01-{i + 1:02d}T00:00:00Z"}}}
                        for i in range(n)]
        self.first_index = first_index
        self.raw_calls = 0

    def get_json(self, path, params=None):
        page = (params or {}).get("page", 1)
        return list(reversed(self.commits)) if page == 1 else []      # l'API restituisce dal più nuovo

    def raw_file(self, repo, sha, path, max_bytes):
        self.raw_calls += 1
        idx = int(sha[3:])
        return "intestazione\n" + (LINE + "\n" if idx >= self.first_index else "") + "fine\n"


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    build = BuildInfo(game="cs2", source="fullscan", ref="tree:abc", date=utcnow(),
                      url="https://github.com/SteamTracking/GameTracking-CS2/tree/abc")
    bid = db.upsert_build(build)
    db.add_finding(Finding(game="cs2", source="fullscan", file_path="game/core/ai_basenpc.fgd", change_type="present",
                           text=LINE, score=100, keywords=["hlx"], token="HLX"), bid)
    return cfg, db


def test_bisection_finds_first_commit(tmp_path):
    cfg, db = make(tmp_path)
    gh = FakeGitHub(first_index=13)
    solved = FirstSeenResolver(cfg, db, client=gh).run()
    assert solved == 1
    fs = db.get_first_seen("cs2", "game/core/ai_basenpc.fgd", line_hash(LINE))
    assert fs["commit_sha"] == "sha13" and fs["commit_date"].startswith("2025-01-14") and not fs["at_least"]
    assert gh.raw_calls <= 6                      # bisezione, non scansione lineare di 20 versioni
    assert FirstSeenResolver(cfg, db, client=gh).run() == 0          # già datata: niente lavoro

    data, ids = collect_input(db, utcnow())
    present = data["gia_presenti_nel_contenuto_attuale"][0]
    assert present["prima_comparsa"] == "2025-01-14"
    assert present["commit_prima_comparsa"].endswith("/commit/sha13")
    db.close()


def test_at_least_when_present_from_start(tmp_path):
    cfg, db = make(tmp_path)
    FirstSeenResolver(cfg, db, client=FakeGitHub(first_index=0)).run()
    fs = db.get_first_seen("cs2", "game/core/ai_basenpc.fgd", line_hash(LINE))
    assert fs["at_least"] == 1 and fs["commit_sha"] == "sha00"
    db.close()


def test_generic_noise_filter():
    generic = {"npc", "vehicle", "weather", "physics", "half_life"}
    assert is_generic({"score": 50, "keywords": '["npc"]'}, generic)
    assert not is_generic({"score": 50, "keywords": '["npc", "hlx"]'}, generic)
    assert not is_generic({"score": 120, "keywords": '["npc"]'}, generic)
    assert not is_generic({"score": 55, "keywords": '["steam_build"]'}, generic)
