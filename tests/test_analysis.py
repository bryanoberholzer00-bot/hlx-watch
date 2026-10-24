import json
from datetime import datetime, timezone
from types import SimpleNamespace

import yaml

from hlxwatch.analysis import Analyzer, cost_of, telegram_text
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.storage import Database

ANSWER = {
    "titolo": "Nuova proprietà riservata a hlx nelle particelle",
    "sintesi": "Valve ha aggiunto una ConVar hlx_.",
    "novita_rilevanti": True,
    "fatti": [{"testo": "Compare hlx_test_convar in CS2.", "prove": ["E1"]}],
    "ipotesi": [{"titolo": "Lavoro attivo su hlx", "spiegazione": "Le aggiunte continuano.", "fiducia": "media",
                 "prove": ["E1"]}],
    "da_osservare": ["Prossimo aggiornamento di Deadlock"],
    "rumore": ["Spostamenti di formato dei dump"],
}


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", model=kwargs["model"],
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(ANSWER))],
            usage=SimpleNamespace(input_tokens=15000, output_tokens=3000, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0),
        )


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    db = Database(cfg.db_path)
    build = BuildInfo(game="cs2", source="github", ref="abc", date=datetime.now(timezone.utc),
                      url="https://github.com/SteamTracking/GameTracking-CS2/commit/abc")
    bid = db.upsert_build(build)
    db.add_finding(Finding(game="cs2", source="github", file_path="convars.txt", change_type="added",
                           text='hlx_test_convar "1"', score=125, keywords=["hlx_prefix"], token="hlx_test_convar"), bid)
    return cfg, db


def test_analysis_call_store_and_site(tmp_path):
    cfg, db = make(tmp_path)
    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    content = Analyzer(cfg, db, client=fake).run(force=True)
    call = fake.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in call and "temperature" not in call
    sent = json.loads(call["messages"][0]["content"].split("\n\n", 1)[1])
    assert sent["novita"][0]["id"] == "E1" and sent["novita"][0]["token"] == "hlx_test_convar"

    assert content["titolo"] == ANSWER["titolo"]
    assert content["evidenze"]["E1"]["commit"].endswith("/commit/abc")
    assert db.latest_analysis() is not None
    assert abs(db.analyses_cost_since(datetime(2000, 1, 1, tzinfo=timezone.utc)) - cost_of(SimpleNamespace(
        input_tokens=15000, output_tokens=3000))) < 1e-9
    assert "analisi.html" in telegram_text(content)

    out = build_site(db, cfg, tmp_path / "site")
    index = (out / "index.html").read_text(encoding="utf-8")
    page = (out / "analisi.html").read_text(encoding="utf-8")
    assert ANSWER["titolo"] in index and "Lavoro attivo su hlx" in page
    assert 'href="https://github.com/SteamTracking/GameTracking-CS2/commit/abc"' in page

    # stesso giorno, senza force: nessuna nuova chiamata
    Analyzer(cfg, db, client=fake).run()
    assert len(fake.beta.messages.calls) == 1
    db.close()


def test_budget_blocks_calls(tmp_path):
    cfg, db = make(tmp_path)
    cfg.raw["ai"]["monthly_budget_usd"] = 0.01
    db.add_analysis("claude-opus-5-5", None, None, {"titolo": "x"}, 0.5)
    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    assert Analyzer(cfg, db, client=fake).run(force=True) is None
    assert fake.beta.messages.calls == []
    db.close()
