import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.evidence import area_of
from hlxwatch.reevaluate import reevaluate
from hlxwatch.sources.valve_github import ValveGitHubSource
from hlxwatch.storage import Database


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def repo(name, pushed, created="2020-01-01T00:00:00Z", description=""):
    return {"full_name": f"ValveSoftware/{name}", "name": name, "pushed_at": pushed, "created_at": created,
            "description": description, "default_branch": "master", "html_url": f"https://github.com/ValveSoftware/{name}"}


def commit(sha, message, date="2026-10-30T10:00:00Z"):
    return {"sha": sha, "html_url": f"https://github.com/x/commit/{sha}",
            "commit": {"message": message, "committer": {"date": date}}}


class FakeClient:
    def __init__(self):
        self.repos = []
        self.commits = {}
        self.asked = []

    def get_json(self, path, params=None):
        return self.repos if params.get("page") == 1 else []

    def raw_file(self, repo, sha, path, max_bytes):
        return '"1313860",'

    def list_commits(self, repo, branch, *, since=None, max_pages=60, **_):
        self.asked.append((repo, branch, since))
        return self.commits.get(repo, [])


def test_first_run_only_records_then_reports_new_repos_and_commits(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    client.repos = [repo("Proton", "2026-10-29T00:00:00Z"), repo("halflife", "2025-01-01T00:00:00Z")]
    client.commits["ValveSoftware/Proton"] = [commit("a1", "update wine")]
    src = ValveGitHubSource(cfg, db, client)
    assert src.run() == []                     # primo giro: niente voci, nessun commit chiesto
    assert client.asked == []

    client.repos = [repo("Proton", "2026-10-30T12:00:00Z"), repo("halflife", "2025-01-01T00:00:00Z"),
                    repo("hlx-tools", "2026-10-30T11:00:00Z", created="2026-10-30T11:00:00Z", description="HLX tools")]
    client.commits["ValveSoftware/Proton"] = [
        commit("a1", "update wine"),
        commit("b2", "Fix Half-Life: Alyx crash on startup"),          # titolo già uscito: niente
        commit("c3", "Add launch options for HLX\n\nSigned-off-by: Someone <someone@valvesoftware.com>"),
    ]
    found = src.run()
    kinds = sorted((f.change_type, f.file_path) for f in found)
    assert kinds == [("commit", "ValveSoftware/Proton (commit c3)"), ("new_repo", "ValveSoftware/hlx-tools")]
    # solo il repository con un push nuovo viene letto, non quello fermo
    assert [a[0] for a in client.asked] == ["ValveSoftware/Proton"]
    by_type = {f.change_type: f for f in found}
    assert by_type["commit"].score >= 100 and by_type["commit"].text.endswith("Add launch options for HLX")
    assert "someone@" not in by_type["commit"].context          # niente email nell'archivio
    assert by_type["new_repo"].score >= 100                      # HLX nel nome: possibile annuncio
    assert area_of(by_type["commit"].file_path, "commit") == "code"
    assert cfg.game_title("valvesoftware") == "Valve su GitHub"

    # stesso push riletto (anche con le date dei commit spostate): nessuna voce doppia
    client.repos[0] = repo("Proton", "2026-10-30T13:00:00Z")
    assert src.run() == []
    # la rivalutazione dell'archivio non tocca le voci di Valve
    reevaluate(cfg, db)
    assert db.conn.execute("SELECT COUNT(*) FROM findings WHERE game = 'valvesoftware'").fetchone()[0] == 2
    db.close()


def test_failing_repo_is_retried(tmp_path):
    cfg, db = make(tmp_path)
    client = FakeClient()
    client.repos = [repo("SteamOS", "2026-10-29T00:00:00Z")]
    src = ValveGitHubSource(cfg, db, client)
    src.run()
    client.repos = [repo("SteamOS", "2026-10-30T00:00:00Z")]

    def boom(*a, **k):
        raise RuntimeError("HTTP 502")
    client.list_commits = boom
    assert src.run() == [] and src.errors
    client.list_commits = FakeClient.list_commits.__get__(client)
    client.commits["ValveSoftware/SteamOS"] = [commit("d4", "half-life 3 support")]
    assert [f.change_type for f in src.run()] == ["commit"]       # il push non era stato segnato come letto
    db.close()


def test_site_says_whether_the_bot_starts_the_runs(tmp_path, monkeypatch):
    import hlxwatch.reports.site as site
    from hlxwatch.reports.site import build_site
    cfg, db = make(tmp_path)
    base = {"ultimo_ok": "2026-10-30T17:30:00Z", "minuti": 5, "fermo": False}
    cases = [({"attivo": True, "token": True, "errore": None}, "avviato dal bot ogni 30 minuti"),
             ({"attivo": False, "token": True, "errore": "HTTP 401"}, "il bot non riesce più ad avviarlo"),
             ({"attivo": False, "token": False, "errore": None}, "solo i run pianificati di GitHub")]
    for i, (avvio, text) in enumerate(cases):
        monkeypatch.setattr(site, "fast_status", lambda cfg, a=avvio: {**base, "avvio": a})
        html = (build_site(db, cfg, tmp_path / f"s{i}") / "index.html").read_text(encoding="utf-8")
        assert text in html
    met = (tmp_path / "s0" / "metodologia.html").read_text(encoding="utf-8")
    assert "ValveSoftware" in met
    db.close()


PROTON_V1 = '''
    if appid in [
        "1313860", # FIFA 21
    ]:
        self.env["MESA_EXTENSION_MAX_YEAR"] = "2003"
game_fixes = {
    "19900"     :  "16",  # Far Cry 2
}
'''
PROTON_V2 = PROTON_V1.replace('"1313860", # FIFA 21', '"1313860", # FIFA 21\n        "3999990",\n        "4000000",\n        "4000010",') \
    .replace('= "2003"', '= "2010"')


def test_new_appids_in_proton(tmp_path, monkeypatch):
    from hlxwatch.sources.valve_github import extract_appids
    assert extract_appids(PROTON_V1) == {"1313860", "19900"}
    cfg, db = make(tmp_path)
    client = FakeClient()
    files = {"proton": PROTON_V1}
    client.raw_file = lambda repo, sha, path, max_bytes: files[path]
    client.repos = [repo("Proton", "2026-10-29T00:00:00Z")]
    src = ValveGitHubSource(cfg, db, client)
    store = {"3999990": {}, "4000000": {"name": "Some Game", "developers": ["Studio"], "publishers": ["Pub"]},
             "4000010": {"name": "New Thing", "developers": ["Valve"], "publishers": ["Valve"]}}
    monkeypatch.setattr(src, "lookup", lambda appid: store[appid])
    assert src.run() == []                                      # primo giro: app registrate
    files["proton"] = PROTON_V2
    assert src.run() == []                                      # nessun push: il file non si rilegge
    client.repos = [repo("Proton", "2026-10-30T12:00:00Z")]
    found = {f.text: f.score for f in src.run()}
    assert found == {"Proton: correzione dedicata a app 3999990 NON pubblica sul negozio": 70,
                     "Proton: correzione dedicata a Some Game (4000000)": 20,
                     "Proton: correzione dedicata a New Thing (4000010, Valve)": 80}
    client.repos = [repo("Proton", "2026-10-30T13:00:00Z")]
    assert src.run() == []                                      # già viste: nessuna voce doppia
    # tante app nuove insieme (cambio di ramo): 15 a giro, il resto al giro dopo anche senza un push nuovo
    import hlxwatch.sources.valve_github as vg
    files["proton"] = PROTON_V2 + "".join(f'"{5000000 + i}",\n' for i in range(vg.MAX_LOOKUPS + 3))
    monkeypatch.setattr(src, "lookup", lambda appid: store.get(appid, {"name": f"G{appid}"}))
    client.repos = [repo("Proton", "2026-10-30T14:00:00Z")]
    assert len(src.run()) == vg.MAX_LOOKUPS
    assert len(src.run()) == 3
    assert src.run() == []
    # negozio che non risponde: l'app non si archivia, si riprova
    files["proton"] += '"6000000",\n'
    client.repos = [repo("Proton", "2026-10-30T15:00:00Z")]
    monkeypatch.setattr(src, "lookup", lambda appid: None)
    assert src.run() == []
    monkeypatch.setattr(src, "lookup", lambda appid: {})
    assert [f.score for f in src.run()] == [70]
    reevaluate(cfg, db)
    assert db.conn.execute("SELECT COUNT(*) FROM findings WHERE change_type = 'appid'").fetchone()[0] == 3 + 18 + 1
    db.close()
