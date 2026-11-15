"""Domini nei certificati HTTPS di Valve: il sito di un gioco nuovo si prepara prima dell'annuncio.

Valve protegge i siti di prova di tutti i suoi giochi (beta.* e preview.*) con un solo certificato, rinnovato ogni poche
settimane, che elenca ogni dominio: half-life.com, playdeadlock.com, steamdeck.com, aperturescience.com... (58 nomi a
fine ottobre 2026). I certificati sono pubblici per legge nei registri di Certificate Transparency.

Qui si usa l'API di Cert Spotter (SSLMate), che permette poche richieste anonime all'ora per uso personale: una
richiesta per dominio ogni `interval_hours`, e dopo la prima solo i certificati nuovi (parametro `after`). Con una
chiave gratuita (variabile CERTSPOTTER_API_KEY) i limiti sono più alti. Non si usa crt.sh: il suo robots.txt lo vieta.
  - primo giro: registra tutti i nomi e basta;
  - poi un dominio mai visto (es. un nuovo gioco.com) è una voce sopra la soglia di notifica; con HLX o Half-Life nel
    nome è un possibile annuncio. Un sottodominio nuovo di un dominio già noto resta in archivio con pochi punti,
    a meno che le parole chiave lo valutino di più.

Riserva, quando Cert Spotter non risponde per nessun dominio (il 15/11/2026 SSLMate ha sospeso il piano gratuito per
carico, errore 503 "service_outage"): ci si collega ai siti di Valve come un browser e si leggono i nomi nel certificato
che servono. Quasi tutti i siti dei giochi (half-life.com, steampowered.com, counter-strike.net, aperturescience.com...)
usano lo stesso certificato, con 48 nomi a novembre 2026: un sito nuovo messo in linea compare lì. Vede meno di Cert
Spotter (solo i certificati in uso, non quelli appena emessi né quelli dei siti di prova beta.* e preview.*, che non
sono raggiungibili), ma non dipende da nessun servizio esterno. La prima lettura registra i nomi senza avvisi; i nomi
nuovi entrano tra quelli noti, così quando Cert Spotter riparte non avvisano una seconda volta.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import ssl
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from hlxwatch import __version__, announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.steam_news import new_half_life
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

API = "https://api.certspotter.com/v1/issuances"
GAME = "valveweb"
KNOWN_KEY = "certs:known"
LAST_KEY = "certs:last_run"
READY_KEY = "certs:ready"          # la prima lettura completa (solo registrazione) è finita
MAX_PAGES = 10
TLS_SEEN = "certs:tls:visti"       # nomi letti dai certificati serviti dai siti (riserva)
FALLBACK_HOSTS = ["half-life.com", "steampowered.com"]


def served_certificate(host: str, timeout: float = 10) -> dict[str, Any]:
    """Il certificato che il sito serve adesso: nomi, numero di serie, valido dal."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert() or {}
    names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    since = None
    if cert.get("notBefore"):
        since = iso(datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notBefore"]), tz=timezone.utc))
    return {"id": f"tls:{cert.get('serialNumber', '')}", "not_before": since, "dns_names": names}


def normalize(name: str) -> str:
    name = name.strip().lower().rstrip(".")
    return name[2:] if name.startswith("*.") else name


def base_domain(name: str) -> str:
    """Il dominio registrato (gli ultimi due pezzi): beta.half-life.com -> half-life.com."""
    parts = normalize(name).split(".")
    return ".".join(parts[-2:])


class ValveCertsSource:
    name = "certs"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.ccfg: dict[str, Any] = cfg.get("valve_certs", {}) or {}
        self.domains = [str(d) for d in self.ccfg.get("domains", ["half-life.com", "valvesoftware.com"])]
        self.interval = timedelta(hours=float(self.ccfg.get("interval_hours", 3)))
        self.score_domain = int(self.ccfg.get("score_new_domain", 120))
        self.score_sub = int(self.ccfg.get("score_new_subdomain", 30))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", f"hlx-watch/{__version__}")
        key = os.environ.get("CERTSPOTTER_API_KEY")
        if key:
            self.session.headers["Authorization"] = f"Bearer {key}"
        self.matcher = KeywordMatcher(cfg.keywords)
        self.errors: list[str] = []
        self.fallback_hosts = [str(h) for h in self.ccfg.get("fallback_hosts", FALLBACK_HOSTS)]
        self.read_served = served_certificate

    @property
    def enabled(self) -> bool:
        return bool(self.ccfg.get("enabled", True))

    def due(self) -> bool:
        last = parse_iso(self.db.get_state(LAST_KEY))
        return last is None or utcnow() - last >= self.interval

    # ------------------------------------------------------------------ API
    def fetch(self, domain: str, after: str | None) -> tuple[list[dict[str, Any]], bool]:
        """Certificati nuovi del dominio e dei sottodomini. Ritorna (certificati, completo)."""
        out: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            params = {"domain": domain, "include_subdomains": "true", "expand": "dns_names"}
            if after:
                params["after"] = after
            r = self.session.get(API, params=params, timeout=30)
            if r.status_code == 429:
                log.info("Cert Spotter: limite di richieste raggiunto, si riprova al prossimo giro")
                return out, False
            if r.status_code >= 400:
                raise RuntimeError(f"Cert Spotter ha risposto {r.status_code} per {domain}")
            page = r.json() or []
            if not page:
                return out, True
            out += page
            after = str(page[-1]["id"])
        return out, False

    # ------------------------------------------------------------------ run
    def run(self, force: bool = False) -> list[Finding]:
        self.errors = []
        if not self.enabled or (not force and not self.due()):
            return []
        raw = self.db.get_state(KNOWN_KEY)
        known: set[str] = set(json.loads(raw)) if raw else set()
        first = self.db.get_state(READY_KEY) is None      # anche i giri che completano la prima lettura
        bases = {base_domain(n) for n in known}
        findings: list[Finding] = []
        complete = True
        failed = 0
        for domain in self.domains:
            after_key = f"certs:after:{domain}"
            try:
                certs, done = self.fetch(domain, self.db.get_state(after_key))
            except Exception as exc:
                log.warning("Cert Spotter: %s non letto: %s", domain, exc)
                self.errors.append(f"{domain}: {exc}")
                failed += 1
                continue
            complete &= done
            for cert in certs:
                names = sorted({normalize(n) for n in cert.get("dns_names") or []})
                new = [n for n in names if n not in known]
                if not new:
                    continue
                known.update(new)
                if first:
                    continue
                findings += self._findings(new, bases, cert)
                bases.update(base_domain(n) for n in new)
            if certs:
                self.db.set_state(after_key, str(certs[-1]["id"]))
        if self.domains and failed == len(self.domains) and not first:
            findings += self.fallback(known, bases)
        self.db.set_state(KNOWN_KEY, json.dumps(sorted(known)))
        if complete and not self.errors:
            self.db.set_state(LAST_KEY, iso(utcnow()))
            if first:
                self.db.set_state(READY_KEY, iso(utcnow()))
        if first:
            log.info("Certificati di Valve: %d nomi registrati, senza avvisi%s", len(known),
                     "" if complete else " (lettura da completare al prossimo giro)")
        return findings

    def fallback(self, known: set[str], bases: set[str]) -> list[Finding]:
        """Riserva: i nomi nei certificati che i siti di Valve servono adesso (aggiorna known e bases)."""
        raw = self.db.get_state(TLS_SEEN)
        first = raw is None
        seen: set[str] = set(json.loads(raw)) if raw else set()
        findings: list[Finding] = []
        read = []
        for host in self.fallback_hosts:
            try:
                cert = self.read_served(host)
            except Exception as exc:
                log.warning("Certificati di Valve, riserva: %s non letto: %s", host, exc)
                continue
            read.append(host)
            names = sorted({normalize(n) for n in cert.get("dns_names") or []})
            new = [n for n in names if n not in known and n not in seen]
            seen.update(names)
            if new and not first:
                known.update(new)
                findings += self._findings(new, bases, cert, via=host)
                bases.update(base_domain(n) for n in new)
        if read:
            self.db.set_state(TLS_SEEN, json.dumps(sorted(seen)))
            note = f"riserva attiva: letti i certificati di {', '.join(read)}"
            self.errors = [f"{e} ({note})" for e in self.errors]
            log.info("Certificati di Valve: Cert Spotter non risponde, %s (%d nomi%s)", note, len(seen),
                     ", prima lettura senza avvisi" if first else "")
        return findings

    def _findings(self, new: list[str], bases: set[str], cert: dict[str, Any], via: str | None = None) -> list[Finding]:
        by_base: dict[str, list[str]] = {}
        for name in new:
            by_base.setdefault(base_domain(name), []).append(name)
        out: list[Finding] = []
        date = parse_iso(cert.get("not_before")) or utcnow()
        for base, names in sorted(by_base.items()):
            fresh_domain = base not in bases
            subject = base if fresh_domain else names[0]
            if fresh_domain:
                text = f"Dominio nuovo nei certificati di Valve: {base}"
                score, keywords = self.score_domain, ["cert_domain", "domain_new"]
            else:
                text = f"Sottodominio nuovo nei certificati di Valve: {', '.join(names[:5])}"
                score, keywords = self.score_sub, ["cert_domain", "subdomain_new"]
            # di un sottodominio conta solo la parte nuova: "store" in store.half-life.com, non "half-life"
            parts = [n if fresh_domain else n[:-len(base)].rstrip(".") for n in names]
            probe = " ".join(p.replace(".", " ").replace("-", " ") for p in parts)
            res = self.matcher.analyze_line(f"{probe} {' '.join(parts)}", file_path=base, change_type="added")
            if res:
                score = max(score, res.score + 20)
                keywords += res.keywords
            url = f"https://sslmate.com/certspotter/search?q={base}"
            build = BuildInfo(game=GAME, source="certs", ref=f"cert:{cert.get('id')}:{base}", date=date, url=url,
                              message=text[:500])
            finding = Finding(game=GAME, source="certs", file_path=subject, change_type="domain", text=text,
                              score=min(score, 250), keywords=keywords, token=f"domain:{subject}",
                              context=json.dumps({"nomi": names, "certificato": cert.get("id"),
                                                  "valido_dal": cert.get("not_before"),
                                                  "domini_nel_certificato": len(cert.get("dns_names") or []),
                                                  **({"letto_da": f"certificato servito da {via} (riserva: Cert "
                                                                  "Spotter non rispondeva)"} if via else {})},
                                                 ensure_ascii=False))
            finding.build = build
            # possibile annuncio solo con un Half-Life nuovo nel nome (hlx, hl3, halflife3...), come per i post di Steam
            if announcement.enabled(self.cfg) and new_half_life(probe, " ".join(parts)):
                announcement.mark(finding, self.cfg)
            self.db.add_finding(finding, self.db.upsert_build(build))
            (log.warning if score >= 50 else log.info)("Certificati di Valve: %s", text)
            out.append(finding)
        return out
