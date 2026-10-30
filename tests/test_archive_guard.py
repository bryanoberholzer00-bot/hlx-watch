import json

import yaml

from hlxwatch.archive_guard import TITLE, check, run_guard
from hlxwatch.cli import main
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True


def make(tmp_path, n=20):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    cfg = Config(raw, path)
    db = Database(cfg.db_path)
    bid = db.upsert_build(BuildInfo(game="cs2", source="github", ref="a" * 40, date=utcnow()))
    for i in range(n):
        db.add_finding(Finding(game="cs2", source="github", file_path="x.txt", change_type="added",
                               text=f"hlx_riga_{i}", score=100, keywords=["hlx"]), bid)
    return cfg, db, path


def test_check_rules():
    new = {"findings": 96, "builds": 100, "seen_strings": 100}
    assert check({"findings": 100, "builds": 100, "seen_strings": 100}, new, 5) == []        # -4%: va bene
    problems = check({"findings": 100, "builds": 200, "seen_strings": 100}, new, 5)
    assert problems == ["build e commit: da 200 a 100 (-50.0%)"]
    assert check(None, new, 5) == []                                                       # primo salvataggio
    assert check({"findings": 100}, {"findings": 0}, 5) == ["l'archivio attuale non contiene nessuna voce"]
    assert check({"findings": 100}, {"findings": 150, "builds": 1, "seen_strings": 1}, 5) == []   # crescere va bene


def test_guard_blocks_and_warns(tmp_path):
    cfg, db, _ = make(tmp_path, n=20)
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"findings": 40, "builds": 1, "seen_strings": 20}), encoding="utf-8")
    n = FakeNotifier()
    assert not run_guard(cfg, db, str(old), accept=False, notifiers=[n])
    assert n.sent[0][0] == TITLE and "da 40 a 20" in n.sent[0][1] and "accetta_calo" in n.sent[0][1]
    assert run_guard(cfg, db, str(old), accept=True, notifiers=[n])                     # calo voluto
    assert run_guard(cfg, db, str(tmp_path / "manca.json"), accept=False, notifiers=[n])    # niente da confrontare
    assert len(n.sent) == 1
    db.close()


def test_cli_writes_github_output(tmp_path, monkeypatch):
    cfg, db, path = make(tmp_path, n=10)
    db.close()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"findings": 100}), encoding="utf-8")
    assert main(["--config", str(path), "controlla-archivio", "--old", str(old), "--no-notify"]) == 0
    assert out.read_text(encoding="utf-8").strip() == "ok=false"
    out.unlink()
    assert main(["--config", str(path), "controlla-archivio", "--old", str(old), "--no-notify", "--accetta"]) == 0
    assert out.read_text(encoding="utf-8").strip() == "ok=true"
