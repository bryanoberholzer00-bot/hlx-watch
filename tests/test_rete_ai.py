"""Seconda rete con l'AI (rete_ai.py): scelta delle righe, risposta validata, voci, avvisi e tetti di spesa."""
import json
from datetime import timedelta
from types import SimpleNamespace

import yaml

from hlxwatch import archivio_righe as ar
from hlxwatch import rete_ai
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

CONVARS = "DumpSource2/convars.txt"


def make(tmp_path, **net):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw.setdefault("rete_ai", {}).update(net)
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def build(db, game, sha, hours_ago):
    return db.upsert_build(BuildInfo(game=game, source="github", ref=sha * 40, date=utcnow() - timedelta(hours=hours_ago),
                                     message="x", url=f"https://github.com/SteamTracking/X/commit/{sha * 40}"))


STREAM = "save_streamed_out_level_async true (developmentonly gamedll clientdll replicated defensive)"
DESC = "A level streaming out encodes and writes its .hl1 (server) and .hl2 (client) on a save thread"
PLAIN = [("+", "companion_follow_distance 128 (gamedll cheat)"),
         ("+", "How far behind the player the companion walks when following"),
         ("+", "npc_cover_preview_radius 32 (cheat)"),
         ("+", "r_gpu_profiler_window 1 (developmentonly)"),
         ("+", "dota_hero_respawn_time 5 (cheat)"),                 # del gioco: fuori
         ("+", "Hero respawn time"),                                 # sua descrizione: fuori
         ("+", "sc_old_line 1 (developmentonly)")]                   # già vista in un altro gioco: fuori


class FakeClient:
    def __init__(self, voci, riassunto="streaming dei livelli"):
        self.voci, self.riassunto, self.calls = voci, riassunto, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps({"voci": self.voci, "riassunto": self.riassunto})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=5000, output_tokens=300), model="claude-opus-5-5")


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.groups = []

    def send_groups(self, heading, groups):
        self.groups.append((heading, groups))
        return True

    def send_text(self, title, text):
        return True


def fill(db):
    old = build(db, "cs2", "a", 30)
    ar.store(db, old, {CONVARS: [("+", "sc_old_line 1 (developmentonly)")]})
    new = build(db, "dota2", "b", 2)
    ar.store(db, new, {CONVARS: PLAIN, "game/dota/bin/win64/server_strings.txt": [("+", "SaveStreamedOutLevel write")]})
    return new


def test_candidates_drop_game_lines_seen_lines_and_dumps(tmp_path):
    cfg, db = make(tmp_path)
    bid = fill(db)
    rows = [i["riga"] for i in rete_ai.candidates(db, bid, cfg)]
    assert rows[0].startswith("companion_follow_distance") and rows[1].startswith("How far behind")
    assert "npc_cover_preview_radius 32 (cheat)" in rows and "r_gpu_profiler_window 1 (developmentonly)" in rows
    assert not any("dota_hero" in r or "Hero respawn" in r or "sc_old_line" in r for r in rows)
    assert not any("SaveStreamedOutLevel" in r for r in rows)            # i dump non vanno a Claude
    db.close()


def test_candidates_skip_lines_already_alerting(tmp_path):
    cfg, db = make(tmp_path)
    bid = build(db, "dota2", "c", 1)
    ar.store(db, bid, {CONVARS: [("+", STREAM), ("+", DESC), ("+", "r_new_thing 1 (developmentonly)")]})
    rows = [i["riga"] for i in rete_ai.candidates(db, bid, cfg)]
    assert rows == ["r_new_thing 1 (developmentonly)"]                   # lo streaming è già un avviso (single_player)
    db.close()


def test_run_makes_findings_validates_answer_and_alerts_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    cfg, db = make(tmp_path)
    fill(db)
    client = FakeClient([
        {"riga": "companion_follow_distance 128 (gamedll cheat)", "file": CONVARS,
         "sistema": "personaggio che accompagna", "perche": "un compagno che segue il giocatore", "fiducia": "alta"},
        {"riga": "npc_cover_preview_radius 32 (cheat)", "file": "sbagliato.txt",
         "sistema": "coperture dei nemici", "perche": "nemici che usano i ripari", "fiducia": "media"},
        {"riga": "una riga inventata", "file": CONVARS, "sistema": "x", "perche": "y", "fiducia": "alta"},
    ])
    n = FakeNotifier()
    res = rete_ai.run(cfg, db, [n], client=client)
    assert res["chiamate"] == 1 and res["voci"] == 2                      # la riga inventata è scartata
    prompt = client.calls[0]["messages"][0]["content"]
    assert "<dati>" in prompt and "dota_hero" not in prompt and client.calls[0]["system"] == rete_ai.SYSTEM
    rows = db.conn.execute("SELECT file_path, score, source, context FROM findings WHERE source = 'rete_ai' "
                           "ORDER BY score DESC").fetchall()
    assert [(r["score"], r["file_path"]) for r in rows] == [(60, CONVARS), (45, CONVARS)]   # file corretto
    assert "personaggio che accompagna" in rows[0]["context"]
    assert len(n.groups) == 1 and "seconda rete" in n.groups[0][0]        # solo la fiducia alta fa avviso
    assert rete_ai.run(cfg, db, [n], client=client)["chiamate"] == 0      # un commit si guarda una volta
    db.close()


def test_run_respects_daily_cap_and_skips_old_and_alyx(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    cfg, db = make(tmp_path, chiamate_al_giorno=1)
    for sha, game, hours in (("d", "dota2", 1), ("e", "cs2", 2), ("f", "alyx", 3), ("g", "deadlock", 24 * 10)):
        ar.store(db, build(db, game, sha, hours), {CONVARS: [("+", f"r_new_{sha}_{i} 1 (developmentonly)") for i in range(5)]})
    client = FakeClient([])
    res = rete_ai.run(cfg, db, [], client=client)
    assert res["chiamate"] == 1 and len(client.calls) == 1               # tetto di una chiamata al giorno
    pend = [r["game"] for r in rete_ai.pending(cfg, db)]
    assert pend == ["dota2"] or pend == ["cs2"]                           # né Alyx né il commit di 10 giorni fa
    db.close()


def test_run_without_key_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg, db = make(tmp_path)
    fill(db)
    assert rete_ai.run(cfg, db, [])["chiamate"] == 0
    db.close()


def test_preview_all_lines_includes_alerting_ones(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg, db = make(tmp_path)
    bid = build(db, "dota2", "h", 1)
    ar.store(db, bid, {CONVARS: [("+", STREAM), ("+", DESC), ("+", "r_new_thing 1 (developmentonly)")]})
    out = rete_ai.preview(cfg, db, "h" * 10, all_lines=True)
    assert out["righe"] == 3 and out["esempi"][0]["riga"] == STREAM and "risposta" not in out   # senza chiave: niente AI
    assert rete_ai.preview(cfg, db, "h" * 10)["righe"] == 1
    assert "errore" in rete_ai.preview(cfg, db, "zzzz")
    db.close()
