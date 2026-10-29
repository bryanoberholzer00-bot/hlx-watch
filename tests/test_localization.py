import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.localization import LocalizationTracker, prefix_counts
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.full_scan import FullScanSource
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database


def make(tmp_path, repos=None):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    if repos:
        raw["github"]["repos"] = repos
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def tokens(prefix, n, start=0):
    return [f'\t\t"{prefix}_Item_{i}"\t\t"testo {i}"' for i in range(start, start + n)]


def test_prefix_counts():
    lines = ['\t"Language" "English"', '\t"Tokens"', '\t{', *tokens("CSGO", 3), *tokens("sfui", 2),
             '\t\t"NoUnderscore"\t"x"', '\t\t"DOTA_x"', '// "Fake_token" "commento"']
    assert prefix_counts(lines) == {"csgo": 3, "sfui": 2}       # senza valore o senza _ non conta


def test_learn_then_commit_alerts(tmp_path):
    cfg, db = make(tmp_path)
    t = LocalizationTracker(cfg, db)
    path = "game/csgo/pak01_dir/resource/csgo_english.txt"
    assert t.check_file("cs2", path, tokens("Zeta", 30)) == []          # non ancora imparato: silenzio
    assert t.learn("cs2", {"csgo": 4698, "sfui": 5635}) == []           # prima scansione: impara
    assert t.known("cs2") == {"csgo": 4698, "sfui": 5635}

    assert t.check_file("cs2", path, tokens("CSGO", 50)) == []          # famiglia nota
    assert t.check_file("cs2", path, tokens("Zeta", 5)) == []           # troppo pochi token
    out = t.check_file("cs2", path, tokens("Zeta", 25) + tokens("CSGO", 2))
    assert [f.token for f in out] == ["locprefix:cs2:zeta"]
    f = out[0]
    assert f.change_type == "localization" and f.score == 60 and f.keywords == ["loc_prefix_new"]
    assert "zeta_*" in f.text and "30 testi" in f.text and f.file_path == path   # 5 + 25: il totale accumulato
    assert t.known("cs2")["zeta"] == 30 and t.known("cs2")["csgo"] == 4750
    assert t.check_file("cs2", path, tokens("Zeta", 40)) == []          # una volta sola
    assert t.check_file("cs2", "game/csgo/x.vdata", tokens("Omega", 40)) == []     # non è un file di localizzazione
    assert t.check_file("cs2", "game/csgo/resource/csgo_italian.txt", tokens("Omega", 40)) == []   # solo inglese

    new = t.check_file("cs2", "game/hlx/resource/hlx_english.txt", tokens("HLX", 300) + tokens("Hud", 10), file_added=True)
    assert [f.keywords[0] for f in new] == ["loc_file_new", "loc_prefix_new"]
    assert new[0].score == 100 and "hlx_ (300)" in new[0].text and new[0].token == "locfile:cs2:game/hlx/resource/hlx_english.txt"
    assert t.check_file("deadlock", path, tokens("Kelvin", 1900), score_override=45) == []    # deadlock non imparato
    t.learn("deadlock", {"citadel": 10})
    hero = t.check_file("deadlock", path, tokens("Kelvin", 1900), score_override=45)
    assert hero[0].score == 45

    # scansione completa dopo l'apprendimento: famiglia nuova segnalata, poi ricordata
    later = t.learn("cs2", {"csgo": 4700, "newfam": 21, "small": 3})
    assert [f.token for f in later] == ["locprefix:cs2:newfam"] and later[0].change_type == "present"
    assert t.learn("cs2", {"csgo": 4700, "newfam": 21}) == []
    assert "zeta" in t.known("cs2")                                       # sparita dalla scansione, resta nota
    db.close()


def test_github_extra_findings_uses_status_and_repo_score(tmp_path):
    cfg, db = make(tmp_path)
    src = GitHubTrackingSource(cfg, db)
    matcher = KeywordMatcher(cfg.keywords)
    LocalizationTracker(cfg, db).learn("deadlock", {"citadel": 10})
    path = "game/citadel/resource/localization/citadel_heroes/citadel_heroes_english.txt"
    lines = [("+", ln) for ln in tokens("Kelvin", 25)]
    out = src._extra_findings("deadlock", path, lines, matcher, status="modified")
    assert [f.token for f in out] == ["locprefix:deadlock:kelvin"] and out[0].score == 45   # loc_prefix_score del repo
    out = src._extra_findings("deadlock", "game/citadel/resource/localization/zeta/zeta_english.txt",
                              [("+", ln) for ln in tokens("Zeta", 30)], matcher, status="added")
    assert [f.keywords[0] for f in out] == ["loc_file_new", "loc_prefix_new"] and out[0].score == 100
    db.close()


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")
def test_full_scan_learns_then_alerts(tmp_path):
    origin = tmp_path / "origin"
    (origin / "resource").mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    (origin / "resource" / "zeta_english.txt").write_text("\n".join(['"lang"', "{", *tokens("Zeta", 25), "}"]), encoding="utf-8")
    (origin / "resource" / "zeta_italian.txt").write_text("\n".join(tokens("Zeta", 25)), encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "uno")
    cfg, db = make(tmp_path, repos=[{"name": "cs2", "repo": "SteamTracking/GameTracking-CS2", "branch": "main",
                                     "clone_url": origin.as_uri(), "appid": 730}])
    src = FullScanSource(cfg, db)
    assert src.run(force=True) == []
    assert LocalizationTracker(cfg, db).known("cs2") == {"zeta": 25}       # solo il file inglese

    (origin / "resource" / "zeta_english.txt").write_text("\n".join(tokens("Zeta", 25) + tokens("Omega", 20) + tokens("Tiny", 2)),
                                                          encoding="utf-8")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "due")
    found = src.run(force=True)
    assert [f.token for f in found] == ["locprefix:cs2:omega"]
    assert found[0].source == "fullscan" and found[0].build is not None and found[0].is_new
    assert src.run(force=True) == []
    db.close()
