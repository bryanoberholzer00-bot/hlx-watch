from datetime import datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.notifiers.base import finding_line, group_findings


def _cfg() -> Config:
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    return Config(raw, PACKAGE_ROOT / "config.example.yaml")


def test_siblings_are_merged_per_commit():
    build = BuildInfo(game="cs2", source="github", ref="abc", date=datetime.now(timezone.utc), url="u")
    other = BuildInfo(game="cs2", source="github", ref="def", date=datetime.now(timezone.utc), url="u")
    items = []
    for i in range(9):
        f = Finding(game="cs2", source="github", file_path=f"game/bin/tool{i}_strings.txt", change_type="removed",
                    text="x!hlx_physics_particle_spawner", score=135, token="hlx_physics_particle_spawner")
        f.build = build
        items.append(f)
    f = Finding(game="cs2", source="github", file_path="other.txt", change_type="removed",
                text="hlx_physics_particle_spawner", score=135, token="hlx_physics_particle_spawner")
    f.build = other
    items.append(f)
    groups = group_findings(items, _cfg())
    assert len(groups) == 1
    assert groups[0].total == 2          # stesso token ma commit diverso: resta separato
    merged = groups[0].findings[0]
    assert len(merged.siblings) == 8
    assert "(+8 file)" in finding_line(merged, "markdown")
    assert "(+8 file)" in finding_line(merged, "html")


def test_link_labels():
    from hlxwatch.links import link_label, steam_app_history_url, steam_build_url
    assert link_label("https://github.com/SteamTracking/GameTracking-CS2/commit/abc") == "commit"
    assert steam_build_url(26185107) == "https://steamdb.info/patchnotes/26185107/"
    assert link_label(steam_build_url(26185107)) == "build"
    assert link_label(steam_app_history_url(570)) == "cronologia"
    assert link_label("https://store.steampowered.com/app/570/") == "pagina Steam"
    assert link_label(None) == "fonte"


def test_webpush_message_and_pages(monkeypatch):
    from hlxwatch.models import Finding
    from hlxwatch.notifiers.base import AlertGroup
    from hlxwatch.notifiers.webpush import WebPushNotifier
    site = "https://example.org/hlx/"
    hlx = Finding(game="deadlock", source="github", file_path="a.h", change_type="added", text="x hlx", score=100,
                  keywords=["hlx"], token="hlx_thumper")
    one = WebPushNotifier.message([AlertGroup("deadlock", "Deadlock", [hlx], 100, 1)], site)
    assert one["title"] == "HLX Watch: novità in Deadlock" and one["url"] == site + "search.html?q=hlx_thumper"
    others = [Finding(game="cs2", source="github", file_path="b", change_type="added", text=f"t{i}", score=55,
                      keywords=["hlx"], token=f"t{i}") for i in range(5)]
    many = WebPushNotifier.message([AlertGroup("deadlock", "Deadlock", [hlx], 100, 1),
                                    AlertGroup("cs2", "Counter-Strike 2", others, 55, 5)], site)
    assert many["title"] == "HLX Watch: 6 novità (max 100)" and many["url"].endswith("search.html?new=1&min=50")
    assert many["body"].splitlines()[0].endswith("[100] Deadlock: hlx_thumper") and "altre 2" in many["body"]

    calls = []

    class Resp:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_post(url, json, headers, timeout):
        calls.append(json["cursor"])
        return Resp({"sent": 10, "statuses": {"201": 10}, "next": None if json["cursor"] else "c1"})

    monkeypatch.setattr("hlxwatch.notifiers.webpush.requests.post", fake_post)
    n = WebPushNotifier("https://bot.example/", "s", site)
    assert n.send_groups("x", [AlertGroup("deadlock", "Deadlock", [hlx], 100, 1)]) is True
    assert calls == [None, "c1"]                      # seconda pagina di browser con il cursore
    assert n.send_text("report", "testo") is True and len(calls) == 2   # i report non vanno come notifiche
