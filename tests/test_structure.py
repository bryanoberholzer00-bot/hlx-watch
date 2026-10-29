import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import Pipeline
from hlxwatch.sources.structure import StructureWatch
from hlxwatch.storage import Database


class FakeClient:
    """Elenchi dell'API dei contenuti: (repo, percorso) -> [(nome, tipo)]."""

    def __init__(self):
        self.tree = {
            ("SteamTracking/GameTracking-CS2", "game"): [("bin", "dir"), ("core", "dir"), ("csgo", "dir"), ("README", "file")],
            ("SteamTracking/GameTracking-CS2", "manifests"): [("manifest_731.txt", "file"), ("manifest_732.txt", "file")],
            ("SteamTracking/Protobufs", ""): [("csgo", "dir"), ("dota2", "dir"), ("steam", "dir"), ("README.md", "file")],
        }
        self.calls = 0

    def get_json(self, path, params=None):
        self.calls += 1
        parts = path.split("/")
        repo, sub = "/".join(parts[2:4]), "/".join(parts[5:])
        entries = self.tree.get((repo, sub))
        if entries is None:
            return None
        return [{"name": n, "type": t, "html_url": f"https://github.com/{repo}/tree/master/{sub}/{n}"} for n, t in entries]


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_targets_only_game_repos_and_protobufs(tmp_path):
    cfg, db = make(tmp_path)
    w = StructureWatch(cfg, db, FakeClient())
    pairs = {(r["name"], p["path"]) for r, p in w.targets()}
    assert ("cs2", "game") in pairs and ("deadlock", "manifests") in pairs and ("protobufs", "") in pairs
    assert ("cs2", "game/bin/win64") in pairs and ("cs2", "game/csgo/bin/win64") in pairs
    assert ("alyx", "game/hlvr/bin/win64") in pairs and not any("{gamedir}" in p for _, p in pairs)
    assert not any(name in ("steamclient", "steamworks") for name, _ in pairs)   # senza appid: niente game/
    assert {p for n, p in pairs if n == "steamvr"} == {"drivers", "resources/rendermodels", "resources/input",
        "drivers/frame_controller/resources/rendermodels", "drivers/deckard/resources/rendermodels",
        "drivers/frame_controller/resources/input"}   # solo l'hardware
    db.close()


def test_baseline_then_new_and_removed(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    w = StructureWatch(cfg, db, client)
    assert w.run() == []                                        # primo giro: registra e basta
    assert w.run() == []                                        # niente cambiamenti: niente voci

    client.tree[("SteamTracking/GameTracking-CS2", "game")] += [("hlx", "dir"), ("newthing.txt", "file")]
    client.tree[("SteamTracking/GameTracking-CS2", "manifests")] = [("manifest_732.txt", "file"), ("manifest_2347790.txt", "file")]
    client.tree[("SteamTracking/Protobufs", "")] += [("hl3", "dir")]
    found = {f.token: f for f in w.run()}
    assert set(found) == {"structure:cs2:game/hlx", "structure:cs2:manifests/manifest_2347790.txt",
                          "structure:cs2:manifests/manifest_731.txt:removed", "structure:protobufs:/hl3"}
    hlx = found["structure:cs2:game/hlx"]
    assert hlx.score >= 120 and "hlx" in hlx.keywords and hlx.change_type == "structure" and hlx.is_new
    assert hlx.file_path == "game/hlx" and hlx.build is not None and hlx.build.url.endswith("/tree/master/game")
    assert found["structure:cs2:manifests/manifest_2347790.txt"].score == 100
    assert found["structure:cs2:manifests/manifest_731.txt:removed"].score == 60
    assert found["structure:protobufs:/hl3"].file_path == "hl3" and found["structure:protobufs:/hl3"].score >= 110
    assert w.run() == []                                        # stato aggiornato: niente doppioni
    alerts = Pipeline(cfg, db).select_alerts(list(found.values()))
    assert len(alerts) == 4                                     # anche la voce sparita supera la soglia (60 >= 50)
    db.close()


def test_disabled_and_missing_path(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    cfg.raw["structure"] = {"enabled": False}
    assert StructureWatch(cfg, db, client).run() == [] and client.calls == 0
    cfg.raw["structure"] = {"places": [{"path": "nonesiste", "kind": "dirs", "repos": ["cs2"]}]}
    w = StructureWatch(cfg, db, client)
    assert w.run() == [] and db.get_state("structure:cs2:nonesiste") is None     # 404: nessuno stato, nessun avviso
    db.close()
