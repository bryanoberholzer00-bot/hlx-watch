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
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
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
        for domain in self.domains:
            after_key = f"certs:after:{domain}"
            try:
                certs, done = self.fetch(domain, self.db.get_state(after_key))
            except Exception as exc:
                log.warning("Cert Spotter: %s non letto: %s", domain, exc)
                self.errors.append(f"{domain}: {exc}")
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
        self.db.set_state(KNOWN_KEY, json.dumps(sorted(known)))
        if complete and not self.errors:
            self.db.set_state(LAST_KEY, iso(utcnow()))
            if first:
                self.db.set_state(READY_KEY, iso(utcnow()))
        if first:
            log.info("Certificati di Valve: %d nomi registrati, senza avvisi%s", len(known),
                     "" if complete else " (lettura da completare al prossimo giro)")
        return findings

    def _findings(self, new: list[str], bases: set[str], cert: dict[str, Any]) -> list[Finding]:
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
                                                  "domini_nel_certificato": len(cert.get("dns_names") or [])},
                                                 ensure_ascii=False))
            finding.build = build
            # possibile annuncio solo con un Half-Life nuovo nel nome (hlx, hl3, halflife3...), come per i post di Steam
            if announcement.enabled(self.cfg) and new_half_life(probe, " ".join(parts)):
                announcement.mark(finding, self.cfg)
            self.db.add_finding(finding, self.db.upsert_build(build))
            (log.warning if score >= 50 else log.info)("Certificati di Valve: %s", text)
            out.append(finding)
        return out
