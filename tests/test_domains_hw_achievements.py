import base64
import io
import json
import zipfile

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.reevaluate import reevaluate
from hlxwatch.sources.achievements import AchievementsSource
from hlxwatch.sources.domains import DomainsSource
from hlxwatch.sources.hardware_ids import HardwareIdsSource
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

VALVE_NS = ["a1-194.akam.net", "a2-64.akam.net"]


def make(tmp_path, **extra):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    for key, value in extra.items():
        raw[key] = {**(raw.get(key) or {}), **value}
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


class Resp:
    def __init__(self, data=None, status=200, content=b""):
        self.data, self.status_code, self.content = data, status, content

    def json(self):
        return self.data


def rdap(registrar, ns, registered="1999-06-28"):
    return {"entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                                           ["fn", {}, "text", registrar]]]}],
            "nameservers": [{"ldhName": n.upper()} for n in ns],
            "events": [{"eventAction": "registration", "eventDate": registered + "T00:00:00Z"}]}


class FakeWeb:
    def __init__(self):
        self.domains = {}
        self.nrd = {}
        self.headers = {}
        self.limited = set()

    def get(self, url, params=None, timeout=None, allow_redirects=True, headers=None):
        if "data.iana.org" in url:
            return Resp({"services": [[["tech"], ["https://rdap.tech.example/"]]]})
        if "/domain/" in url:
            name = url.rsplit("/", 1)[1]
            if name in self.limited:
                return Resp({}, 429)
            d = self.domains.get(name)
            return Resp(d) if d else Resp({}, 404)
        if "whoisds" in url:
            day = base64.b64decode(url.split("/")[-2]).decode()[:-4]
            if day not in self.nrd:
                return Resp(status=404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("domain-names.txt", "\n".join(self.nrd[day]))
            return Resp(content=buf.getvalue())
        raise AssertionError(url)


def test_watched_domain_moves_to_valve_dns(tmp_path):
    cfg, db = make(tmp_path, domains={"watch": ["half-life3.com", "hlx.com", "free-name.com"], "newly_registered": False, "pause_seconds": 0})
    web = FakeWeb()
    web.domains["half-life3.com"] = rdap("SafeNames Ltd.", ["dns1.safenames.com", "dns2.safenames.net"])
    web.domains["hlx.com"] = rdap("Key-Systems GmbH", ["ns01.brandshelter.com"])
    src = DomainsSource(cfg, db, session=web)
    assert src.run() == []                                     # prima lettura
    assert src.run() == []                                     # una volta al giorno
    web.domains["half-life3.com"] = rdap("SafeNames Ltd.", VALVE_NS)
    web.domains["hlx.com"] = rdap("MarkMonitor Inc.", ["ns1.markmonitor.com"])
    web.domains["free-name.com"] = rdap("Tucows", ["ns.tucows.com"], registered="2026-10-30")
    found = {f.file_path: f for f in src.run(force=True)}
    assert found["half-life3.com"].score >= 150 and found["half-life3.com"].text.startswith("POSSIBILE ANNUNCIO")
    assert found["hlx.com"].score == 80 and "registrar cambiato" in found["hlx.com"].text
    assert found["free-name.com"].score == 70
    assert src.run(force=True) == []                           # niente di nuovo
    db.close()


def test_newly_registered_domains_only_with_valve_fingerprint(tmp_path):
    cfg, db = make(tmp_path, domains={"watch": [], "newly_registered": True, "pause_seconds": 0})
    web = FakeWeb()
    day = (utcnow().date().fromordinal(utcnow().date().toordinal() - 1)).isoformat()
    web.nrd[day] = ["halflife-brasil.tech", "playnewvalvegame.com", "playgroundtoys.com", "cats.com",
                    "half-life-alpha.com"]
    web.domains["halflife-brasil.tech"] = rdap("Hostinger", ["ns1.dns-parking.com"])
    web.domains["playnewvalvegame.com"] = rdap("SafeNames Ltd.", VALVE_NS, registered=day)
    web.domains["playgroundtoys.com"] = rdap("GoDaddy", ["ns1.godaddy.com"])
    web.domains["half-life-alpha.com"] = rdap("MarkMonitor Inc.", ["ns1.markmonitor.com"])
    src = DomainsSource(cfg, db, session=web)
    found = {f.file_path: f.score for f in src.run()}
    assert found == {"playnewvalvegame.com": 150, "half-life-alpha.com": 60}
    assert src.run(force=True) == []                           # lista del giorno già letta
    assert src.server("x.tech") == "https://rdap.tech.example/domain/"       # registro dall'elenco IANA
    assert src.server("x.com") == "https://rdap.org/domain/"                 # non nell'elenco: rdap.org
    db.close()


def test_rate_limited_registry_keeps_the_day_for_later(tmp_path):
    cfg, db = make(tmp_path, domains={"watch": [], "newly_registered": True, "pause_seconds": 0})
    web = FakeWeb()
    day = (utcnow().date().fromordinal(utcnow().date().toordinal() - 1)).isoformat()
    web.nrd[day] = ["half-life-beta.com", "playnewvalvegame.com"]
    web.domains["playnewvalvegame.com"] = rdap("SafeNames Ltd.", VALVE_NS, registered=day)
    web.limited = {"half-life-beta.com"}
    src = DomainsSource(cfg, db, session=web)
    assert src.run() == []                                     # il registro chiede di rallentare: ci si ferma
    assert db.get_state("domains:nrd_last_day") is None
    web.limited = set()
    assert [f.file_path for f in src.run(force=True)] == ["playnewvalvegame.com"]
    db.close()


class FakeGitHub:
    def __init__(self, files):
        self.files = files

    def raw_file(self, repo, sha, path, max_bytes):
        return self.files.get(path, "")


SDL = """#define USB_VENDOR_VALVE        0x28de
#define USB_PRODUCT_VALVE_STEAM_CONTROLLER_DONGLE         0x1142
#define USB_PRODUCT_VALVE_STEAM_PROTEUS_DONGLE            0x1304
"""


def test_new_valve_hardware_codename(tmp_path):
    cfg, db = make(tmp_path, hardware_ids={"files": [{"name": "SDL", "repo": "libsdl-org/SDL", "branch": "main",
                                                      "path": "src/joystick/usb_ids.h",
                                                      "pattern": r"#define\s+USB_PRODUCT_VALVE_(\w+)\s+(0x[0-9a-fA-F]+)"}]})
    gh = FakeGitHub({"src/joystick/usb_ids.h": SDL})
    src = HardwareIdsSource(cfg, db, gh)
    assert src.run() == []
    gh.files["src/joystick/usb_ids.h"] = SDL + "#define USB_PRODUCT_VALVE_STEAM_HLX_HEADSET 0x1310\n"
    found = src.run(force=True)
    assert [f.token for f in found] == ["hwid:steam_hlx_headset"]
    assert found[0].score >= 60 and "0x1310" in found[0].text
    gh.files["src/joystick/usb_ids.h"] = "niente"                  # formato cambiato: errore, non "tutto tolto"
    assert src.run(force=True) == [] and src.errors
    db.close()


class FakeSteam:
    def __init__(self):
        self.apps = {}
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        names = self.apps.get(int(params["gameid"]))
        if names is None:
            return Resp({})
        return Resp({"achievementpercentages": {"achievements": [{"name": n, "percent": "1.0"} for n in names]}})


def test_new_and_newly_visible_achievements(tmp_path):
    cfg, db = make(tmp_path, achievements={"pause_seconds": 0})
    steam = FakeSteam()
    steam.apps[546560] = ["TRACK_A1_INTRO_WORLD_2", "GLOBAL_GNOME_ALONE"]
    src = AchievementsSource(cfg, db, session=steam)
    assert src.run() == []                                     # prima lettura; Deadlock nascosto
    steam.apps[546560] += ["NEW_BONUS_LEVEL"]
    steam.apps[1422450] = ["HLX_CROSSOVER_HAT"]
    found = {f.game: f for f in src.run(force=True)}
    assert found["alyx"].score == 55 and "NEW_BONUS_LEVEL" in found["alyx"].text
    assert found["deadlock"].score >= 90 and "ora visibili" in found["deadlock"].text
    assert found["deadlock"].text.startswith("POSSIBILE ANNUNCIO")
    assert src.run(force=True) == []
    reevaluate(cfg, db)
    kinds = sorted(r[0] for r in db.conn.execute("SELECT change_type FROM findings"))
    assert kinds == ["achievement", "achievement"]
    assert json.loads(db.get_state("achievements:1422450"))["visibili"] is True
    db.close()
