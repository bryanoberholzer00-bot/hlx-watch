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


def test_telegram_and_push_show_the_kind():
    line = finding_line(f("CHLXCoverPreviewHelper"), "html")
    assert "🎮 pezzo di gioco" in line and "<code>CHLXCoverPreviewHelper</code>" in line
    assert "🔁 eredità di Alyx" in finding_line(f("hlx_combine_barrier_cell_spacing"), "markdown")
    assert "pezzo" not in finding_line(f("CCitadelWeapon", keywords=["codename"], score=60), "html")
    one = WebPushNotifier.message([AlertGroup("cs2", "Counter-Strike 2", [f("CHLXCoverPreviewHelper")], 115, 1)],
                                  "https://example.org/")
    assert one["title"] == "HLX Watch: pezzo di gioco in Counter-Strike 2" and "🎮" in one["body"]
