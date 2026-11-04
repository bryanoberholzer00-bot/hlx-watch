"""Prova sul passato: i rilevatori di oggi su un repository finto, senza conoscere il nome del progetto."""
import subprocess
from datetime import datetime, timezone

import yaml

from hlxwatch import backtest
from hlxwatch.config import PACKAGE_ROOT, Config


def git(cwd, *args, env=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def commit(repo, message, when):
    import os
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when, GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", message, env=env)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path, start_names, later_line):
    repo = tmp_path / "src"
    (repo / "tools" / "bugreporter").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    cfg_file = repo / "tools" / "bugreporter" / "config.txt"
    cfg_file.write_text("".join(f'"product" "{n}"\n' for n in start_names), encoding="utf-8")
    first = commit(repo, "inizio", "2022-01-10T10:00:00+00:00")
    cfg_file.write_text(cfg_file.read_text(encoding="utf-8") + later_line, encoding="utf-8")
    last = commit(repo, "aggiornamento", "2022-02-10T10:00:00+00:00")
    return repo, first, last


def make_cfg(tmp_path, repo):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["github"]["repos"] = [{"name": "finto", "title": "Finto", "repo": "x/finto", "branch": "master",
                               "clone_url": repo.resolve().as_uri()}]
    raw.setdefault("codenames", {})["known"] = ["dota", "citadel"]
    return Config(raw, tmp_path / "config.yaml")


def fake_commits(monkeypatch, first, last):
    dates = {first: datetime(2022, 1, 10, 10, tzinfo=timezone.utc), last: datetime(2022, 2, 10, 10, tzinfo=timezone.utc)}

    def before(client, repo, branch, when):
        sha = last if when >= dates[last] else first
        return sha, dates[sha]
    monkeypatch.setattr(backtest, "commit_before", before)


TEST = {"title": "prova", "project": "Deadlock", "announced": "2024-08-23", "repo": "finto",
        "start": "2022-02-01", "end": "2022-03-01", "codes": r"citadel"}


def test_new_codename_is_an_alert_even_if_known_today(tmp_path, monkeypatch):
    repo, first, last = make_repo(tmp_path, ["dota"], '"product" "citadel"\n')
    fake_commits(monkeypatch, first, last)
    r = backtest.run_test(make_cfg(tmp_path, repo), "t", TEST, tmp_path / "work")
    assert r["commits"] == 1 and r["learned_at_start"] == []
    assert r["first_related"]["day"] == "2022-02-10" and r["first_related"]["anticipo_giorni"] == 925
    # il nome nuovo nel bug reporter, più la riga del file sorvegliato
    assert {a["type"] for a in r["related"]} == {"codename", "added"}


def test_name_present_at_start_is_learned_silently(tmp_path, monkeypatch):
    repo, first, last = make_repo(tmp_path, ["dota", "citadel"], '"product" "underlords"\n')
    fake_commits(monkeypatch, first, last)
    r = backtest.run_test(make_cfg(tmp_path, repo), "t", TEST, tmp_path / "work")
    assert r["learned_at_start"] == ["citadel"]
    assert r["first_related"] is None
    assert "codename:underlords" in [a["token"] for a in r["others_sample"]]


def test_blind_config_removes_hindsight(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["codenames"] = {"known": ["csgo", "citadel", "citadel_rel_win64"]}
    raw["watched_files"] = [{"pattern": "^drivers/(deckard|frame_hmd)/x$"}, {"pattern": "gameinfo"}]
    raw["keywords"]["global_excludes"] = ["citadel", "altro"]
    raw["keywords"]["items"] = [{"id": "hw", "pattern": "(deckard|ibex)", "weight": 35},
                                {"id": "hlx", "pattern": "hlx", "weight": 100}]
    cfg = backtest.blind_config(Config(raw, tmp_path / "c.yaml"), tmp_path / "w", r"citadel|deckard")
    assert cfg.get("codenames.known") == ["csgo"]
    assert [w["pattern"] for w in cfg.get("watched_files")] == ["gameinfo"]
    assert cfg.get("keywords.global_excludes") == ["altro"]
    assert [i["id"] for i in cfg.get("keywords.items")] == ["hlx"]
    assert cfg.db_path == tmp_path / "w" / "backtest.sqlite"


def test_new_class_family_found_by_end_scan_and_dated(tmp_path, monkeypatch):
    repo = tmp_path / "src"
    (repo / "bin").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    dump = repo / "bin" / "client_strings.txt"
    dump.write_text("CDotaPlayerPawn\nCDotaAbilityBase\n", encoding="utf-8")
    first = commit(repo, "inizio", "2022-01-10T10:00:00+00:00")
    dump.write_text(dump.read_text(encoding="utf-8") + "CCitadelPlayerPawn\nCCitadelAbilityBase\n", encoding="utf-8")
    last = commit(repo, "aggiornamento", "2022-02-10T10:00:00+00:00")
    fake_commits(monkeypatch, first, last)
    r = backtest.run_test(make_cfg(tmp_path, repo), "t", TEST, tmp_path / "work")
    fam = [a for a in r["related"] if a["type"] == "class_family"]
    assert fam and fam[0]["day"] == "2022-02-10" and fam[0]["token"] == "classfam:ccitadel"
