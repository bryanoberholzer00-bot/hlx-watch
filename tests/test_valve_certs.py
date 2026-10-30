import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.evidence import area_of
from hlxwatch.reevaluate import reevaluate
from hlxwatch.sources.valve_certs import ValveCertsSource, base_domain
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["valve_certs"]["domains"] = ["half-life.com"]
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeSession:
    """Cert Spotter finto: certificati in ordine di scoperta, pagine da 2, `after` = id dell'ultimo letto."""

    def __init__(self):
        self.certs = []
        self.headers = {}
        self.calls = 0
        self.limited = False

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.limited:
            return Resp({"code": "rate_limited"}, 429)
        after = int(params.get("after") or 0)
        return Resp([c for c in self.certs if c["id"] > after][:2])


def cert(i, *names):
    return {"id": i, "not_before": "2026-10-30T10:00:00Z", "dns_names": list(names)}


GAMES = ["*.beta.half-life.com", "beta.half-life.com", "*.beta.playdeadlock.com", "beta.playdeadlock.com",
         "*.preview.steamdeck.com"]


def test_new_domain_in_valve_certificate(tmp_path):
    assert base_domain("*.beta.half-life.com") == "half-life.com"
    cfg, db = make(tmp_path)
    s = FakeSession()
    s.certs = [cert(1, *GAMES), cert(2, *GAMES), cert(3, "half-life.com", "www.half-life.com")]
    src = ValveCertsSource(cfg, db, session=s)
    assert src.run() == []                          # prima lettura (3 certificati in 2 pagine): solo registrazione
    assert src.run() == []                          # entro 3 ore non richiede niente
    calls = s.calls
    s.certs.append(cert(4, *GAMES, "*.beta.halflife-x.com", "beta.halflife-x.com", "*.beta.newgame.com",
                        "store.half-life.com"))
    found = {f.file_path: f for f in src.run(force=True)}
    assert set(found) == {"halflife-x.com", "newgame.com", "store.half-life.com"}
    assert found["newgame.com"].score == 120
    assert found["halflife-x.com"].score >= 120                 # con Half-Life nel nome: possibile annuncio
    assert found["store.half-life.com"].score < 50              # sottodominio di un dominio noto: solo archivio
    assert area_of("newgame.com", "domain") == "web"
    assert s.calls - calls == 2                                  # solo il certificato nuovo (after) e la pagina vuota
    assert src.run(force=True) == []                            # nessun certificato nuovo
    reevaluate(cfg, db)
    assert db.conn.execute("SELECT COUNT(*) FROM findings WHERE game = 'valveweb'").fetchone()[0] == 3
    assert cfg.game_title("valveweb") == "Siti di Valve (certificati)"
    db.close()


def test_rate_limit_keeps_the_first_reading_silent(tmp_path):
    cfg, db = make(tmp_path)
    s = FakeSession()
    s.certs = [cert(1, *GAMES), cert(2, "a.half-life.com"), cert(3, "*.beta.otherold.com")]
    src = ValveCertsSource(cfg, db, session=s)
    src.fetch = lambda domain, after, _orig=src.fetch: (_orig(domain, after)[0][:2], False) if not after else _orig(domain, after)
    assert src.run() == []                          # prima lettura interrotta a metà
    src.fetch = ValveCertsSource.fetch.__get__(src)
    assert src.run() == []                          # il resto della prima lettura: ancora nessun avviso
    s.limited = True
    assert src.run(force=True) == [] and not src.errors       # limite di richieste: si riprova, non è un errore
    db.close()


def test_methodology_lists_the_new_sources(tmp_path):
    from hlxwatch.reports.site import build_site
    cfg, db = make(tmp_path)
    met = (build_site(db, cfg, tmp_path / "s") / "metodologia.html").read_text(encoding="utf-8")
    assert "Cert Spotter" in met and "App Steam nel codice di Proton" in met
    db.close()
