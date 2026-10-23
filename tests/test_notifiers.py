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
