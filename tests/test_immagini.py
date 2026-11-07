"""Le immagini degli strumenti: icone nuove tra un commit e l'altro, con la data del disegno scritta nel PNG."""
import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources import immagini
from hlxwatch.storage import Database

XMP = (b'\x89PNG....iTXtXML:com.adobe.xmp<x:xmpmeta><rdf:Description xmp:CreateDate="2026-01-14T16:09:39-08:00" '
       b'xmp:ModifyDate="2026-01-14T16:11:46-08:00"/></x:xmpmeta>')


def test_created_at():
    assert immagini.created_at(XMP) == "2026-01-14T16:09:39-08:00"
    assert immagini.created_at(b"<xmp:CreateDate>2025-03-18T12:19:55-07:00</xmp:CreateDate>") == "2025-03-18T12:19:55-07:00"
    assert immagini.created_at(b"\x89PNG senza dati") is None and immagini.created_at(None) is None


class _Resp:
    def __init__(self, data):
        self.data = data

    def iter_content(self, n):
        yield self.data

    def close(self):
        pass


class _Client:
    """Tre stati della cartella: prima (p0), dopo il commit c1 (+kitchen) e dopo c2 (+asset_hla)."""
    TREES = {"t0": ["common/a.png", "common/b.txt"], "t1": ["common/a.png", "common/kitchen.png"],
             "t2": ["common/a.png", "common/kitchen.png", "common/asset_hla.png"]}
    REF_TREE = {"p0": "t0", "c1": "t1", "c2": "t2", "master": "t2"}

    def __init__(self, commits):
        self.commits = commits

    def get_json(self, path, params=None):
        params = params or {}
        if path.endswith("/commits"):
            if params.get("page", 1) > 1:
                return []
            return [c for c in reversed(self.commits) if c["commit"]["committer"]["date"] >= params["since"]]
        if path.endswith("/contents/game/core/tools"):
            return [{"name": "images", "sha": self.REF_TREE[params["ref"]]}]
        if "/git/trees/" in path:
            return {"tree": [{"path": p, "type": "blob", "sha": p} for p in self.TREES[path.rsplit("/", 1)[-1]]]}
        raise AssertionError(path)

    def _request(self, url, stream=False):
        return _Resp(XMP if url.endswith("kitchen.png") else b"\x89PNG")


def _commit(sha, parent, when):
    return {"sha": sha, "parents": [{"sha": parent}], "commit": {"committer": {"date": when}}}


def _cfg(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.actions.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path)
    raw["github"]["repos"] = [{"name": "dota2", "repo": "SteamTracking/GameTracking-Dota2", "branch": "master"}]
    return Config(raw, tmp_path / "config.yaml")


class _Rec:
    name = "prova"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append(text)
        return True

    def send_groups(self, heading, groups):
        return True


def test_history_then_new_images(tmp_path):
    cfg, db, n = _cfg(tmp_path), Database(tmp_path / "db.sqlite"), _Rec()
    client = _Client([_commit("c1", "p0", "2026-01-22T20:00:00Z")])
    assert immagini.scan(cfg, db, client, [n]) == {"dota2": 1}           # storico: kitchen, in silenzio
    assert n.sent == []
    s = immagini.summary(db)
    k = s["giorni"][0]["immagini"][0]
    assert k["nome"] == "kitchen.png" and k["creata"].startswith("2026-01-14") and k["anticipo"] == 8
    assert [o["nome"] for o in s["etichette"]] == ["kitchen.png"]
    assert immagini.scan(cfg, db, client, [n]) == {"dota2": 0}           # niente di nuovo
    client.commits.append(_commit("c2", "c1", "2026-03-24T20:00:00Z"))
    assert immagini.scan(cfg, db, client, [n]) == {"dota2": 1}           # asset_hla, dopo lo storico
    s = immagini.summary(db, {"2026-03-24"})
    assert s["n"] == 2 and s["giorni"][0]["hlx"] and s["giorni"][0]["immagini"][0]["nome"] == "asset_hla.png"
    db.close()


def test_folder_born_later_is_a_starting_point(tmp_path):
    """Come Deadlock ad agosto 2024: prima del primo commit la cartella non c'era, il primo elenco non è "nuovo"."""
    cfg, db = _cfg(tmp_path), Database(tmp_path / "db.sqlite")

    class Born(_Client):
        def get_json(self, path, params=None):
            if path.endswith("/contents/game/core/tools") and (params or {}).get("ref") == "p0":
                return []
            return super().get_json(path, params)
    client = Born([_commit("c1", "p0", "2024-08-10T20:00:00Z"), _commit("c2", "c1", "2026-03-24T20:00:00Z")])
    assert immagini.scan(cfg, db, client) == {"dota2": 1}                 # solo asset_hla, non tutto c1
    db.close()
