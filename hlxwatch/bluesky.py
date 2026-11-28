"""Pubblicazione automatica su Bluesky (account BLUESKY_HANDLE, password per app BLUESKY_APP_PASSWORD).

Cosa si pubblica, in inglese e senza parole come "leak" o "confirmed":
  - le voci da `min_score` punti in su (100) appena mandate come avviso su Telegram, mai il rumore: un post per build
    (le due voci più forti, "+ N more"), con il link alla scheda della traccia o alla Ricerca del sito;
  - solo voci di modifiche recenti (`max_age_days`): un recupero storico o l'archeologia non pubblicano;
  - al massimo `max_per_day` post al giorno; un possibile annuncio passa sempre (fino a `max_announcements_per_day`);
  - nella serata vera di un evento (serate.json, mai la prova): un post all'inizio.

Doppioni: ogni post si annota nell'archivio (stato bluesky:pubblicati) e, prima di pubblicare, si leggono gli ultimi
post dell'account (API pubblica, senza accesso): se il link c'è già non si ripubblica. Serve se un giro pubblica e poi
fallisce prima di salvare l'archivio. Ci si collega solo quando c'è qualcosa da pubblicare (Bluesky limita gli accessi).

Bluesky è separato dai notificatori di Telegram (che ricevono anche i messaggi privati: credito, proposte, guasti):
se i segreti mancano o Bluesky non risponde si salta, senza toccare gli avvisi né far fallire il giro.
Con `dry_run: true` (o il comando `bluesky-prova`) i post si scrivono nel registro invece di pubblicarli.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

import requests

from hlxwatch.config import Config
from hlxwatch.models import Finding

log = logging.getLogger(__name__)

PDS = "https://bsky.social/xrpc"
PUBLIC = "https://public.api.bsky.app/xrpc"
STATE = "bluesky:pubblicati"          # {"chiavi": [...], "giorni": {"YYYY-MM-DD": {"n": 2, "annunci": 0}}}
KEEP = 400
MAX_CHARS = 290                       # Bluesky ne ammette 300 (grafemi): un margine per gli accenti
TITLES = {"cs2": "Counter-Strike 2", "dota2": "Dota 2", "deadlock": "Deadlock", "alyx": "Half-Life: Alyx",
          "steamvr": "SteamVR", "steamclient": "the Steam client", "protobufs": "Steam's network definitions",
          "steamworks": "Steamworks", "steamhelp": "Steam Support", "valveweb": "Valve's websites",
          "valvesoftware": "Valve's GitHub", "steamtracking": "SteamTracking", "tf2": "Team Fortress 2"}


def settings(cfg: Config) -> dict[str, Any]:
    raw = cfg.get("bluesky", {}) or {}
    return {"enabled": bool(raw.get("enabled", False)), "dry_run": bool(raw.get("dry_run", False)),
            "min_score": int(raw.get("min_score", 100)), "max_per_day": int(raw.get("max_per_day", 3)),
            "max_announcements_per_day": int(raw.get("max_announcements_per_day", 6)),
            "max_age_days": int(raw.get("max_age_days", 3)), "serate": bool(raw.get("serate", True))}


# ------------------------------------------------------------------ testo dei post
def title(cfg: Config, game: str) -> str:
    return TITLES.get(game) or cfg.game_title(game)


def why(f: Finding) -> str:
    """Perché conta, in poche parole e con prudenza."""
    from hlxwatch.announcement import KEYWORD
    kws = set(f.keywords or [])
    if KEYWORD in kws:
        return "a possible Valve announcement"
    if kws & {"hl3", "half_life_3"}:
        return "it mentions Half-Life 3"
    if any(k.startswith("hlx") for k in kws):
        return "HLX is the reported codename of Valve's next Half-Life project"
    if "hl_universe" in kws or "hl_nomi_attaccati" in kws:
        return "it names Half-Life characters, creatures or places"
    return "a high-relevance change in Valve's public files"


def link(cfg: Config, f: Finding) -> str:
    if f.card:
        return f.card
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    site = site if site.endswith("/") else site + "/"
    return f"{site}search.html?q={quote((f.token or f.text or '')[:60])}"


def _label(f: Finding, n: int = 70) -> str:
    return " ".join((f.token or f.text or "").split())[:n]


def alert_text(cfg: Config, items: list[Finding]) -> tuple[str, str]:
    """(testo, link) di un post per un gruppo di voci della stessa build."""
    from hlxwatch.announcement import KEYWORD
    from hlxwatch.fase import piece_key
    # lo stesso pezzo scritto in modi diversi conta una volta, col nome più corto e il punteggio più alto
    # (CHLXCoverPreviewHelper e GetAttributeTable_CHLXCoverPreviewHelper)
    pieces: dict[str, Finding] = {}
    for f in sorted(items, key=lambda f: (-f.score, len(_label(f)))):
        key = piece_key(_label(f, 200))
        if key not in pieces:
            pieces[key] = f
        elif len(_label(f)) < len(_label(pieces[key])):
            f.score, f.card = max(f.score, pieces[key].score), f.card or pieces[key].card
            pieces[key] = f
    items = sorted(pieces.values(), key=lambda f: (-f.score, len(_label(f))))
    top = items[0]
    url = link(cfg, top)
    where = title(cfg, top.game)
    head = ("Possible Valve announcement" if KEYWORD in (top.keywords or []) else f"New in {where}")
    names = ", ".join(_label(f, 60) for f in items[:2])
    more = f" (+{len(items) - 2} more)" if len(items) > 2 else ""
    body = f"{head}: {names}{more}. Why it matters: {why(top)}."
    if KEYWORD in (top.keywords or []):
        body = f"{head} in {where}: {names}{more}. Not verified yet."
    room = MAX_CHARS - len(url) - 1
    if len(body) > room:
        body = body[: room - 1].rstrip() + "…"
    return f"{body}\n{url}", url


def serata_text(cfg: Config, s: dict[str, Any]) -> tuple[str, str]:
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    site = site if site.endswith("/") else site + "/"
    url = f"{site}previsioni.html?serata={quote(str(s.get('id', '')))}#serata"   # la query rende unico il link di ogni serata
    body = (f"{s.get('nome', 'The show')} is tonight. HLX Watch checks Valve's sites, Steam and public game files "
            "every 10 minutes and will post here if anything Half-Life related shows up.")
    return f"{body}\n{url}", url


# ------------------------------------------------------------------ scelta
def _state(db: Any) -> dict[str, Any]:
    try:
        data = json.loads(db.get_state(STATE) or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("chiavi", [])
    data.setdefault("giorni", {})
    return data


def _recent(f: Finding, days: int, now: datetime) -> bool:
    when = getattr(f.build, "date", None) if f.build else None
    if when is None:
        return True                        # voci senza build (app nuove, siti): sono di questo giro
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when <= timedelta(days=days)


def plan(cfg: Config, db: Any, alerts: Iterable[Finding], now: datetime | None = None) -> list[dict[str, Any]]:
    """I post da fare in questo giro (senza pubblicare): [{"chiave", "testo", "link", "annuncio"}]."""
    from hlxwatch.announcement import KEYWORD
    s = settings(cfg)
    now = now or datetime.now(timezone.utc)
    state = _state(db)
    done = set(state["chiavi"])
    day = state["giorni"].get(now.date().isoformat(), {})
    normal_left = s["max_per_day"] - int(day.get("n", 0))
    ann_left = s["max_announcements_per_day"] - int(day.get("annunci", 0))
    out: list[dict[str, Any]] = []
    if s["serate"]:
        from hlxwatch import serata
        for sr in serata.load():
            if sr.get("prova"):
                continue                       # la prova generale non si pubblica
            try:
                start, end = (datetime.fromisoformat(sr[k].replace("Z", "+00:00")) for k in ("inizio", "fine"))
            except (KeyError, ValueError):
                continue
            key = f"serata:{sr.get('id')}"
            if start <= now < end and key not in done:
                text, url = serata_text(cfg, sr)
                out.append({"chiave": key, "testo": text, "link": url, "annuncio": False, "serata": True})
    groups: dict[str, list[Finding]] = {}
    for f in alerts or []:
        if int(f.score or 0) < s["min_score"] or not _recent(f, s["max_age_days"], now):
            continue
        key = f"build:{f.build_id}" if f.build_id is not None else f"voce:{f.game}:{(f.token or f.text or '')[:80]}"
        groups.setdefault(key, []).append(f)
    for key, items in sorted(groups.items(), key=lambda kv: -max(f.score for f in kv[1])):
        if key in done:
            continue
        ann = any(KEYWORD in (f.keywords or []) for f in items)
        if ann and ann_left <= 0 or not ann and normal_left <= 0:
            log.info("Bluesky: limite di post del giorno raggiunto, salto %s", key)
            continue
        text, url = alert_text(cfg, items)
        out.append({"chiave": key, "testo": text, "link": url, "annuncio": ann, "serata": False})
        if ann:
            ann_left -= 1
        else:
            normal_left -= 1
    return out


def _record(db: Any, posts: list[dict[str, Any]], now: datetime) -> None:
    state = _state(db)
    day = state["giorni"].setdefault(now.date().isoformat(), {"n": 0, "annunci": 0})
    for p in posts:
        state["chiavi"].append(p["chiave"])
        if p.get("annuncio"):
            day["annunci"] = int(day.get("annunci", 0)) + 1
        elif not p.get("serata"):
            day["n"] = int(day.get("n", 0)) + 1
    state["chiavi"] = state["chiavi"][-KEEP:]
    state["giorni"] = {k: v for k, v in state["giorni"].items() if k >= (now.date() - timedelta(days=7)).isoformat()}
    db.set_state(STATE, json.dumps(state))


# ------------------------------------------------------------------ Bluesky
class Client:
    def __init__(self, handle: str, password: str, session: Any = None, timeout: float = 20):
        self.handle = handle
        self.password = password
        self.http = session or requests.Session()
        self.timeout = timeout
        self.jwt: str | None = None
        self.did: str | None = None

    def recent_links(self, limit: int = 40) -> set[str]:
        """I link negli ultimi post dell'account (lettura pubblica, senza accesso)."""
        r = self.http.get(f"{PUBLIC}/app.bsky.feed.getAuthorFeed",
                          params={"actor": self.handle, "limit": limit, "filter": "posts_no_replies"}, timeout=self.timeout)
        r.raise_for_status()
        links: set[str] = set()
        for item in r.json().get("feed", []):
            rec = (item.get("post") or {}).get("record") or {}
            for facet in rec.get("facets") or []:
                for feat in facet.get("features") or []:
                    if feat.get("uri"):
                        links.add(feat["uri"])
            for word in str(rec.get("text") or "").split():
                if word.startswith("http"):
                    links.add(word)
        return links

    def login(self) -> None:
        r = self.http.post(f"{PDS}/com.atproto.server.createSession",
                           json={"identifier": self.handle, "password": self.password}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        self.jwt, self.did = data["accessJwt"], data["did"]

    def post(self, text: str, url: str) -> str:
        if not self.jwt:
            self.login()
        raw = text.encode("utf-8")
        start = raw.find(url.encode("utf-8"))
        record: dict[str, Any] = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
                                  "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        if start >= 0:
            record["facets"] = [{"index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
                                 "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}]
        r = self.http.post(f"{PDS}/com.atproto.repo.createRecord", headers={"Authorization": f"Bearer {self.jwt}"},
                           json={"repo": self.did, "collection": "app.bsky.feed.post", "record": record},
                           timeout=self.timeout)
        r.raise_for_status()
        return str(r.json().get("uri", ""))


def publish(cfg: Config, db: Any, alerts: Iterable[Finding], *, client: Client | None = None,
            now: datetime | None = None, dry_run: bool | None = None) -> dict[str, Any]:
    """Pubblica i post di questo giro. Non solleva eccezioni: l'esito è nel risultato e nel registro."""
    s = settings(cfg)
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {"post": [], "pubblicati": 0, "errore": None, "tentato": False}
    if not s["enabled"]:
        return result
    try:
        posts = plan(cfg, db, alerts, now)
    except Exception as exc:                          # un errore nel calcolo non deve fermare il giro
        log.exception("Bluesky: scelta dei post non riuscita")
        result["errore"] = str(exc)
        return result
    result["post"] = posts
    if not posts:
        return result
    if s["dry_run"] if dry_run is None else dry_run:
        for p in posts:
            log.info("Bluesky (prova a vuoto, non pubblicato):\n%s", p["testo"])
        return result
    handle, password = cfg.env("BLUESKY_HANDLE"), cfg.env("BLUESKY_APP_PASSWORD")
    if client is None:
        if not handle or not password:
            log.info("Bluesky: BLUESKY_HANDLE o BLUESKY_APP_PASSWORD mancano, salto %d post", len(posts))
            return result
        client = Client(handle, password)
    result["tentato"] = True
    try:
        seen = client.recent_links()
    except Exception as exc:
        log.warning("Bluesky: ultimi post non letti (%s), salto per non rischiare doppioni", exc)
        result["errore"] = f"lettura dei post: {exc}"
        return result
    done = []
    for p in posts:
        if p["link"] in seen:
            log.info("Bluesky: %s già pubblicato (link presente), lo segno e salto", p["chiave"])
            done.append(p)
            continue
        try:
            uri = client.post(p["testo"], p["link"])
            log.info("Bluesky: pubblicato %s (%s)", p["chiave"], uri)
            done.append(p)
            result["pubblicati"] += 1
        except Exception as exc:
            log.warning("Bluesky: pubblicazione di %s non riuscita: %s", p["chiave"], exc)
            result["errore"] = str(exc)
            break
    if done:
        _record(db, done, now)
    return result
