"""Hardware Valve fuori dai file dei giochi: i pacchetti di SteamOS e le certificazioni FCC.

Nella ricerca sui metodi dei data miner (2020-2026) l'hardware di Valve è uscito soprattutto da qui:
  - **pacchetti di SteamOS**: l'archivio pubblico da cui si aggiornano Steam Deck e Steam Machine
    (steamdeck-packages.steamos.cloud/archlinux-mirror/). Ogni dispositivo ha i suoi pacchetti di supporto:
    jupiter-hw-support (Steam Deck), galileo-mura (Steam Deck OLED), fremont-hw-support (Steam Machine), il kernel
    linux-neptune. Un pacchetto di supporto con un nome in codice mai visto è un dispositivo nuovo; un archivio nuovo
    (orion-main) o una versione nuova (jupiter-3.11) dicono che SteamOS si prepara a qualcosa. Si leggono solo i due
    elenchi principali, jupiter-main e holo-main (~120 KB l'uno), più l'elenco delle cartelle.
  - **FCC**: ogni dispositivo radio, prima di uscire negli Stati Uniti, riceve un codice FCC. Valve è il richiedente
    2AES4: Steam Controller e Steam Machine sono comparsi lì mesi prima di uscire, Steam Frame il 29/07/2026. Il sito
    ufficiale della FCC respinge i programmi (Access Denied); fccid.io ripubblica gli stessi dati e il suo robots.txt
    permette la pagina del richiedente. Si salvano solo codice, data e descrizione (non il nome del contatto).
Geekbench ("Valve Fremont" nei test) è stato valutato e scartato: risponde con un controllo anti-robot di Cloudflare.
Il primo giro di ogni fonte registra quello che c'è già, senza avvisi.
"""
from __future__ import annotations

import io
import json
import logging
import re
import tarfile
from datetime import timedelta
from typing import Any

import requests

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

GAME = "valvehw"
HEADERS = {"User-Agent": f"Mozilla/5.0 (compatible; hlx-watch/{__version__}; +https://github.com/bryanoberholzer00-bot/hlx-watch)"}
MIRROR = "https://steamdeck-packages.steamos.cloud/archlinux-mirror/"
FCC_PAGE = "https://fccid.io/{grantee}"

# archivi di SteamOS: prefisso (jupiter, holo, core...) e versione (main, rel, staging, 3.10, staging-2026-10-29...)
REPO_NAME = re.compile(r"^(?P<prefix>[a-z][a-z0-9-]*?)-(?:debug-|testing-|unstable-)*"
                       r"(?P<kind>main|rel|staging(?:-[0-9-]+)?|ci-test|\d+(?:\.\d+)*(?:\.\d+x|x)?)$")
# pacchetti legati a un dispositivo: il nome in codice è la prima parte (o quella dopo linux-)
DEVICE_PKG = re.compile(r"^(?:linux-(?P<kernel>[a-z]+)(?:-\d+)?|(?P<dev>[a-z][a-z0-9]+)-(?:hw-support|mura|firmware|fw|"
                        r"fan-control|dock-updater(?:-bin)?|validation-tools|legacy-support|hibernate-disable|"
                        r"controller-update|tools))$")
VARIANT = re.compile(r"-(?:debug|headers|devel|docs|drm-exec|kasan)$")
# il kernel linux-<nome> si chiama come il dispositivo solo per i nomi in codice, non per le varianti generiche
KERNEL_GENERIC = {"api", "firmware", "headers", "tools", "lts", "zen", "hardened", "rt"}


def _get(session: requests.Session, url: str, timeout: int = 60) -> requests.Response:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


class _Base:
    """Schema comune delle fonti piccole: enabled, intervallo, errori parziali in .errors."""
    name = ""
    section = ""
    last_key = ""

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.session = session or requests.Session()
        self.scfg: dict[str, Any] = (cfg.get("hardware_valve", {}) or {}).get(self.section, {}) or {}
        self.interval = timedelta(hours=float(self.scfg.get("interval_hours", 12)))
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool((self.cfg.get("hardware_valve", {}) or {}).get("enabled", True)) and bool(self.scfg.get("enabled", True))

    def due(self) -> bool:
        last = parse_iso(self.db.get_state(self.last_key))
        return last is None or utcnow() - last >= self.interval

    def run(self, force: bool = False) -> list[Finding]:
        self.errors = []
        if not self.enabled or (not force and not self.due()):
            return []
        try:
            found = self.check()
        except Exception as exc:
            log.warning("[%s] non letto: %s", self.name, exc)
            self.errors.append(str(exc))
            return []
        self.db.set_state(self.last_key, iso(utcnow()))
        return found

    def check(self) -> list[Finding]:
        raise NotImplementedError

    def _add(self, *, source: str, change_type: str, keyword: str, token: str, text: str, score: int, path: str,
             url: str, ref: str, context: dict[str, Any]) -> Finding:
        build = BuildInfo(game=GAME, source=source, ref=ref, date=utcnow(), url=url, message=text[:500])
        finding = Finding(game=GAME, source=source, file_path=path, change_type=change_type, text=text,
                          score=min(score, 250), keywords=[keyword], token=token,
                          context=json.dumps(context, ensure_ascii=False))
        finding.build = build
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("[%s] %s", self.name, text)
        return finding


# ------------------------------------------------------------------ pacchetti di SteamOS
def parse_repos(html: str) -> list[str]:
    """Le cartelle dell'archivio (jupiter-main, holo-3.10...) dall'elenco del server."""
    return sorted({m for m in re.findall(r'href="([a-z][a-z0-9.-]*)/"', html)})


def parse_packages(raw: bytes) -> dict[str, str]:
    """nome del pacchetto -> descrizione, dal file .db di pacman (un tar, compresso o no)."""
    out: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            if not member.name.endswith("/desc"):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            fields = dict(re.findall(r"%([A-Z0-9]+)%\n(.*?)(?:\n\n|\n?$)", fh.read().decode("utf-8", "replace"), re.S))
            name = (fields.get("NAME") or "").strip()
            if name:
                out[name] = (fields.get("DESC") or "").strip()[:200]
    return out


def repo_parts(name: str) -> tuple[str, str] | None:
    """jupiter-3.10 -> ("jupiter", "3.10"); holo-staging-2026-10-29 -> ("holo", "staging-2026-10-29")."""
    m = REPO_NAME.match(name)
    return (m.group("prefix"), m.group("kind")) if m else None


def device_codename(package: str) -> str:
    """fremont-hw-support -> "fremont", linux-neptune-73 -> "neptune"; "" se il pacchetto non è di un dispositivo."""
    m = DEVICE_PKG.match(package)
    if not m:
        return ""
    name = m.group("kernel") or m.group("dev") or ""
    return "" if name in KERNEL_GENERIC else name


class SteamOSPackagesSource(_Base):
    name = "steamos_packages"
    section = "steamos"
    last_key = "steamos:last_run"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        super().__init__(cfg, db, session)
        self.repos: list[str] = list(self.scfg.get("repos") or ["jupiter-main", "holo-main"])
        self.score_device = int(self.scfg.get("score_device", 90))
        self.score_device_known = int(self.scfg.get("score_device_known", 40))
        self.score_repo = int(self.scfg.get("score_repo", 80))
        self.score_version = int(self.scfg.get("score_version", 40))
        self.score_package = int(self.scfg.get("score_package", 30))

    def check(self) -> list[Finding]:
        found: list[Finding] = []
        listing = parse_repos(_get(self.session, MIRROR).text)
        if not listing:
            raise RuntimeError("elenco degli archivi di SteamOS vuoto: formato cambiato?")
        found += self._compare_repos(listing)
        for repo in self.repos:
            raw = _get(self.session, f"{MIRROR}{repo}/os/x86_64/{repo}.db").content
            packages = parse_packages(raw)
            if not packages:
                raise RuntimeError(f"nessun pacchetto in {repo}.db: formato cambiato?")
            found += self._compare_packages(repo, packages)
        return found

    # -------------------------------------------------------------- archivi nuovi
    def _compare_repos(self, listing: list[str]) -> list[Finding]:
        key = "steamos:repos"
        raw = self.db.get_state(key)
        self.db.set_state(key, json.dumps(listing))
        if raw is None:
            log.info("[steamos] %d archivi registrati", len(listing))
            return []
        old = set(json.loads(raw))
        old_prefixes = {p[0] for p in map(repo_parts, old) if p}
        old_versions = {p[1] for p in map(repo_parts, old) if p and re.match(r"\d", p[1])}
        new = sorted(set(listing) - old)
        families: dict[str, list[str]] = {}               # famiglia mai vista -> i suoi archivi nuovi
        versions: dict[str, list[str]] = {}               # versione mai vista -> archivi (core-3.11, jupiter-3.11...)
        for name in new:
            parts = repo_parts(name)
            if not parts:
                continue
            if parts[0] not in old_prefixes:
                families.setdefault(parts[0], []).append(name)
            elif re.match(r"\d", parts[1]) and parts[1] not in old_versions:
                versions.setdefault(parts[1], []).append(name)
        out = []
        for prefix, names in families.items():
            main = next((n for n in names if n.endswith("-main")), names[0])
            text = f"SteamOS: famiglia di archivi nuova «{prefix}» ({', '.join(names[:6])})"
            out.append(self._add(source="steamos_packages", change_type="steamos_repo", keyword="steamos_repo",
                                 token=f"steamos:{main}", text=text, score=self.score_repo, path="steamos/archlinux-mirror",
                                 url=f"{MIRROR}{main}/", ref=f"steamos:repo:{prefix}", context={"archivi": names}))
        for version, names in versions.items():
            main = next((n for n in names if n.startswith("jupiter-")), names[0])
            text = f"SteamOS: versione nuova in preparazione, {version} ({len(names)} archivi, fra cui {main})"
            out.append(self._add(source="steamos_packages", change_type="steamos_repo", keyword="steamos_repo",
                                 token=f"steamos:{main}", text=text, score=self.score_version, path="steamos/archlinux-mirror",
                                 url=f"{MIRROR}{main}/", ref=f"steamos:version:{version}", context={"archivi": names}))
        return out

    # -------------------------------------------------------------- pacchetti nuovi
    def _known_codename(self, codename: str, packages_before: set[str]) -> str:
        """Dove si vedeva già il nome in codice: nei pacchetti, nella lista dell'hardware Valve o nell'archivio."""
        rx = re.compile(rf"(?<![a-z0-9]){re.escape(codename)}(?![a-z0-9])", re.I)
        hit = next((p for p in sorted(packages_before) if rx.search(p)), "")
        if hit:
            return f"c'era già il pacchetto {hit}"
        from hlxwatch.sources.hardware_ids import HardwareIdsSource
        hw = HardwareIdsSource(self.cfg, self.db, None)
        hw.search_code = False
        res = hw.matcher.analyze_line(codename, file_path="steamos", change_type="added")
        if res and "valve_hardware" in res.keywords:
            return "è tra i nomi dell'hardware Valve già noti"
        return hw.known({"repo": "steamos", "path": ""}, f"STEAM_{codename.upper()}")

    def _compare_packages(self, repo: str, packages: dict[str, str]) -> list[Finding]:
        key = f"steamos:pkgs:{repo}"
        raw = self.db.get_state(key)
        old: dict[str, str] = json.loads(raw) if raw else {}
        self.db.set_state(key, json.dumps({**old, **packages}, sort_keys=True))   # i tolti restano: niente falsi "nuovi"
        if raw is None:
            log.info("[steamos] %s: %d pacchetti registrati", repo, len(packages))
            return []
        before = set(old)
        bases = {VARIANT.sub("", p) for p in before}
        out = []
        for name in sorted(set(packages) - before):
            base = VARIANT.sub("", name)
            if base != name and base in bases | set(packages):
                continue                      # name-debug o name-headers di un pacchetto già noto
            if re.fullmatch(r"linux-[a-z]+-\d+", base) and re.sub(r"-\d+$", "", base) in {re.sub(r"-\d+$", "", b) for b in bases}:
                score, extra = self.score_package, "versione nuova del kernel"
            else:
                codename = device_codename(base)
                if codename:
                    seen = self._known_codename(codename, before)
                    score = self.score_device_known if seen else self.score_device
                    extra = (f"supporto per un dispositivo, nome in codice «{codename}» già noto: {seen}" if seen
                             else f"supporto per un dispositivo, nome in codice «{codename}» mai visto prima")
                else:
                    score, extra = self.score_package, "pacchetto nuovo"
            desc = packages[name]
            text = f"SteamOS {repo}: {name}: {extra}" + (f" ({desc})" if desc else "")
            out.append(self._add(source="steamos_packages", change_type="steamos_package", keyword="steamos_package",
                                 token=f"steamos:{name}", text=text, score=score,
                                 path=f"steamos/{repo}", url=f"{MIRROR}{repo}/os/x86_64/", ref=f"steamos:{repo}:{name}",
                                 context={"archivio": repo, "pacchetto": name, "descrizione": desc}))
        return out


# ------------------------------------------------------------------ FCC
def parse_fcc(html: str, grantee: str) -> dict[str, dict[str, str]]:
    """codice FCC -> {data, descrizione}, dalla tabella della pagina del richiedente su fccid.io."""
    out: dict[str, dict[str, str]] = {}
    code = re.escape(grantee)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        m = re.search(rf'href="/({code}-?[A-Za-z0-9-]+)"', row)
        if not m:
            continue
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        day = re.search(r"\d{4}-\d{2}-\d{2}", " ".join(cells))
        purpose = cells[1] if len(cells) > 1 else ""
        purpose = re.sub(r"\s*(Original Equipment|Class II Permissive Change|Class I Permissive Change|Change in "
                         r"Identification)\s*$", "", purpose).strip()
        out[m.group(1)] = {"data": day.group(0) if day else "", "descrizione": purpose[:120]}
    return out


class FccIdsSource(_Base):
    name = "fcc"
    section = "fcc"
    last_key = "fcc:last_run"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        super().__init__(cfg, db, session)
        self.grantees: list[str] = list(self.scfg.get("grantees") or ["2AES4"])
        self.score = int(self.scfg.get("score_new", 80))

    def check(self) -> list[Finding]:
        found: list[Finding] = []
        for grantee in self.grantees:
            url = FCC_PAGE.format(grantee=grantee)
            ids = parse_fcc(_get(self.session, url).text, grantee)
            if not ids:
                raise RuntimeError(f"nessun codice FCC per {grantee}: formato della pagina cambiato?")
            key = f"fcc:{grantee}"
            raw = self.db.get_state(key)
            old = json.loads(raw) if raw else {}
            self.db.set_state(key, json.dumps({**old, **ids}, sort_keys=True))
            if raw is None:
                log.info("[fcc] %s: %d codici registrati", grantee, len(ids))
                continue
            for fcc_id in sorted(set(ids) - set(old)):
                info = ids[fcc_id]
                text = (f"FCC: dispositivo Valve nuovo {fcc_id}" + (f" ({info['descrizione']})" if info["descrizione"] else "")
                        + (f", data {info['data']}" if info["data"] else ""))
                found.append(self._add(source="fcc", change_type="fcc_id", keyword="fcc_id", token=f"fcc:{fcc_id.lower()}",
                                       text=text, score=self.score, path=f"fccid.io/{grantee}",
                                       url=f"https://fccid.io/{fcc_id}", ref=f"fcc:{fcc_id}",
                                       context={"codice": fcc_id, **info}))
        return found


def latest(db: Database) -> dict[str, Any]:
    """Per la pagina Hardware: i codici FCC registrati e i pacchetti di supporto dei dispositivi in SteamOS."""
    fcc: list[dict[str, str]] = []
    for row in db.conn.execute("SELECT key, value FROM state WHERE key LIKE 'fcc:%' AND key != 'fcc:last_run'"):
        for code, info in (json.loads(row[1]) or {}).items():
            fcc.append({"codice": code, **info})
    fcc.sort(key=lambda r: (r.get("data") or "", r["codice"]), reverse=True)
    devices: dict[str, list[str]] = {}
    for row in db.conn.execute("SELECT key, value FROM state WHERE key LIKE 'steamos:pkgs:%'"):
        for name in json.loads(row[1]) or {}:
            codename = device_codename(VARIANT.sub("", name))
            if codename:
                devices.setdefault(codename, [])
                if VARIANT.sub("", name) not in devices[codename]:
                    devices[codename].append(VARIANT.sub("", name))
    return {"fcc": fcc, "steamos_devices": {k: sorted(v) for k, v in sorted(devices.items())}}
