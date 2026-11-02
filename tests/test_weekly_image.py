from datetime import date, datetime, timezone

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.notifiers.telegram import TelegramNotifier
from hlxwatch.reports import weekly_image as wi
from hlxwatch.storage import Database
from hlxwatch import weekly


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "prova.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def add(db, token, day, score=115, kw="hlx_class"):
    b = db.upsert_build(BuildInfo(game="cs2", source="github", ref=(token * 40)[:40],
                                  date=datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=timezone.utc)))
    db.add_finding(Finding(game="cs2", source="github", file_path="game/bin/win64/tools/hammer_strings.txt",
                           change_type="added", text=token, token=token, score=score, keywords=[kw]), b)


def test_week_pieces_and_image(tmp_path):
    cfg, db = make(tmp_path)
    add(db, "CHLXCoverPreviewHelper", "2026-10-28")
    add(db, "GetAttributeTable_CHLXCoverPreviewHelper", "2026-10-28")      # stesso pezzo
    add(db, "hlx_combine_barrier_cell_spacing", "2026-10-27", 110, "hlx_prefix")
    add(db, "hlx_physics_particle_spawner", "2026-10-30", 105, "hlx_prefix")
    add(db, "CCitadelWeapon", "2026-10-29", 60, "codename")                 # non è HLX
    add(db, "CHLXOld", "2026-10-10")                                         # fuori dalla settimana
    mon, sun = date(2026, 10, 26), date(2026, 11, 1)
    pieces = wi.week_pieces(db, cfg, mon, sun)
    assert [p["kind"] for p in pieces] == ["alyx", "gioco", "motore"]       # prima l'eredità, poi il gioco
    days = wi.week_days(db, sun)
    assert len(days) == 30 and days[-1]["day"] == "2026-11-01"
    assert sum(x["high"] for x in days) == 5
    out = tmp_path / "settimana.png"
    assert wi.weekly_image(cfg, db, {"voci_rilevanti": 4}, mon, sun, out, weekly.week_label(mon, sun))
    from PIL import Image
    with Image.open(out) as img:
        assert img.size == (1200, 675)
    db.close()


class FakeResponse:
    status_code = 200
    text = "ok"


def test_telegram_send_photo(tmp_path, monkeypatch):
    sent = {}

    def fake_post(url, data=None, files=None, timeout=None, **kw):
        sent.update(url=url, data=data, name=files["photo"].name)
        return FakeResponse()
    monkeypatch.setattr("hlxwatch.notifiers.telegram.requests.post", fake_post)
    img = tmp_path / "x.png"
    img.write_bytes(b"png")
    assert TelegramNotifier("TOKEN", "42").send_photo(img, "<b>titolo</b>")
    assert sent["url"].endswith("/botTOKEN/sendPhoto") and sent["data"]["chat_id"] == "42"
    assert sent["data"]["caption"] == "<b>titolo</b>" and sent["name"].endswith("x.png")


def test_week_image_sent_once(tmp_path):
    cfg, db = make(tmp_path)

    class Photo:
        calls = 0

        def send_photo(self, path, caption):
            Photo.calls += 1
            return path.exists()
    mon, sun = date(2026, 10, 26), date(2026, 11, 1)
    for _ in range(2):                                   # il testo non parte, si ritenta: l'immagine no
        weekly.send_week_image(cfg, db, [Photo()], {"voci_rilevanti": 0}, mon, sun, mon.isoformat())
    assert Photo.calls == 1 and db.get_state("weekly:image") == mon.isoformat()
    db.close()
