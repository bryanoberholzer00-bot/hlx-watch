import yaml

from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.analyzers.codenames import CodenameTracker, detect
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.sources.github_tracking import GitHubTrackingSource
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_detect_places():
    assert detect('// MPropertySuppressExpr = "mod != csgo && mod != hlx"') == [
        ("mod", "csgo", "espressione mod != / == degli strumenti"), ("mod", "hlx", "espressione mod != / == degli strumenti")]
    assert [n for _, n, _ in detect(r"G:\dev\source2\main\content\hlx\materials\x.exr")] == ["hlx"]
    br = "game/core/tools/bugreporter/config.txt"
    assert [n for _, n, _ in detect('\t\t"product"\t\t"Half-Life VR"', br)] == ["half-life vr"]
    assert [n for _, n, _ in detect('\t"RPG"', br)] == ["rpg"]
    assert detect('\t"RPG"', "game/other.txt") == []                 # le sezioni contano solo nel bug reporter
    assert detect('\t\t"component"\t\t"other"', br) == []           # parola generica
    assert [n for _, n, _ in detect('\t\t"mod"\t"hl3"', "game/core/pak01_dir/source1import_txtmap.txt")] == ["hl3"]
    assert [n for _, n, _ in detect('"mod"\t"left4dead2_source2"')] == ["left4dead2_source2"]


def test_learning_then_alert(tmp_path):
    cfg, db = make(tmp_path)
    t = CodenameTracker(cfg, db)
    assert t.check("mod != hlx", "a.h", game="cs2") is None            # noto dal config
    assert t.check("mod != zeta", "a.h", game="cs2", change_type="present") is None   # apprendimento: imparato in silenzio
    t.finish_learning()
    assert CodenameTracker(cfg, db).check("mod != zeta", "a.h", game="cs2") is None    # ricordato tra un run e l'altro
    f = CodenameTracker(cfg, db).check("mod != omega && mod != hlx", "a.h", game="dota2")
    assert f is not None and f.change_type == "codename" and f.token == "codename:omega" and f.score == 120
    assert CodenameTracker(cfg, db).check("mod != omega", "b.h", game="cs2") is None   # una volta sola
    db.close()


def test_watched_file_lines(tmp_path):
    cfg, db = make(tmp_path)
    src = GitHubTrackingSource(cfg, db)
    matcher = KeywordMatcher(cfg.keywords)
    lines = [("+", '\t"Zeta"'), ("+", "\t{"), ("+", '\t\t"product"\t\t"zeta"'), ("+", '\t\t"component"\t\t"hlx"'),
             ("-", '\t\t"version"\t\t"4"'), ("+", "\t}")]
    db.set_state("codenames:learned", "1")
    src.codenames = type(src.codenames)(cfg, db)
    out = src._extra_findings("cs2", "game/core/tools/bugreporter/config.txt", lines, matcher)
    kinds = [(f.change_type, f.keywords[0]) for f in out]
    assert ("codename", "codename_new") in kinds                      # "Zeta" mai visto
    watched = [f for f in out if f.keywords == ["watched_file"]]
    assert [f.text for f in watched] == ['"Zeta"', '"product"\t\t"zeta"', '"version"\t\t"4"']    # niente parentesi né righe con hlx
    assert all(f.score == 60 for f in watched) and watched[2].change_type == "removed"
    assert src._extra_findings("cs2", "game/csgo/x.txt", [("+", "qualsiasi riga")], matcher) == []
    db.close()
