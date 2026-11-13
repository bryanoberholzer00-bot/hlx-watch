import json
from types import SimpleNamespace

import yaml

from hlxwatch import fase
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.core import Pipeline
from hlxwatch.lezioni import LEARNED, Checker, learned, run_check, static_lessons
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.revisione import (CORRECTIONS, PROPOSALS, apply_decisions, corrections, proposals, run_review,
                                silenced)
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, utcnow
from hlxwatch.weekly import last_week


def make(tmp_path, config="config.actions.yaml"):
    raw = yaml.safe_load((PACKAGE_ROOT / config).read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    for key in ("telegram", "discord", "webpush"):
        raw.setdefault("notifiers", {}).setdefault(key, {})["enabled"] = False
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


# ------------------------------------------------------------------ registro degli errori
def test_every_lesson_holds_with_the_published_rules():
    for config in ("config.actions.yaml", "config.example.yaml"):
        raw = yaml.safe_load((PACKAGE_ROOT / config).read_text(encoding="utf-8"))
        result = Checker(Config(raw, PACKAGE_ROOT / "x.yaml")).run()
        assert result["n"] == len(static_lessons()) >= 15
        assert result["falliti"] == [], (config, result["falliti"])


def test_a_mistake_that_comes_back_is_caught_and_reported_once(tmp_path):
    cfg, db = make(tmp_path)

    class Fake:
        name = "fake"
        sent = []

        def send_text(self, title, text):
            self.sent.append((title, text))
            return True

    fake = Fake()
    assert run_check(cfg, db, [fake])["ok"] and fake.sent == []
    db.set_state(CORRECTIONS, json.dumps({"tipi": {"hlx": "alyx"}}))       # torna l'errore di "mod != hlx"
    result = run_check(cfg, db, [fake])
    assert {f["id"] for f in result["falliti"]} == {"mod-hlx-motore", "suppressexpr-hlx-motore"}
    assert len(fake.sent) == 1 and "mod != hlx" in fake.sent[0][1] and "tipo alyx invece di motore" in fake.sent[0][1]
    run_check(cfg, db, [fake])                                               # stesso errore: nessun avviso nuovo
    assert len(fake.sent) == 1
    db.set_state(CORRECTIONS, "{}")
    assert run_check(cfg, db, [fake])["ok"] and len(fake.sent) == 2 and "di nuovo" in fake.sent[1][0]
    fase.set_overrides({})
    db.close()


# ------------------------------------------------------------------ revisione settimanale
class FakeClient:
    """Il modello finto: restituisce le proposte date, nel formato della risposta dell'API."""

    def __init__(self, proposte):
        self.proposte = proposte
        self.prompts = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.prompts.append(kw)
        text = json.dumps({"proposte": self.proposte})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200), model="claude-opus-5-5")


class Resp:
    def __init__(self, data):
        self.data, self.status_code = data, 200

    def json(self):
        return self.data


class Bot:
    """Il bot su Cloudflare finto: voti 👎 e scelte sulle proposte."""

    def __init__(self, votes=(), decisions=None):
        self.votes, self.decisions = votes, decisions or {}

    def get(self, url, timeout=None, headers=None):
        if url.endswith("/rumore.json"):
            return Resp({"voti": {str(v): "2026-11-05" for v in self.votes}})
        return Resp({"decisioni": self.decisions})


class Telegram:
    name = "telegram"

    def __init__(self):
        self.sent, self.texts = [], []

    def send_buttons(self, title, lines, markup):
        self.sent.append((title, lines, markup))
        return True

    def send_text(self, title, text):
        self.texts.append((title, text))
        return True


def fill(db):
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="abc", date=utcnow(),
                                  url="https://github.com/SteamTracking/GameTracking-CS2/commit/abc"))
    ids = {}
    for name, f in {
        "motore": Finding(game="cs2", source="github", file_path="game/csgo/pak01_dir/particles.txt", change_type="added",
                          text="hlx_spark_particle_heavy", token="hlx_spark_particle_heavy", score=110, keywords=["hlx_prefix"]),
        "rumore": Finding(game="steamvr", source="github", file_path="drivers/frame_controller/resources/input/x.json",
                          change_type="added", text='"binding_url": "doomvfr_bindings_frame_controller.json"', score=60,
                          keywords=["watched_file"]),
    }.items():
        ids[name] = db.add_finding(f, b).id
    db.mark_notified(ids.values())
    return ids


def test_review_proposes_checks_and_applies_only_what_is_approved(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "finta")
    cfg, db = make(tmp_path)
    ids = fill(db)
    monday, _ = last_week()
    db.set_state("weekly:last", monday.isoformat())
    good = [
        {"azione": "tipo", "id": ids["motore"], "tipo": "gioco", "esito": "", "nota": "",
         "perche": "È la scia di un proiettile pesante: un pezzo di gioco."},
        {"azione": "silenzio", "id": ids["rumore"], "tipo": "", "esito": "", "nota": "",
         "perche": "Comandi di DOOM VFR, un gioco di terze parti, segnati con 👎."},
        {"azione": "nota", "id": ids["motore"], "tipo": "", "esito": "spostata nel motore", "nota": "Oggi esiste con un altro prefisso.",
         "perche": "La stessa riga è comparsa in core."},
    ]
    bad = [
        {"azione": "silenzio", "id": ids["motore"], "tipo": "", "esito": "", "nota": "", "perche": "Traccia HLX: mai."},
        {"azione": "tipo", "id": ids["rumore"], "tipo": "gioco", "esito": "", "nota": "", "perche": "Non è HLX."},
        {"azione": "tipo", "id": 999999, "tipo": "gioco", "esito": "", "nota": "", "perche": "Voce inventata."},
        {"azione": "tipo", "id": ids["motore"], "tipo": "motore", "esito": "", "nota": "", "perche": "Già così."},
    ]
    client = FakeClient(bad + good)
    telegram = Telegram()
    found = run_review(cfg, db, [telegram], client=client, session=Bot(votes=[ids["rumore"]]))
    assert [p["azione"] for p in found] == ["tipo", "silenzio", "nota"]
    data = json.loads(client.prompts[0]["messages"][0]["content"].split("<dati>\n", 1)[1].rsplit("\n</dati>", 1)[0])
    by_id = {a["id"]: a for a in data["avvisi"]}
    assert by_id[ids["rumore"]]["voto_rumore"] is True and by_id[ids["motore"]]["traccia_hlx"] is True
    assert by_id[ids["rumore"]]["gia_silenziato"] is False
    assert data["errori_gia_corretti"]                                   # il modello vede gli errori già corretti
    title, lines, markup = telegram.sent[0]
    assert "revisione della settimana" in title
    pids = [p["pid"] for p in found]
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"p:{pids[0]}:s"
    assert run_review(cfg, db, [telegram], client=client, session=Bot()) is None   # una volta a settimana

    # il proprietario approva il tipo e il silenzio, scarta la nota
    bot = Bot(decisions={pids[0]: {"d": "s"}, pids[1]: {"d": "s"}, pids[2]: {"d": "n"}})
    assert apply_decisions(cfg, db, [telegram], session=bot) == 2
    assert [p["stato"] for p in proposals(db)] == ["applicata", "applicata", "rifiutata"]
    assert fase.trace_kind("hlx_spark_particle_heavy", None, ["hlx_prefix"]) == "gioco"
    noise_like = Finding(game="steamvr", source="github", file_path="drivers/frame_controller/resources/input/y.json",
                         change_type="added", text="altro", score=60, keywords=["watched_file"], is_new=True)
    assert silenced(db, noise_like)
    assert Pipeline(cfg, db).select_alerts([noise_like]) == []
    assert "correzioni applicate" in telegram.texts[0][0]
    # le correzioni approvate diventano esempi del registro, e il registro le rispetta
    assert {x["controllo"] for x in learned(db)} == {"tipo", "avviso"}
    result = Checker(cfg, db).run()
    assert result["ok"] and result["imparate"] == 2
    # una nuova revisione non ripropone ciò che è già stato applicato
    db.set_state("revisione:ultima", "")
    again = run_review(cfg, db, [], client=FakeClient(good), session=Bot(), send_it=False)
    assert [p["azione"] for p in again] == ["nota"]
    fase.set_overrides({})
    db.close()


def test_strong_hlx_traces_cannot_be_silenced_even_by_hand(tmp_path):
    cfg, db = make(tmp_path)
    ids = fill(db)
    row = db.conn.execute("SELECT * FROM findings WHERE id = ?", (ids["motore"],)).fetchone()
    from hlxwatch import noise
    rule = noise.rule_of(row["game"], row["change_type"], json.loads(row["keywords"]), row["file_path"])
    db.set_state(CORRECTIONS, json.dumps({"silenzi": {rule: {"descrizione": "x"}}}))
    hlx = Finding(game="cs2", source="github", file_path="game/csgo/pak01_dir/particles.txt", change_type="added",
                  text="hlx_new_thing", token="hlx_new_thing", score=110, keywords=["hlx_prefix"], is_new=True)
    assert not silenced(db, hlx)
    assert Pipeline(cfg, db).select_alerts([hlx]) == [hlx]
    db.close()


def test_unanswered_proposals_expire(tmp_path):
    cfg, db = make(tmp_path)
    old = iso(utcnow().replace(year=utcnow().year - 1))
    db.set_state(PROPOSALS, json.dumps([{"pid": "p2611091", "azione": "tipo", "chiave": "x", "tipo": "gioco",
                                         "prima": "motore", "voce": 1, "esempio": "x", "gioco": "CS2", "perche": "p",
                                         "stato": "attesa", "creata": old}]))
    assert apply_decisions(cfg, db, [], session=Bot()) == 0
    assert proposals(db)[0]["stato"] == "scaduta" and corrections(db)["tipi"] == {}
    assert db.get_state(LEARNED) in (None, "", "[]")
    db.close()


# ------------------------------------------------------------------ controllo a conteggio
def test_repeated_alerts_without_hlx_are_proposed_for_silence(tmp_path):
    from hlxwatch.revisione import check_noise, noisy_groups
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="net", date=utcnow()))
    ids = []
    for n in range(4):                       # quattro impostazioni di rete in gameinfo.gi: rumore
        ids.append(db.add_finding(Finding(game="cs2", source="github", file_path="game/csgo/gameinfo.gi",
                                          change_type="added", text=f'"net_limit_{n}" "128"', score=50,
                                          keywords=["watched_file"]), b).id)
    for n in range(5):                       # cinque tracce HLX: mai
        ids.append(db.add_finding(Finding(game="cs2", source="github", file_path="game/csgo/pak01_dir/x.txt",
                                          change_type="added", text=f"hlx_cosa_{n}", token=f"hlx_cosa_{n}", score=110,
                                          keywords=["hlx_prefix"]), b).id)
    for n in range(3):                       # tre build di Alyx: tenute apposta (esclusi nel config)
        ids.append(db.add_finding(Finding(game="alyx", source="steam_pics", file_path="depots/branches/public",
                                          change_type="build", text=f"alyx {n}", score=60, keywords=["steam_build"]), b).id)
    for n in range(2):                       # due build: sotto la soglia
        ids.append(db.add_finding(Finding(game="dota2", source="steam_pics", file_path="depots/branches/public",
                                          change_type="build", text=f"build {n}", score=60, keywords=["steam_build"]), b).id)
    db.add_finding(Finding(game="cs2", source="github", file_path="game/csgo/gameinfo.gi", change_type="added",
                           text='"net_mai_inviata" "1"', score=40, keywords=["watched_file"]), b)   # non mandata
    db.mark_notified(ids)
    found = noisy_groups(cfg, db)
    assert [(p["gioco"], p["avvisi"]) for p in found] == [("Counter-Strike 2", 4)]
    assert "game/csgo/gameinfo.gi" in found[0]["perche"] and "nessuno con parole HLX" in found[0]["perche"]
    assert check_noise(cfg, db, [], send_it=False) and proposals(db) == []        # anteprima: niente salvato
    telegram = Telegram()
    sent = check_noise(cfg, db, [telegram])
    pid = sent[0]["pid"]
    title, lines, markup = telegram.sent[0]
    assert "falsi allarmi" in title and markup["inline_keyboard"][0][1]["callback_data"] == f"p:{pid}:n"
    import re
    assert re.fullmatch(r"p\d{6,8}\d{1,2}", pid)                                  # la forma che il bot accetta
    assert check_noise(cfg, db, [telegram]) == []                                 # già in attesa: non si ripete
    # rifiutata: non si ripropone più; approvata: silenzia e diventa un esempio del registro
    assert apply_decisions(cfg, db, [telegram], session=Bot(decisions={pid: {"d": "n"}})) == 0
    assert proposals(db)[0]["stato"] == "rifiutata" and noisy_groups(cfg, db) == []
    db.set_state(PROPOSALS, json.dumps([{**proposals(db)[0], "stato": "attesa"}]))
    assert apply_decisions(cfg, db, [telegram], session=Bot(decisions={pid: {"d": "s"}})) == 1
    noisy = Finding(game="cs2", source="github", file_path="game/csgo/gameinfo.gi", change_type="added",
                    text='"net_altra" "1"', score=50, keywords=["watched_file"], is_new=True)
    assert Pipeline(cfg, db).select_alerts([noisy]) == []
    assert Checker(cfg, db).run()["ok"]
    db.close()
