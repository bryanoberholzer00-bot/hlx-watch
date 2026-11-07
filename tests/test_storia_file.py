"""Storico dei file dei personaggi: righe aggiunte e tolte tra versioni, una volta sola, con la data del commit."""
from hlxwatch.sources import storia_file


def test_changed_lines_ignores_moves_and_formatting():
    old = '@PointClass = npc_a\n\t"0" : "HITGROUP_GENERIC"\n\tmetadata\n'
    new = '@PointClass = npc_a\n"0"  :  "HITGROUP_GENERIC"\n\t"41" : "HITGROUP_CHASSIS"\nmetadata\n}\n'
    import re
    assert storia_file.changed_lines(old, new, [re.compile("^metadata$")]) == [("+", '"41" : "HITGROUP_CHASSIS"')]


def test_near():
    days = {"2026-09-23"}
    assert storia_file.near("2026-09-24", days) and storia_file.near("2026-09-23", days)
    assert not storia_file.near("2026-09-26", days) and not storia_file.near("niente", days)


class _Client:
    """Due commit sullo stesso file: il primo aggiunge il telaio, il secondo i fari."""
    V = {"p0": '"0" : "HITGROUP_GENERIC"\n', "c1": '"0" : "HITGROUP_GENERIC"\n"41" : "HITGROUP_CHASSIS"\n',
         "c2": '"41" : "HITGROUP_CHASSIS"\n"54" : "HITGROUP_HEADLIGHT_LEFT"\n'}

    def __init__(self):
        self.calls = 0

    def get_json(self, path, params=None):
        self.calls += 1
        if path.endswith("/contents/game/core"):
            return [{"name": "ai_basenpc.fgd"}, {"name": "base.fgd"}]
        if (params or {}).get("page", 1) > 1:
            return []
        return [{"sha": "c2", "parents": [{"sha": "c1"}], "commit": {"committer": {"date": "2026-03-24T20:00:00Z"}, "message": "b"}},
                {"sha": "c1", "parents": [{"sha": "p0"}], "commit": {"committer": {"date": "2025-07-28T20:00:00Z"}, "message": "a"}}]

    def raw_file(self, repo, sha, path, max_bytes):
        return self.V[sha]


def test_run_once_with_commit_dates(tmp_path):
    import yaml
    from hlxwatch.config import PACKAGE_ROOT, Config
    from hlxwatch.storage import Database
    raw = yaml.safe_load((PACKAGE_ROOT / "config.actions.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path)
    raw["github"]["repos"] = [{"name": "dota2", "repo": "SteamTracking/GameTracking-Dota2", "branch": "master"}]
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(tmp_path / "db.sqlite")
    client = _Client()
    stats = storia_file.run(cfg, db, client, names=("dota2",))
    assert stats["dota2"] == {"file": 1, "commit": 2, "voci": 3}       # telaio aggiunto; fari aggiunti, generico tolto
    rows = db.conn.execute("SELECT change_type, text, created_at FROM findings ORDER BY created_at").fetchall()
    assert [(r[0], r[1], r[2][:10]) for r in rows] == [
        ("added", '"41" : "HITGROUP_CHASSIS"', "2025-07-28"),
        ("added", '"54" : "HITGROUP_HEADLIGHT_LEFT"', "2026-03-24"),
        ("removed", '"0" : "HITGROUP_GENERIC"', "2026-03-24")]
    assert storia_file.run(cfg, db, client, names=("dota2",)) == {}       # una volta sola
    o = storia_file.overview(db, {"2025-07-28"})
    assert o["n"] == 2 and o["vicini"] == 1 and o["attesi"] < 1
    db.close()
