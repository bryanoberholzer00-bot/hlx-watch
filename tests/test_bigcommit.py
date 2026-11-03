"""Commit con più file di quanti l'API di GitHub ne mostri: il resto si legge con git, e l'archeologia
rilegge i commit tagliati già nell'archivio senza creare doppioni."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.archaeology import gather_repo, insert_commits
from hlxwatch.sources.bigcommit import API_FILE_CAP, LocalDiff
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_origin(tmp_path: Path) -> tuple[Path, str, str]:
    """Primo commit con 12 file; il secondo li cambia tutti, ne toglie uno e aggiunge un binario."""
    origin = tmp_path / "origin"
    (origin / "dump").mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "uploadpack.allowFilter", "true")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    for i in range(12):
        (origin / "dump" / f"f{i:02}_strings.txt").write_text(f"riga comune {i}\nhlx_old_{i}\n", encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "uno")
    parent = git(origin, "rev-parse", "HEAD")
    for i in range(12):
        (origin / "dump" / f"f{i:02}_strings.txt").write_text(f"riga comune {i}\nhlx_new_convar_{i}\n", encoding="utf-8")
    (origin / "dump" / "f11_strings.txt").unlink()
    (origin / "dump" / "blob.bin").write_bytes(bytes(range(256)) * 4)
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "2000913 | 13 files")
    return origin, parent, git(origin, "rev-parse", "HEAD")


def make_cfg(tmp_path: Path, origin: Path) -> Config:
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["github"]["repos"] = [{"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "main",
                               "clone_url": origin.as_uri()}]
    return Config(raw, tmp_path / "config.yaml")


def test_local_diff_entries(tmp_path):
    origin, parent, sha = make_origin(tmp_path)
    ld = LocalDiff(tmp_path / "work")
    entries = {e["filename"]: e for e in ld.entries("x/y", sha, parent, {"dump/f00_strings.txt"}, clone_url=origin.as_uri())}
    assert ld.files_total == 13 and len(entries) == 12                      # 13 file, uno già letto dall'API
    assert entries["dump/f11_strings.txt"]["status"] == "removed" and "-hlx_old_11" in entries["dump/f11_strings.txt"]["patch"]
    assert entries["dump/blob.bin"]["status"] == "added" and entries["dump/blob.bin"]["patch"] is None
    assert entries["dump/f05_strings.txt"]["patch"].startswith("@@") and "+hlx_new_convar_5" in entries["dump/f05_strings.txt"]["patch"]
    assert list((tmp_path / "work").iterdir()) == []                        # la cartella di lavoro si cancella


class FakeClient:
    """L'API mostra solo i primi 3 file del commit, come oltre i 3000 file su GitHub."""

    def __init__(self, origin: Path, parent: str, sha: str, url_ok: bool = True):
        self.origin, self.parent, self.sha = origin, parent, sha

    def list_commits(self, repo, branch, **kw):
        return [{"sha": self.sha, "commit": {"committer": {"date": "2026-09-23T00:05:28Z"}}}]

    def commit_detail(self, repo, sha, max_pages=10):
        files = []
        for i in range(3):
            path = f"dump/f{i:02}_strings.txt"
            patch = git(self.origin, "diff", "--unified=3", self.parent, sha, "--", path).split("\n")
            patch = "\n".join(patch[next(n for n, ln in enumerate(patch) if ln.startswith("@@")):])
            files.append({"filename": path, "status": "modified", "patch": patch, "additions": 1, "deletions": 1})
        return {"sha": sha, "parents": [{"sha": self.parent}], "html_url": f"https://github.com/x/commit/{sha}",
                "commit": {"message": "2000913 | 13 files"}, "files": files, "truncated": True}


def test_truncated_commit_is_read_with_git(tmp_path):
    origin, parent, sha = make_origin(tmp_path)
    cfg = make_cfg(tmp_path, origin)
    db = Database(cfg.db_path)
    src = GitHubTrackingSource(cfg, db)
    src.client = FakeClient(origin, parent, sha)
    src.errors = []
    found = src.scan_repo(cfg.github_repos[0])
    added = {f.text for f in found if f.change_type in ("added", "changed")}
    assert {f"hlx_new_convar_{i}" for i in range(11)} <= added            # anche i file oltre il terzo
    assert any(f.change_type == "removed" and f.text == "hlx_old_11" for f in found)   # file tolto, oltre il limite
    assert db.build_row("cs2", "github", sha)["files_changed"] == 13        # il numero vero, non quello dell'API
    assert src.errors == []
    db.close()


def test_truncated_commit_when_git_fails_stays_marked(tmp_path):
    origin, parent, sha = make_origin(tmp_path)
    cfg = make_cfg(tmp_path, origin)
    cfg.raw["github"]["repos"][0]["clone_url"] = (tmp_path / "non-esiste").as_uri()
    db = Database(cfg.db_path)
    src = GitHubTrackingSource(cfg, db)
    src.client = FakeClient(origin, parent, sha)
    src.errors = []
    found = src.scan_repo(cfg.github_repos[0])
    assert "hlx_new_convar_0" in {f.text for f in found if f.change_type in ("added", "changed")}   # la parte dell'API resta
    assert src.errors and "oltre i 3000" in src.errors[0]                 # il controllo di salute lo vede
    assert db.build_row("cs2", "github", sha)["files_changed"] == 3        # resta tagliato: l'archeologia lo rilegge
    db.close()


def test_archaeology_rereads_truncated_commits_without_duplicates(tmp_path):
    origin, parent, sha = make_origin(tmp_path)
    cfg = make_cfg(tmp_path, origin)
    db = Database(cfg.db_path)
    repo_cfg = cfg.github_repos[0]
    # nell'archivio: il commit tagliato dall'API, con una sola delle sue righe
    b = BuildInfo(game="cs2", source="github", ref=sha, date=__import__("datetime").datetime(2026, 9, 23),
                  url=f"https://github.com/x/commit/{sha}", message="2000913 | 13 files", files_changed=API_FILE_CAP)
    bid = db.upsert_build(b)
    db.add_finding(Finding(game="cs2", source="github", file_path="dump/f00_strings.txt", change_type="added",
                           text="hlx_new_convar_0", score=110, keywords=["hlx"], token="hlx_new_convar_0"), bid)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", origin.as_uri(), str(bare)], check=True, capture_output=True)
    stats = {"commit_gia_noti": 0}
    items = gather_repo(cfg, db, repo_cfg, bare, stats)
    assert sha in [c["sha"] for _, c in items] and stats["commit_gia_noti"] == 1      # già noto: si rilegge
    per_repo = {"cs2": {"nuove": 0, "voci": 0, "commit_con_righe": 0}}
    insert_commits(cfg, db, items, per_repo)
    rows = db.conn.execute("SELECT text FROM findings WHERE build_id = ? AND change_type = 'added'", (bid,)).fetchall()
    texts = [r[0] for r in rows]
    assert texts.count("hlx_new_convar_0") == 1 and "hlx_new_convar_10" in texts                # niente doppioni
    assert db.build_row("cs2", "github", sha)["message"] == "2000913 | 13 files"                # messaggio intatto
    before = db.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    insert_commits(cfg, db, gather_repo(cfg, db, repo_cfg, bare, stats), per_repo)             # di nuovo: niente doppioni
    assert db.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == before
    db.close()


def test_archaeology_rereads_known_commits_for_new_keywords(tmp_path):
    """Un commit già nell'archivio per altre righe: una parola chiave arrivata dopo vi trova una riga tolta
    (come CHLXUserCmdPB in Dota 2, tolto il 6 marzo 2023) e l'archeologia la aggiunge, senza doppioni
    anche rispetto alle righe "changed" del monitoraggio dei commit."""
    cfg = make_cfg(tmp_path, tmp_path)
    db = Database(cfg.db_path)
    repo_cfg = cfg.github_repos[0]
    b = BuildInfo(game="cs2", source="github", ref="abc123", date=__import__("datetime").datetime(2023, 3, 6),
                  url="https://github.com/x/commit/abc123", message="5630 | 235 files", files_changed=235)
    bid = db.upsert_build(b)
    db.add_finding(Finding(game="cs2", source="github", file_path="p/base.proto", change_type="changed",
                           text="hlx_new_field", old_text="hlx_old_field", score=110, keywords=["hlx"]), bid)
    commit = {"sha": "abc123", "date": "2023-03-06T23:12:54+00:00", "subject": "5630 | 235 files", "changes": [
        {"path": "p/base.proto", "sign": "+", "text": "hlx_new_field"},           # già registrata come "changed"
        {"path": "p/base.proto", "sign": "-", "text": "hlx_old_field"},
        {"path": "p/usercmd.proto", "sign": "-", "text": "message CHLXUserCmdPB {"}]}
    per_repo = {"cs2": {"nuove": 0, "voci": 0, "commit_con_righe": 0}}
    insert_commits(cfg, db, [(repo_cfg, commit)], per_repo)
    rows = db.conn.execute("SELECT file_path, change_type, text FROM findings WHERE build_id = ? ORDER BY id", (bid,)).fetchall()
    assert [tuple(r) for r in rows][1:] == [("p/usercmd.proto", "removed", "message CHLXUserCmdPB {")]
    assert db.build_row("cs2", "github", "abc123")["message"] == "5630 | 235 files"
    insert_commits(cfg, db, [(repo_cfg, commit)], per_repo)
    assert db.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 2
    db.close()
