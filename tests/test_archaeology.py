from datetime import datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import Finding
from hlxwatch.sources.archaeology import parse_history
from hlxwatch.storage import Database

LOG = """@@@aaa111|2025-02-25T10:00:00+00:00|1234 | 5 files
diff --git a/game/bin/win64/engine2_strings.txt b/game/bin/win64/engine2_strings.txt
--- a/game/bin/win64/engine2_strings.txt
+++ b/game/bin/win64/engine2_strings.txt
@@ -10,0 +11,2 @@
+r_hlx_fsr3_min_reactiveness
+r_other_cvar
@@@bbb222|2025-03-01T10:00:00+00:00|1235 | 1 file
diff --git a/x.txt b/x.txt
--- a/x.txt
+++ b/x.txt
@@ -1 +1 @@
-niente da vedere
+ancora niente
@@@ccc333|2025-05-08T10:00:00+00:00|1240 | 2 files
diff --git a/game/bin/win64/engine2_strings.txt b/game/bin/win64/engine2_strings.txt
--- a/game/bin/win64/engine2_strings.txt
+++ /dev/null
@@ -11 +0,0 @@
-r_hlx_fsr3_min_reactiveness
"""


def test_parse_history():
    commits = list(parse_history(iter(LOG.splitlines(keepends=True))))
    assert [c["sha"] for c in commits] == ["aaa111", "ccc333"]          # il commit senza righe HLX non c'è
    assert commits[0]["changes"] == [{"path": "game/bin/win64/engine2_strings.txt", "sign": "+",
                                      "text": "r_hlx_fsr3_min_reactiveness"}]
    assert commits[1]["changes"][0]["sign"] == "-"                       # file cancellato: resta il percorso
    assert commits[1]["changes"][0]["path"] == "game/bin/win64/engine2_strings.txt"


def test_backdated_finding(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    db = Database(Config(raw, tmp_path / "config.yaml").db_path)
    f = Finding(game="deadlock", source="github", file_path="a", change_type="added", text="hlx_x",
                score=110, keywords=["hlx"], token="hlx_x")
    db.add_finding(f, None, created_at=datetime(2025, 2, 25, tzinfo=timezone.utc))
    row = db.conn.execute("SELECT created_at FROM findings WHERE id = ?", (f.id,)).fetchone()
    assert row["created_at"].startswith("2025-02-25")
    db.close()
