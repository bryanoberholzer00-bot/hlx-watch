from hlxwatch import fase
from hlxwatch.models import Finding
from hlxwatch.notifiers.base import AlertGroup, finding_line
from hlxwatch.notifiers.webpush import WebPushNotifier


def f(token, text=None, keywords=("hlx_class",), score=115):
    return Finding(game="cs2", source="github", file_path="game/bin/win64/tools/hammer_strings.txt",
                   change_type="added", text=text or token, token=token, score=score, keywords=list(keywords))


def test_alert_kind():
    assert fase.alert_kind("CHLXCoverPreviewHelper", None, ["hlx_class"]) == "gioco"
    assert fase.alert_kind("hlx_physics_particle_spawner", None, ["hlx_prefix"]) == "motore"
    assert fase.alert_kind("hlx_combine_barrier_cell_spacing", None, ["hlx_prefix"]) == "alyx"   # era hlvr_ in Alyx
    assert fase.alert_kind("Thumper", '"- Thumper (HLX): causes antlions to run away briefly"', ["hlx"]) == "gioco"
    assert fase.alert_kind("hlvr/hlx", None, ["hlx_path"]) is None                                # tipo non chiaro
    assert fase.alert_kind("CCitadelWeapon", None, ["codename"]) is None                          # non è HLX
    # "hlvr" da solo è il nome del gioco: "mod != hlx" è un'impostazione del motore, non un'eredità di Alyx
    assert fase.alert_kind("hlx", "mod != hlx || !m_bRefract", ["hlx"]) == "motore"
    assert fase.alert_kind("HLX", "// MPropertySuppressExpr = \"mod != hlx\"", ["hlx"]) == "motore"


def test_trace_kind_for_the_search_page():
    assert fase.trace_kind("hlvr/hlx", None, ["hlx_path"]) == "dubbio"
    assert fase.trace_kind("hlx", '"product" "hlx"', ["hlx"]) == "strumenti"
    assert fase.trace_kind("CCitadelWeapon", None, ["codename"]) is None


def test_search_data_carries_the_kind(tmp_path):
    import yaml
    from hlxwatch.config import PACKAGE_ROOT, Config
    from hlxwatch.models import BuildInfo
    from hlxwatch.reports.site import export_findings
    from hlxwatch.storage import Database
    from hlxwatch.storage.db import utcnow
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    db = Database(Config(raw, tmp_path / "config.yaml").db_path)
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref="abc", date=utcnow()))
    db.add_finding(f("CHLXCoverPreviewHelper"), b)
    db.add_finding(f("CCitadelWeapon", keywords=["codename"], score=60), b)
    kinds = {r["token"]: r.get("k") for r in export_findings(db)}
    assert kinds == {"CHLXCoverPreviewHelper": "gioco", "CCitadelWeapon": None}
    db.close()


def test_telegram_and_push_show_the_kind():
    line = finding_line(f("CHLXCoverPreviewHelper"), "html")
    assert "🎮 pezzo di gioco" in line and "<code>CHLXCoverPreviewHelper</code>" in line
    assert "🔁 eredità di Alyx" in finding_line(f("hlx_combine_barrier_cell_spacing"), "markdown")
    assert "pezzo" not in finding_line(f("CCitadelWeapon", keywords=["codename"], score=60), "html")
    one = WebPushNotifier.message([AlertGroup("cs2", "Counter-Strike 2", [f("CHLXCoverPreviewHelper")], 115, 1)],
                                  "https://example.org/")
    assert one["title"] == "HLX Watch: pezzo di gioco in Counter-Strike 2" and "🎮" in one["body"]
