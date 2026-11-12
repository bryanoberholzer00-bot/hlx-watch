"""Pacchetti di SteamOS e certificazioni FCC (hlxwatch/sources/hardware_valve.py), con un web finto."""
import io
import json
import tarfile

import yaml

from hlxwatch.config import PACKAGE_ROOT, Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.reports.site import build_site
from hlxwatch.sources.hardware_valve import (FccIdsSource, SteamOSPackagesSource, device_codename, parse_fcc,
                                             parse_repos, repo_parts)
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow


def make(tmp_path):
    raw = yaml.safe_load((PACKAGE_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(tmp_path / "data")
    raw["general"]["db_path"] = str(tmp_path / "data" / "hlxwatch.sqlite")
    cfg = Config(raw, tmp_path / "config.yaml")
    return cfg, Database(cfg.db_path)


def pacman_db(packages):
    """Un .db di pacman come quelli di SteamOS: un tar con una cartella <nome>-<versione>/desc per pacchetto."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, desc in packages.items():
            data = f"%FILENAME%\n{name}-1-1-x86_64.pkg.tar.zst\n\n%NAME%\n{name}\n\n%VERSION%\n1-1\n\n%DESC%\n{desc}\n\n".encode()
            info = tarfile.TarInfo(f"{name}-1-1/desc")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def listing(names):
    return "<html><body>" + "".join(f'<a href="{n}/">{n}/</a>' for n in ["..", *names]) + "</body></html>"


class Resp:
    def __init__(self, text="", content=b"", status=200):
        self.text, self.content, self.status_code = text, content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeWeb:
    def __init__(self):
        self.repos = ["core-3.10", "extra-3.10", "jupiter-3.10", "jupiter-main", "holo-main", "holo-3.10", "sources"]
        self.packages = {"jupiter-main": {"jupiter-hw-support": "Jupiter HW support package",
                                          "galileo-mura": "Galileo display calibration",
                                          "linux-neptune-73": "The Linux kernel", "gamescope": "gaming shell"},
                         "holo-main": {"fremont-hw-support": "Fremont HW support", "steamos-manager": "manager"}}
        self.fcc = [("2AES4-1015", "2026-07-29", "VR Headset"), ("2AES41001", "2015-07-13", "Steam Controller")]
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/archlinux-mirror/"):
            return Resp(listing(self.repos))
        if url.endswith(".db"):
            repo = url.rsplit("/", 1)[1][:-3]
            return Resp(content=pacman_db(self.packages[repo]))
        if "fccid.io" in url:
            rows = "".join(f'<tr><td><a href="/{c}">{c}</a> {d}</td><td>{p} Original Equipment</td></tr>' for c, d, p in self.fcc)
            return Resp('<table><tr><td>Company</td><td>Valve Corporation</td></tr><tr><td>Contact</td><td>Mario Rossi</td></tr>'
                        f'<tr><td>FCC ID Application Date</td><td>Product Purpose</td></tr>{rows}</table>')
        return Resp(status=404)


def test_parsers():
    assert parse_repos(listing(["jupiter-main", "holo-3.10"])) == ["holo-3.10", "jupiter-main"]
    assert repo_parts("jupiter-3.10") == ("jupiter", "3.10") and repo_parts("core-3.8.1x") == ("core", "3.8.1x")
    assert repo_parts("holo-staging-2026-10-29") == ("holo", "staging-2026-10-29")
    assert repo_parts("kde-unstable-debug-main") == ("kde", "main") and repo_parts("sources") is None
    assert device_codename("fremont-hw-support") == "fremont" and device_codename("galileo-mura") == "galileo"
    assert device_codename("linux-neptune-73") == "neptune" and device_codename("gamescope") == ""
    assert device_codename("linux-firmware") == ""
    fcc = parse_fcc('<tr><td><a href="/2AES4-1016">2AES4-1016</a> 2025-12-31</td><td>Video Game Machine Original Equipment</td></tr>'
                    '<tr><td>Contact</td><td>Mario Rossi</td></tr>', "2AES4")
    assert fcc == {"2AES4-1016": {"data": "2025-12-31", "descrizione": "Video Game Machine"}}


def test_steamos_packages(tmp_path):
    cfg, db = make(tmp_path)
    web = FakeWeb()
    src = SteamOSPackagesSource(cfg, db, web)
    assert src.run() == [] and not src.errors                       # primo giro: registra
    assert src.run() == []                                          # intervallo di 12 ore
    # un dispositivo nuovo, uno noto, una variante, un kernel nuovo, un pacchetto qualunque, archivi nuovi
    web.packages["holo-main"].update({"orion-hw-support": "Orion HW support", "fremont-hw-support-debug": "debug",
                                      "galileo-firmware": "firmware", "kwin-addons": "addons"})
    web.packages["jupiter-main"]["linux-neptune-74"] = "The Linux kernel"
    web.repos += ["core-3.11", "extra-3.11", "jupiter-3.11", "orion-main", "orion-3.11", "holo-staging-2026-11-20"]
    found = {f.token: f for f in src.run(force=True)}
    assert found["steamos:orion-hw-support"].score == 90 and "mai visto prima" in found["steamos:orion-hw-support"].text
    assert found["steamos:galileo-firmware"].score == 40 and "già noto" in found["steamos:galileo-firmware"].text
    assert found["steamos:kwin-addons"].score == 30 and found["steamos:linux-neptune-74"].score == 30
    assert "steamos:fremont-hw-support-debug" not in found                        # variante di un pacchetto noto
    assert found["steamos:orion-main"].score == 80 and "orion" in found["steamos:orion-main"].text
    versions = [t for t in found if t in ("steamos:core-3.11", "steamos:extra-3.11", "steamos:jupiter-3.11")]
    assert len(versions) == 1 and found[versions[0]].score == 40                  # 3.11: una voce sola
    assert "steamos:orion-3.11" not in found and "steamos:holo-staging-2026-11-20" not in found
    assert all(f.game == "valvehw" and f.change_type in ("steamos_package", "steamos_repo") for f in found.values())
    # un pacchetto tolto e rimesso non torna "nuovo"
    del web.packages["jupiter-main"]["gamescope"]
    assert src.run(force=True) == []
    web.packages["jupiter-main"]["gamescope"] = "gaming shell"
    assert src.run(force=True) == []
    # formato cambiato: errore, nessuna voce
    web.repos = []
    assert src.run(force=True) == [] and src.errors
    db.close()


def test_device_codename_already_in_archive(tmp_path):
    cfg, db = make(tmp_path)
    b = db.upsert_build(BuildInfo(game="steamvr", source="github", ref="d" * 40, date=utcnow()))
    db.add_finding(Finding(game="steamvr", source="github", file_path="drivers/x.txt", change_type="added",
                           text="Valve Calypso", score=80, keywords=[]), b)
    web = FakeWeb()
    src = SteamOSPackagesSource(cfg, db, web)
    src.run()
    web.packages["holo-main"]["calypso-hw-support"] = "Calypso HW support"
    web.packages["holo-main"]["ibex-firmware"] = "controller firmware"
    found = {f.token: f for f in src.run(force=True)}
    assert found["steamos:calypso-hw-support"].score == 40 and "nell'archivio" in found["steamos:calypso-hw-support"].text
    assert found["steamos:ibex-firmware"].score == 40 and "hardware Valve già noti" in found["steamos:ibex-firmware"].text
    db.close()


def test_fcc_and_hardware_page(tmp_path):
    cfg, db = make(tmp_path)
    web = FakeWeb()
    fcc = FccIdsSource(cfg, db, web)
    assert fcc.run() == [] and not fcc.errors
    assert "Mario Rossi" not in json.dumps(db.get_state("fcc:2AES4"))                  # niente nomi di persone
    web.fcc.insert(0, ("2AES4-1017", "2026-11-20", "Handheld Gaming Device"))
    [f] = fcc.run(force=True)
    assert f.score == 80 and f.token == "fcc:2aes4-1017" and "Handheld Gaming Device" in f.text and "2026-11-20" in f.text
    assert f.change_type == "fcc_id" and f.build.url == "https://fccid.io/2AES4-1017"
    steamos = SteamOSPackagesSource(cfg, db, web)
    steamos.run()
    out = build_site(db, cfg, tmp_path / "site")
    page = (out / "hardware.html").read_text(encoding="utf-8")
    assert "2AES4-1017" in page and "Handheld Gaming Device" in page and "fremont-hw-support" in page
    assert "Certificazioni FCC (3)" in page and "Dispositivi nei pacchetti di SteamOS" in page
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert [r["area"] for r in data if r["type"] == "fcc_id"] == ["docs"]
    web.fcc = []                                                                         # pagina cambiata: errore
    assert fcc.run(force=True) == [] and fcc.errors
    db.close()


def test_reevaluate_keeps_the_new_entries(tmp_path):
    from hlxwatch.reevaluate import reevaluate
    cfg, db = make(tmp_path)
    web = FakeWeb()
    fcc, steamos = FccIdsSource(cfg, db, web), SteamOSPackagesSource(cfg, db, web)
    fcc.run(), steamos.run()
    web.fcc.insert(0, ("2AES4-1017", "2026-11-20", "Handheld"))
    web.packages["holo-main"]["orion-hw-support"] = "x"
    web.repos.append("orion-main")
    assert len(fcc.run(force=True)) == 1 and len(steamos.run(force=True)) == 2
    reevaluate(cfg, db)
    left = {r[0] for r in db.conn.execute("SELECT token FROM findings WHERE game = 'valvehw'")}
    assert left == {"fcc:2aes4-1017", "steamos:orion-hw-support", "steamos:orion-main"}
    db.close()


# ------------------------------------------------------------------ documentazione di Steamworks
def doc_page(paths, text_lines):
    nav = "".join(f'<a href="https://partner.steamgames.com{p}">{p}</a>' for p in paths)
    body = "".join(f"<p>{line}</p>" for line in text_lines)
    return (f'<html><div id="docNavigation">{nav}</div><div class="documentation_bbcode">{body}</div>'
            "<div>Steamworks is the set of tools and services built by Valve</div></html>")


class DocsWeb:
    def __init__(self):
        self.paths = ["/doc/home", "/doc/steamhardware", "/doc/steamhardware/steamdeck", "/doc/steamhardware/steamframe",
                      "/doc/steamhardware/steamframe/input"] + [f"/doc/features/page{i}" for i in range(60)]
        self.lines = ["The Steam hardware lineup includes Steam Deck, Steam Controller, and the upcoming Steam Frame.",
                      "Steam Deck", "Steam Frame"]

    def get(self, url, headers=None, timeout=None):
        return Resp(doc_page(self.paths, self.lines))


def test_steamworks_docs(tmp_path):
    from hlxwatch.sources.hardware_valve import SteamworksDocsSource, latest, parse_doc_index, parse_doc_text
    web = DocsWeb()
    html = doc_page(web.paths, web.lines)
    assert "/doc/steamhardware/steamframe" in parse_doc_index(html) and len(parse_doc_index(html)) == 65
    assert parse_doc_text(html)[0].startswith("The Steam hardware lineup") and "Steamworks is" not in " ".join(parse_doc_text(html))
    cfg, db = make(tmp_path)
    src = SteamworksDocsSource(cfg, db, web)
    assert src.run() == [] and not src.errors                                     # primo giro: registra
    web.paths += ["/doc/steamhardware/steamorion", "/doc/steamhardware/steamorion/devkits",
                  "/doc/steamhardware/steamframe/engines", "/doc/store/new_rules", "/doc/features/hlx_preview"]
    web.lines[0] = "The Steam hardware lineup includes Steam Deck, Steam Controller, Steam Frame and the upcoming Steam Orion."
    found = {f.token: f for f in src.run(force=True)}
    assert found["steamworks:/doc/steamhardware/steamorion"].score == 90
    assert "steamorion" in found["steamworks:/doc/steamhardware/steamorion"].text
    assert found["steamworks:/doc/steamhardware/steamorion/devkits"].score == 60          # stesso dispositivo: una sola a 90
    assert found["steamworks:/doc/steamhardware/steamframe/engines"].score == 60
    assert found["steamworks:/doc/store/new_rules"].score == 30
    assert found["steamworks:/doc/features/hlx_preview"].score >= 100                      # le parole HLX contano
    [line] = [f for f in found.values() if f.change_type == "steamworks_text"]
    assert line.score == 40 and "Steam Orion" in line.text
    assert src.run(force=True) == []                                                       # niente di nuovo
    assert latest(db)["steamworks_hw"] == {"steamdeck": 1, "steamframe": 3, "steamorion": 2}
    web.paths = ["/doc/home"]                                                             # formato cambiato: errore
    assert src.run(force=True) == [] and src.errors
    db.close()


def test_generic_hardware_pages_are_not_devices():
    from hlxwatch.sources.hardware_valve import doc_device
    assert doc_device("/doc/steamhardware/steamframe/input") == "steamframe"
    assert doc_device("/doc/steamhardware/compat/verified") == "" and doc_device("/doc/steamhardware/proton") == ""
    assert doc_device("/doc/steamhardware") == "" and doc_device("/doc/features/steamvr") == ""
