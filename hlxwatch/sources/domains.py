"""Domini: chi possiede i nomi che Valve userebbe, e i domini appena registrati con i server di Valve.

Il caso di Deadlock: playdeadlock.com è stato registrato il 5 aprile 2024, più di quattro mesi prima dell'annuncio
(23 agosto 2024), con gli stessi server DNS di half-life.com (Akamai a1-194 / a2-64). Due controlli, una volta al giorno:

1. Domini sorvegliati (`domains.watch`): con RDAP, il servizio pubblico dei registri fatto per i programmi, si legge
   registrar, server DNS e data di registrazione. Un dominio che passa ai DNS di Valve è un segnale forte (con HLX o
   Half-Life 3 nel nome, un possibile annuncio); un cambio di registrar o una registrazione nuova sono voci con avviso;
   altri cambi di DNS restano in archivio. half-life3.com, per esempio, è registrato dal 1999 presso SafeNames (lo
   stesso registrar di playdeadlock.com) ma è parcheggiato: se un giorno passa ai DNS di Valve, lo si vede qui.
2. Domini appena registrati: WhoisDS pubblica gratis ogni giorno una lista (circa 70.000 nomi, parziale; il suo
   robots.txt lo permette). Si tengono i nomi che corrispondono a `domains.new_pattern` e si controllano con RDAP:
   solo quelli con i DNS o il registrar di Valve diventano voci. Gli altri (siti di fan) si ignorano.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import zipfile
from datetime import timedelta
from typing import Any

import requests

from hlxwatch import __version__, announcement
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.steam_news import new_half_life
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

GAME = "valveweb"
RDAP = "https://rdap.org/domain/"                      # solo se il registro del dominio non è nell'elenco IANA
BOOTSTRAP = "https://data.iana.org/rdap/dns.json"     # elenco ufficiale: dominio di primo livello -> registro RDAP
NRD = "https://www.whoisds.com//whois-database/newly-registered-domains/{key}/nrd"
LAST_KEY = "domains:last_run"
NRD_KEY = "domains:nrd_last_day"
DEFAULT_WATCH = ["half-life3.com", "halflife3.com", "half-life-3.com", "halflife-3.com", "hl3.com", "hlx.com",
                 "halflifex.com", "half-lifex.com", "halflife.com"]
DEFAULT_NS = ["a1-194.akam.net", "a2-64.akam.net"]
DEFAULT_REGISTRARS = ["markmonitor", "safenames"]
class RateLimited(RuntimeError):
    """Il registro chiede di rallentare: si smette per questo giro e si riprova al prossimo."""


DEFAULT_PATTERN = r"half-?life|\bhlx|hlx\b|^hl-?3|aperture-?science|valve-?(?:software|games)|^play[a-z]+\.com$"


def domain_words(domain: str) -> str:
    """'half-life3.com' -> 'half life3' per le regole sui nomi."""
    return " ".join(domain.lower().rsplit(".", 1)[0].replace("-", " ").replace(".", " ").split())


class DomainsSource:
    name = "domains"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.dcfg: dict[str, Any] = cfg.get("domains", {}) or {}
        self.watch = [str(d).lower() for d in self.dcfg.get("watch", DEFAULT_WATCH)]
        self.valve_ns = {str(n).lower() for n in self.dcfg.get("valve_nameservers", DEFAULT_NS)}
        self.valve_registrars = [str(r).lower() for r in self.dcfg.get("valve_registrars", DEFAULT_REGISTRARS)]
        self.pattern = re.compile(str(self.dcfg.get("new_pattern", DEFAULT_PATTERN)), re.IGNORECASE)
        self.interval = timedelta(hours=float(self.dcfg.get("interval_hours", 24)))
        self.max_checks = int(self.dcfg.get("max_new_checks", 40))
        self.nrd_enabled = bool(self.dcfg.get("newly_registered", True))
        self.pause = float(self.dcfg.get("pause_seconds", 1.5))
        self._servers: dict[str, str] | None = None
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", f"hlx-watch/{__version__}")
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.dcfg.get("enabled", True))

    def due(self) -> bool:
        last = parse_iso(self.db.get_state(LAST_KEY))
        return last is None or utcnow() - last >= self.interval

    # ------------------------------------------------------------------ RDAP
    def server(self, domain: str) -> str:
        """Il registro RDAP del dominio (per .com e .net Verisign), dall'elenco IANA letto una volta per giro."""
        if self._servers is None:
            self._servers = {}
            try:
                r = self.session.get(BOOTSTRAP, timeout=30)
                for tlds, urls in (r.json() or {}).get("services", []) if r.status_code == 200 else []:
                    for tld in tlds:
                        self._servers[tld.lower()] = urls[0].rstrip("/") + "/domain/"
            except Exception as exc:                          # senza elenco si passa da rdap.org
                log.info("Elenco IANA dei registri non letto: %s", exc)
        return self._servers.get(domain.rsplit(".", 1)[-1].lower(), RDAP)

    def rdap(self, domain: str) -> dict[str, Any] | None:
        """Dati pubblici del dominio; {"registrato": False} se libero. Errore se il registro non risponde."""
        if self.pause:
            time.sleep(self.pause)                            # i registri limitano le richieste ravvicinate
        r = self.session.get(self.server(domain) + domain, timeout=30, allow_redirects=True,
                             headers={"Accept": "application/rdap+json"})
        if r.status_code == 429:
            raise RateLimited(domain)
        if r.status_code == 404:
            return {"registrato": False}
        if r.status_code >= 400:
            raise RuntimeError(f"RDAP ha risposto {r.status_code} per {domain}")
        data = r.json() or {}
        registrar = ""
        for entity in data.get("entities") or []:
            if "registrar" in (entity.get("roles") or []):
                vcard = (entity.get("vcardArray") or [None, []])[1]
                registrar = next((str(v[3]) for v in vcard if v and v[0] == "fn"), "")
        events = {e.get("eventAction"): str(e.get("eventDate") or "")[:10] for e in data.get("events") or []}
        return {"registrato": True, "registrar": registrar,
                "ns": sorted(str(n.get("ldhName") or "").lower().rstrip(".") for n in data.get("nameservers") or []),
                "registrazione": events.get("registration", "")}

    def valve_dns(self, info: dict[str, Any]) -> bool:
        return bool(self.valve_ns) and self.valve_ns <= set(info.get("ns") or [])

    def valve_registrar(self, info: dict[str, Any]) -> bool:
        return any(r in str(info.get("registrar") or "").lower() for r in self.valve_registrars)

    # ------------------------------------------------------------------ run
    def run(self, force: bool = False) -> list[Finding]:
        self.errors = []
        if not self.enabled or (not force and not self.due()):
            return []
        findings = self.check_watch()
        if self.nrd_enabled:
            try:
                findings += self.check_new()
            except Exception as exc:
                log.warning("Domini appena registrati non letti: %s", exc)
                self.errors.append(f"domini nuovi: {exc}")
        if not self.errors:
            self.db.set_state(LAST_KEY, iso(utcnow()))
        return findings

    def check_watch(self) -> list[Finding]:
        findings: list[Finding] = []
        for domain in self.watch:
            key = f"domains:watch:{domain}"
            try:
                info = self.rdap(domain)
            except Exception as exc:
                log.warning("RDAP: %s non letto: %s", domain, exc)
                self.errors.append(f"{domain}: {exc}")
                continue
            raw = self.db.get_state(key)
            self.db.set_state(key, json.dumps(info, sort_keys=True))
            if raw is None:
                continue                                  # prima lettura: solo registrazione
            old = json.loads(raw)
            change = self._change(domain, old, info)
            if change:
                findings.append(self._finding(domain, info, *change, old=old))
        return findings

    def _change(self, domain: str, old: dict[str, Any], new: dict[str, Any]) -> tuple[str, int] | None:
        if old == new:
            return None
        if not new.get("registrato"):
            return f"{domain} non è più registrato (scaduto o liberato)", 30
        if not old.get("registrato"):
            what = f"{domain} è stato registrato ({new.get('registrar') or 'registrar ignoto'})"
            return (what + ", con i server DNS di Valve", 150) if self.valve_dns(new) else (what, 70)
        if self.valve_dns(new) and not self.valve_dns(old):
            return f"{domain} ora usa i server DNS di Valve ({', '.join(new.get('ns') or [])})", 150
        if (new.get("registrar") or "") != (old.get("registrar") or ""):
            return f"{domain}: registrar cambiato da {old.get('registrar') or '?'} a {new.get('registrar') or '?'}", 80
        if new.get("ns") != old.get("ns"):
            return f"{domain}: server DNS cambiati in {', '.join(new.get('ns') or []) or 'nessuno'}", 40
        return None                                       # cambia solo qualcosa che non conta

    # ------------------------------------------------------------------ domini appena registrati
    def nrd_names(self, day: str) -> list[str]:
        key = base64.b64encode(f"{day}.zip".encode()).decode()
        r = self.session.get(NRD.format(key=key), timeout=60)
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise RuntimeError(f"WhoisDS ha risposto {r.status_code} per il {day}")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return [n.strip().lower() for name in z.namelist()
                    for n in z.read(name).decode("utf-8", "replace").split() if n.strip()]

    def check_new(self) -> list[Finding]:
        """Le liste dei giorni non ancora letti (al massimo gli ultimi 3, quella di oggi esce domani)."""
        today = utcnow().date()
        last = self.db.get_state(NRD_KEY)
        # la prima volta solo la lista di ieri; poi i giorni non ancora letti (al massimo gli ultimi 3)
        days = [today - timedelta(days=d) for d in ((3, 2, 1) if last else (1,))]
        days = [d for d in days if not last or d.isoformat() > last]
        findings: list[Finding] = []
        checks = 0
        stopped = False
        for day in days:
            names = self.nrd_names(day.isoformat())
            # prima i nomi con Half-Life o HLX, poi i play*.com (lo schema dei siti dei giochi: playdeadlock.com)
            hits = sorted((n for n in names if self.pattern.search(n)), key=lambda n: n.startswith("play"))
            log.info("Domini registrati il %s: %d nella lista, %d con nomi da controllare", day, len(names), len(hits))
            for domain in hits:
                if checks >= self.max_checks:
                    log.info("Domini nuovi: raggiunto il limite di %d controlli RDAP", self.max_checks)
                    break
                checks += 1
                try:
                    info = self.rdap(domain)
                except RateLimited:
                    log.info("RDAP: il registro chiede di rallentare, la lista del %s si riprende al prossimo giro", day)
                    stopped = True
                    break
                except Exception as exc:
                    log.info("RDAP: %s non letto: %s", domain, exc)
                    continue
                if not info.get("registrato"):
                    continue
                if self.valve_dns(info):
                    findings.append(self._finding(domain, info, f"dominio nuovo {domain} con i server DNS di Valve", 150))
                elif self.valve_registrar(info):
                    findings.append(self._finding(domain, info,
                                                  f"dominio nuovo {domain} presso {info.get('registrar')}, "
                                                  "un registrar usato da Valve", 60))
            if stopped:
                break
            if names:
                self.db.set_state(NRD_KEY, day.isoformat())
        return findings

    # ------------------------------------------------------------------ voce
    def _finding(self, domain: str, info: dict[str, Any], text: str, score: int,
                 old: dict[str, Any] | None = None) -> Finding:
        url = f"https://rdap.org/domain/{domain}"
        build = BuildInfo(game=GAME, source="domains", ref=f"rdap:{domain}:{utcnow().date()}", date=utcnow(), url=url,
                          message=text[:500])
        finding = Finding(game=GAME, source="domains", file_path=domain, change_type="domain",
                          text=f"Registro dei domini: {text}", score=score, keywords=["cert_domain", "rdap"],
                          token=f"rdap:{domain}:{text.lower()}"[:300],
                          context=json.dumps({"ora": info, "prima": old}, ensure_ascii=False))
        finding.build = build
        words = domain_words(domain)
        if score >= 150 and announcement.enabled(self.cfg) and new_half_life(words, words):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        (log.warning if score >= 50 else log.info)("Domini: %s", text)
        return finding
