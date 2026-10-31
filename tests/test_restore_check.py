import gzip
import json

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.export import backup_db
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.restore_check import STATE, RestoreCheck, check_file
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def fill(db, n, start=0):
    for i in range(start, start + n):
        b = db.upsert_build(BuildInfo(game="cs2", source="github", ref=f"sha{i}", date=utcnow()))
        db.add_finding(Finding(game="cs2", source="github", file_path="a.txt", change_type="added",
                               text=f"riga {i}", score=60, keywords=["hlx"]), b)


class Resp:
    def __init__(self, data=None, content=b"", status=200):
        self.data, self.content, self.status_code = data, content, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    def __init__(self):
        self.files = {}
        self.headers = {}

    def get(self, url, headers=None, timeout=None, stream=False):
        if url.endswith("/releases/tags/archivio"):
            return Resp({"assets": [{"name": n, "url": f"https://api/assets/{n}"} for n in self.files]
                         + [{"name": "altro.txt", "url": "x"}]})
        return Resp(content=self.files[url.rsplit("/", 1)[1]])


class Notifier:
    name = "finto"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def test_good_copy_passes_and_shows_on_site(tmp_path):
    cfg, db = make(tmp_path)
    fill(db, 20)
    backup_db(cfg.db_path, tmp_path / "copia.sqlite.gz")
    fill(db, 1, start=20)                                       # una settimana dopo: una voce in più
    gh = FakeGitHub()
    gh.files["hlxwatch-2026-W43.sqlite.gz"] = b"vecchia"       # la più recente è W44
    gh.files["hlxwatch-2026-W44.sqlite.gz"] = (tmp_path / "copia.sqlite.gz").read_bytes()
    n = Notifier()
    check = RestoreCheck(cfg, db, repo="u/hlx-watch", session=gh)
    result = check.run([n])
    assert result["ok"] and result["file"] == "hlxwatch-2026-W44.sqlite.gz"
    assert result["conteggi"]["findings"] == 20 and n.sent == []
    assert check.run([n]) is None                               # una volta a settimana
    html = (build_site(db, cfg, tmp_path / "site") / "index.html").read_text(encoding="utf-8")
    assert "ultima prova di ripristino della copia settimanale" in html and "(riuscita)" in html
    db.close()


def test_broken_or_incomplete_copy_is_reported_once_and_recovery_too(tmp_path):
    cfg, db = make(tmp_path)
    fill(db, 20)
    gh = FakeGitHub()
    gh.files["hlxwatch-2026-W44.sqlite.gz"] = gzip.compress(b"non sono un database" * 100)
    n = Notifier()
    check = RestoreCheck(cfg, db, repo="u/hlx-watch", session=gh)
    result = check.run([n])
    assert not result["ok"] and any("database" in p for p in result["problemi"])
    assert len(n.sent) == 1 and "NON si ripristina" in n.sent[0][0]
    # la settimana dopo la copia è buona: un avviso che è tutto a posto
    backup_db(cfg.db_path, tmp_path / "buona.sqlite.gz")
    gh.files["hlxwatch-2026-W45.sqlite.gz"] = (tmp_path / "buona.sqlite.gz").read_bytes()
    assert check.run([n], force=True)["ok"]
    assert len(n.sent) == 2 and "di nuovo" in n.sent[1][0]
    assert json.loads(db.get_state(STATE))["ok"] is True
    db.close()


def test_copy_much_smaller_than_the_archive_fails(tmp_path):
    cfg, db = make(tmp_path)
    fill(db, 5)
    backup_db(cfg.db_path, tmp_path / "piccola.sqlite.gz")
    fill(db, 20, start=5)
    counts, problems = check_file(tmp_path / "piccola.sqlite.gz", db.stats(), 90)
    assert counts["findings"] == 5 and any("voci: 5 nella copia contro 25" in p for p in problems)
    counts, problems = check_file(tmp_path / "nonesiste.gz", db.stats(), 90)
    assert problems and "non si decomprime" in problems[0]
    db.close()
