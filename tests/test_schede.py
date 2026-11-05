"""Schede delle tracce HLX: una pagina per traccia, con righe vicine, comparse e link da Esiti, Ricerca e Alyx."""
import json
from datetime import datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.outcomes import outcome_register
from hlxwatch.reports.schede import (assign_slugs, context_blocks, display_label, fill_neighbours, occurrences,
                                     page_name, stored_neighbours)
from hlxwatch.reports.site import build_site
from hlxwatch.site_check import check_site
from hlxwatch.storage import Database

FGD = "game/core/ai_basenpc.fgd"
HAMMER = "game/bin/win64/tools/hammer_strings.txt"
THUMPER = '"- Thumper (HLX): causes antlions to run away briefly<br>"'
FGD_FILE = """@PointClass base(Targetname) = ai_sound
[
	soundtype(choices) : "Sound Type" =
	[
		"- Combat: Will cause most NPCs to become alert<br>" +
		"- Thumper (HLX): causes antlions to run away briefly<br>"
	]
]

@PointClass = other_entity
[
]
"""


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def build(db, game, sha, when):
    return db.upsert_build(BuildInfo(game=game, source="github", ref=sha * 40, date=when, message="1 | 2 files | x",
                                     url=f"https://github.com/SteamTracking/GameTracking-CS2/commit/{sha * 40}"))


def add(db, bid, game, path, kind, text, token=None, keywords=("hlx",), context=""):
    db.add_finding(Finding(game=game, source="github", file_path=path, change_type=kind, text=text, token=token,
                           score=100, keywords=list(keywords), context=context), bid)


def fill(db):
    b1 = build(db, "cs2", "a", datetime(2026, 9, 23, tzinfo=timezone.utc))
    b2 = build(db, "dota2", "b", datetime(2026, 10, 7, tzinfo=timezone.utc))
    b3 = build(db, "cs2", "c", datetime(2025, 7, 28, tzinfo=timezone.utc))
    add(db, b1, "cs2", HAMMER, "added", "CHLXCoverPreviewHelper", token="CHLXCoverPreviewHelper", keywords=("hlx_class",),
        context=" CHLXBase\n+CHLXCoverPreviewHelper\n CHLXZ")
    add(db, b1, "cs2", HAMMER, "added", "CDummyHLXCover", token="CDummyHLXCover", keywords=("hlx_class",))
    add(db, b2, "dota2", HAMMER, "added", "CHLXCoverPreviewHelper", token="CHLXCoverPreviewHelper", keywords=("hlx_class",))
    # trovata dall'archeologia: niente diff, solo una nota
    add(db, b3, "cs2", FGD, "added", THUMPER, token="HLX", context="archeologia: dalla storia completa del repository")
    db.set_snapshot("fullscan:cs2", "x", f"{HAMMER}\tCHLXCoverPreviewHelper\n{FGD}\t{THUMPER}")


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, []

    def raw_file(self, repo, sha, path, max_bytes):
        self.calls.append((repo, sha, path))
        return self.text


def test_slugs_are_stable_unique_and_short():
    traces = [{"key": "chlxcoverpreviewhelper"}, {"key": "mod != hlx"}, {"key": "mod-hlx"}, {"key": "x" * 80}]
    slugs = assign_slugs(traces)
    assert slugs["chlxcoverpreviewhelper"] == "chlxcoverpreviewhelper"
    assert slugs["mod != hlx"] != slugs["mod-hlx"] and slugs["mod != hlx"].startswith("mod-hlx-")
    assert len(slugs["x" * 80]) <= 57 and traces[0]["slug"] == "chlxcoverpreviewhelper"
    assert assign_slugs([dict(t) for t in traces]) == slugs              # stessi nomi al giro dopo
    assert page_name("abc") == "traccia-abc.html"
    assert display_label(THUMPER) == "Thumper (HLX): causes antlions to run away briefly"


def test_context_blocks_skip_notes_and_warn_on_dumps():
    dump = {"change_type": "added", "file_path": HAMMER, "context": " A\n+CHLXCover\n B", "game": "cs2", "day": "2026-09-23"}
    note = {"change_type": "added", "file_path": FGD, "context": "archeologia: dalla storia", "game": "cs2", "day": "2025-07-28"}
    blocks, only_dumps = context_blocks([dump, note])
    assert len(blocks) == 1 and only_dumps and blocks[0]["lines"][1] == {"mark": "+", "text": "CHLXCover", "hlx": True}
    stored = {"game": "cs2", "day": "2025-07-28", "file_path": FGD, "url": "u", "lines": ["@PointClass", THUMPER]}
    blocks, only_dumps = context_blocks([dump, note], stored)
    assert not only_dumps and [b["file"] for b in blocks] == [FGD] and blocks[0]["source"] == "file"


def test_fill_neighbours_downloads_once(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    client = FakeClient(FGD_FILE)
    assert fill_neighbours(cfg, db, client) == 1                      # solo il FGD: gli altri hanno il diff o sono dump
    assert client.calls[0][0] == "SteamTracking/GameTracking-CS2" and client.calls[0][2] == FGD
    key = next(t["key"] for t in outcome_register(cfg, db)["traces"] if "thumper" in t["key"])
    lines = stored_neighbours(db, key)["lines"]
    assert lines[0].startswith("@PointClass base") and "Thumper" in lines[-3] and "other_entity" not in "".join(lines)
    assert fill_neighbours(cfg, db, client) == 0 and len(client.calls) == 1
    db.close()


def test_fill_neighbours_remembers_misses(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    client = FakeClient("")                                           # file sparito: niente righe, ma non si riprova
    assert fill_neighbours(cfg, db, client) == 1 and fill_neighbours(cfg, db, client) == 0
    db.close()


def test_site_writes_cards_and_links(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    fill_neighbours(cfg, db, FakeClient(FGD_FILE))
    out = build_site(db, cfg, tmp_path / "site")
    cover = (out / "traccia-chlxcoverpreviewhelper.html").read_text(encoding="utf-8")
    assert "pezzo di gioco" in cover and "presente oggi" in cover and "Dota 2" in cover
    assert 'href="traccia-cdummyhlxcover.html"' in cover               # comparsa lo stesso giorno nello stesso file
    assert "ordine alfabetico" in cover                                 # le righe vicine vengono da un dump
    thumper = next(out.glob("traccia-thumper-*.html")).read_text(encoding="utf-8")
    assert "righe del file a quel commit" in thumper and "@PointClass base(Targetname)" in thumper
    assert "&lt;br&gt;</code></h1>" not in thumper and "Thumper (HLX): causes antlions to run away briefly</code></h1>" in thumper
    esiti = (out / "esiti.html").read_text(encoding="utf-8")
    assert 'href="traccia-chlxcoverpreviewhelper.html"' in esiti
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert {r.get("tc") for r in data if r.get("token") == "CHLXCoverPreviewHelper"} == {"chlxcoverpreviewhelper"}
    assert not [p for p in check_site(out, need_preview=False) if "link" in p["testo"]]
    # una traccia che non c'è più: la sua vecchia pagina sparisce al giro dopo
    (out / "traccia-vecchia.html").write_text("<html></html>", encoding="utf-8")
    build_site(db, cfg, out)
    assert not (out / "traccia-vecchia.html").exists()
    db.close()


def test_occurrences_only_hlx_rows(tmp_path):
    cfg, db = make(tmp_path)
    fill(db)
    add(db, None, "cs2", HAMMER, "added", "CHLXCoverPreviewHelper", token="CHLXCoverPreviewHelper", keywords=("other",))
    found = occurrences(db, {"chlxcoverpreviewhelper"})
    assert [r["game"] for r in found["chlxcoverpreviewhelper"]] == ["cs2", "dota2"]
    db.close()


def test_alerts_link_to_card(tmp_path):
    from hlxwatch.notifiers.base import AlertGroup, finding_line
    from hlxwatch.notifiers.webpush import WebPushNotifier
    from hlxwatch.reports.schede import annotate_cards
    cfg, db = make(tmp_path)
    fill(db)
    hlx = Finding(game="cs2", source="github", file_path=HAMMER, change_type="added", text="CDummyHLXCover",
                  token="CDummyHLXCover", score=120, keywords=["hlx_class"])
    other = Finding(game="cs2", source="github", file_path=HAMMER, change_type="added", text="weapon_x",
                    token="weapon_x", score=60, keywords=["weapon"])
    assert annotate_cards(cfg, db, [hlx, other]) == 1
    assert hlx.card.endswith("/traccia-cdummyhlxcover.html") and hlx.card.startswith("https://") and other.card == ""
    assert '· <a href="' + hlx.card + '">scheda</a>' in finding_line(hlx, "html")
    assert f"[scheda]({hlx.card})" in finding_line(hlx, "md") and "scheda" not in finding_line(other, "html")
    msg = WebPushNotifier.message([AlertGroup("cs2", "Counter-Strike 2", [hlx], 120, 1)], "https://x/")
    assert msg["url"] == hlx.card                                       # la notifica apre la scheda
    assert annotate_cards(cfg, db, [other]) == 0
    db.close()
