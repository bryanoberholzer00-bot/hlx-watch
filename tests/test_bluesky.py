"""Pubblicazione su Bluesky (hlxwatch/bluesky.py) con un Bluesky finto: non si pubblica niente davvero."""
from datetime import datetime, timedelta, timezone

import yaml

from hlxwatch import bluesky, serata
from hlxwatch.announcement import KEYWORD
from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
BANNED = ("leak", "confirmed", "upcoming", "imminent")


def make(tmp_path, **over):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.actions.yaml").read_text(encoding="utf-8"))
    raw["bluesky"] = {**raw.get("bluesky", {}), "enabled": True, "dry_run": False, **over}
    return Config(raw, PACKAGE_ROOT / "x.yaml"), Database(tmp_path / "db.sqlite")


def alert(build_id, token, score=115, game="cs2", days_ago=0, keywords=("hlx_class",)):
    b = BuildInfo(game=game, source="github", ref=f"{build_id:040d}", date=NOW - timedelta(days=days_ago))
    return Finding(game=game, source="github", file_path="game/bin/win64/tools/hammer_strings.txt", change_type="added",
                   text=token, token=token, score=score, keywords=list(keywords), build_id=build_id, build=b)


class FakeClient:
    def __init__(self, links=(), fail=False):
        self.links, self.fail, self.posts, self.feed_reads = set(links), fail, [], 0

    def recent_links(self, limit=40):
        self.feed_reads += 1
        return set(self.links)

    def post(self, text, url):
        if self.fail:
            raise RuntimeError("Bluesky non risponde")
        self.posts.append((text, url))
        self.links.add(url)
        return "at://fake/post"


def no_serate(monkeypatch):
    monkeypatch.setattr(serata, "load", lambda path=None: [])


def test_only_high_alerts_one_post_per_build_in_english(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path)
    alerts = [alert(1, "CHLXCoverPreviewHelper"), alert(1, "CHLXBaseProjectileEmitterHelper", 116),
              alert(1, "CDummyHLXCover", 110), alert(2, "npc_rumore", 55, keywords=("watched_file",))]
    posts = bluesky.plan(cfg, db, alerts, NOW)
    assert len(posts) == 1                                     # una build, il rumore da 55 no
    text = posts[0]["testo"]
    assert text.startswith("New in Counter-Strike 2: CHLXBaseProjectileEmitterHelper, CHLXCoverPreviewHelper (+1 more)")
    assert "reported codename" in text and posts[0]["link"] in text and len(text) <= 300
    assert not any(w in text.lower() for w in BANNED)
    db.close()


def test_publish_records_and_never_posts_twice(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path)
    client = FakeClient()
    out = bluesky.publish(cfg, db, [alert(1, "CHLXCoverPreviewHelper")], client=client, now=NOW)
    assert out["pubblicati"] == 1 and len(client.posts) == 1
    out = bluesky.publish(cfg, db, [alert(1, "CHLXCoverPreviewHelper")], client=client, now=NOW)
    assert out["post"] == [] and len(client.posts) == 1       # già nell'archivio
    # archivio perso (giro fallito prima di salvarlo): il link è già nei post dell'account, non si ripubblica
    cfg2, db2 = make(tmp_path / "altro")
    out = bluesky.publish(cfg2, db2, [alert(1, "CHLXCoverPreviewHelper")], client=client, now=NOW)
    assert out["pubblicati"] == 0 and len(client.posts) == 1 and bluesky.plan(cfg2, db2, [alert(1, "CHLXCoverPreviewHelper")], NOW) == []
    db.close()
    db2.close()


def test_daily_limit_but_announcements_pass(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path, max_per_day=2)
    alerts = [alert(i, f"CHLXThing{i}") for i in range(1, 6)]
    alerts.append(alert(9, "Half-Life 3", 220, keywords=(KEYWORD, "hl3")))
    posts = bluesky.plan(cfg, db, alerts, NOW)
    assert len(posts) == 3 and posts[0]["annuncio"] and posts[0]["testo"].startswith("Possible Valve announcement")
    assert "Not verified yet" in posts[0]["testo"]
    db.close()


def test_old_changes_and_backfill_are_not_posted(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path)
    assert bluesky.plan(cfg, db, [alert(1, "CHLXOld", days_ago=30)], NOW) == []
    db.close()


def test_missing_secrets_or_errors_never_raise(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    cfg, db = make(tmp_path)
    out = bluesky.publish(cfg, db, [alert(1, "CHLXA")], now=NOW)
    assert out["tentato"] is False and out["pubblicati"] == 0
    broken = FakeClient(fail=True)
    out = bluesky.publish(cfg, db, [alert(1, "CHLXA")], client=broken, now=NOW)
    assert out["errore"] and broken.posts == []
    assert bluesky.plan(cfg, db, [alert(1, "CHLXA")], NOW)    # non segnato: si riprova al giro dopo
    db.close()


def test_dry_run_does_not_touch_bluesky(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path, dry_run=True)
    client = FakeClient()
    out = bluesky.publish(cfg, db, [alert(1, "CHLXA")], client=client, now=NOW)
    assert len(out["post"]) == 1 and client.posts == [] and client.feed_reads == 0
    db.close()


def test_serata_post_once_and_never_for_the_rehearsal(tmp_path, monkeypatch):
    real = {"id": "tga-2026", "nome": "The Game Awards 2026", "inizio": "2026-11-28T10:00:00Z",
            "spettacolo": "2026-11-28T18:00:00Z", "fine": "2026-11-29T08:00:00Z"}
    drill = {**real, "id": "prova", "prova": True}
    monkeypatch.setattr(serata, "load", lambda path=None: [drill, real])
    cfg, db = make(tmp_path)
    client = FakeClient()
    out = bluesky.publish(cfg, db, [], client=client, now=NOW)
    assert out["pubblicati"] == 1 and "The Game Awards 2026 is tonight" in client.posts[0][0]
    assert "serata=tga-2026" in client.posts[0][1]
    assert bluesky.publish(cfg, db, [], client=client, now=NOW)["post"] == []
    monkeypatch.setattr(serata, "load", lambda path=None: [drill])
    cfg2, db2 = make(tmp_path / "prova")
    assert bluesky.plan(cfg2, db2, [], NOW) == []
    db.close()
    db2.close()


def test_disabled_does_nothing(tmp_path, monkeypatch):
    no_serate(monkeypatch)
    cfg, db = make(tmp_path, enabled=False)
    assert bluesky.publish(cfg, db, [alert(1, "CHLXA")], client=FakeClient(), now=NOW)["post"] == []
    db.close()


def test_client_sends_a_link_facet_and_reads_links_from_the_feed():
    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Http:
        def __init__(self):
            self.calls = []

        def post(self, url, json=None, headers=None, timeout=None):
            self.calls.append((url, json, headers))
            if url.endswith("createSession"):
                return Resp({"accessJwt": "jwt", "did": "did:plc:x"})
            return Resp({"uri": "at://did:plc:x/app.bsky.feed.post/1"})

        def get(self, url, params=None, timeout=None):
            return Resp({"feed": [{"post": {"record": {"text": "è nuovo\nhttps://a/b", "facets": [
                {"features": [{"uri": "https://a/b"}]}]}}}]})

    http = Http()
    c = bluesky.Client("hlx-radar.bsky.social", "segreto", session=http)
    assert c.recent_links() == {"https://a/b"}
    text = "Novità è qui\nhttps://site/x"
    c.post(text, "https://site/x")
    record = http.calls[-1][1]["record"]
    start = text.encode("utf-8").find(b"https://site/x")
    assert record["facets"][0]["index"] == {"byteStart": start, "byteEnd": start + len("https://site/x")}
    assert http.calls[-1][2]["Authorization"] == "Bearer jwt" and http.calls[0][1]["identifier"] == "hlx-radar.bsky.social"
