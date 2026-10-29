import json

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core.pipeline import Pipeline
from hlxwatch.models import BuildInfo
from hlxwatch.reports.site import with_summaries
from hlxwatch.sources.summaries import CommitSummaries, flagged_lines, summary_title
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.storage import Database

BOT = "github-actions[bot]"
PLAIN = "Big Head Mode mutator and silent reload.\n\n### Silent reload\n- New setting \"Silent Reload Mode\"\n"
HLX = ("Engine update with editor helpers.\n\n### Engine update\n- Hammer: new `CHLXCoverPreviewHelper` (only in this game)\n"
       "- Rebuilt binaries\n")
OTHER = ("Hero rework.\n\n### Identifiers from unannounced projects\n- `npc_zeta_gunship` entity class, not used by this game\n"
         "\n### Other changes\n- New particle `hero_trail`\n")


def comment(cid, sha, body, user=BOT):
    return {"id": cid, "commit_id": sha, "body": body, "user": {"login": user},
            "html_url": f"https://github.com/SteamTracking/GameTracking-CS2/commit/{sha}#commitcomment-{cid}",
            "created_at": "2026-10-29T10:00:00Z"}


class FakeClient:
    def __init__(self):
        self.comments = {"SteamTracking/GameTracking-CS2": [comment(1, "a" * 40, PLAIN)]}
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, (params or {}).get("page")))
        repo = "/".join(path.split("/")[2:4])
        page = (params or {}).get("page", 1)
        items = self.comments.get(repo, [])
        return items[(page - 1) * 100: page * 100]


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def test_title_and_flagged_lines(tmp_path):
    cfg, db = make(tmp_path)
    m = KeywordMatcher(cfg.keywords)
    assert summary_title(PLAIN) == "Big Head Mode mutator and silent reload."
    assert flagged_lines(PLAIN, m, 90) == []
    hits = flagged_lines(HLX, m, 90)
    assert [t for t, _, _ in hits] == ["Hammer: new `CHLXCoverPreviewHelper` (only in this game)"] and hits[0][2] >= 100
    other = flagged_lines(OTHER, m, 90)
    assert [t for t, _, _ in other] == ["`npc_zeta_gunship` entity class, not used by this game"]   # solo la sua sezione
    body = "Patch.\n\n### Other gameplay\n- Crates break faster\n### Other games\n- `CZetaThing`\n"
    assert flagged_lines(body, m, 90) == [("`CZetaThing`", [], 0)]      # "Other gameplay" non è una sezione su altri giochi
    db.close()


def test_baseline_then_alerts(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    s = CommitSummaries(cfg, db, client)
    assert s.run() == []                                                   # primo giro: registra e basta
    saved = json.loads(db.get_state("summary:cs2:" + "a" * 40))
    assert saved["title"].startswith("Big Head Mode") and saved["url"].endswith("#commitcomment-1")

    client.comments["SteamTracking/GameTracking-CS2"] += [comment(2, "b" * 40, HLX), comment(3, "c" * 40, OTHER),
                                                          comment(4, "d" * 40, HLX, user="someone"), comment(5, "e" * 40, PLAIN)]
    found = s.run()
    assert [(f.change_type, f.score >= 110) for f in found] == [("summary", True), ("summary", True)]
    assert "CHLXCoverPreviewHelper" in found[0].text and "hlx_class" in found[0].keywords
    assert "npc_zeta_gunship" in found[1].text and found[1].keywords == ["steamtracking_summary"]
    assert found[0].build.url.endswith("#commitcomment-2") and all(f.is_new for f in found)
    assert db.get_state("summary:cs2:" + "d" * 40) is None                # solo i commenti del bot
    assert len(Pipeline(cfg, db).select_alerts(found)) == 2
    assert s.run() == []                                                   # già letti

    db.upsert_build(BuildInfo(game="cs2", source="github", ref="e" * 40, date=__import__("datetime").datetime.now()))
    rows = with_summaries(db, [dict(r) for r in db.recent_builds(10)])
    hit = [r for r in rows if r["ref"] == "e" * 40][0]
    assert hit["summary"]["title"].startswith("Big Head Mode")
    db.close()


def test_pages_start_near_the_end(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    client.comments["SteamTracking/GameTracking-CS2"] = [comment(i, f"{i:040d}", PLAIN) for i in range(1, 251)]
    s = CommitSummaries(cfg, db, client)
    s.run()
    assert db.get_state("summaries:cs2:count") == "250"
    client.calls.clear()
    client.comments["SteamTracking/GameTracking-CS2"].append(comment(251, "f" * 40, HLX))
    assert len(s.run()) == 1
    assert [p for path, p in client.calls if "GameTracking-CS2" in path] == [2, 3]   # non riparte dalla prima pagina
    db.close()
