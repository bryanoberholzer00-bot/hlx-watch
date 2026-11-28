"""Pubblicazione automatica su Bluesky (account BLUESKY_HANDLE, password per app BLUESKY_APP_PASSWORD).

Cosa si pubblica, in inglese e senza parole come "leak" o "confirmed", con gli hashtag #HalfLife #Valve #HLX (veri
hashtag di Bluesky: una faccetta di tipo tag per ognuno) e #TheGameAwards nella serata dei Game Awards:
  - le voci da `min_score` punti in su (100) appena mandate come avviso su Telegram, mai il rumore: un post per build
    (le due voci più forti, "+ N more"), con il link alla scheda della traccia o alla Ricerca del sito;
  - solo voci di modifiche recenti (`max_age_days`): un recupero storico o l'archeologia non pubblicano;
  - al massimo `max_per_day` post al giorno; un possibile annuncio passa sempre (fino a `max_announcements_per_day`);
  - nella serata vera di un evento (serate.json, mai la prova): un post all'inizio e i possibili annunci trovati dal
    bot su Cloudflare (pagina nuova su half-life.com, prodotto nuovo nella pagina di Valve su Steam, video nuovo),
    letti dal suo elenco pubblico /fast/annuncio.json. Il bot non pubblica su Bluesky: lo fa solo il giro, così c'è
    un solo pubblicatore (al prezzo di una decina di minuti di ritardo rispetto a Telegram).

Doppioni: ogni post si annota nell'archivio (stato bluesky:pubblicati) e, prima di pubblicare, si leggono gli ultimi
post dell'account (API pubblica, senza accesso): se il link c'è già non si ripubblica (serve se un giro pubblica e poi
fallisce prima di salvare l'archivio). Inoltre al massimo un "Possible Valve announcement" ogni ANNOUNCE_GAP: lo stesso
annuncio trovato da due fonti, con due link diversi, non diventa due post. Ci si collega solo quando c'è qualcosa da
pubblicare (Bluesky limita gli accessi).

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
HASHTAGS = ["HalfLife", "Valve", "HLX"]
TGA_TAG = "TheGameAwards"
ANNOUNCE = "Possible Valve announcement"
ANNOUNCE_GAP = timedelta(hours=3)
BOT_KINDS = ("web", "video", "store")   # annunci del bot: pagina su half-life.com, video nuovo, prodotto su Steam
TITLES = {"cs2": "Counter-Strike 2", "dota2": "Dota 2", "deadlock": "Deadlock", "alyx": "Half-Life: Alyx",
          "steamvr": "SteamVR", "steamclient": "the Steam client", "protobufs": "Steam's network definitions",
          "steamworks": "Steamworks", "steamhelp": "Steam Support", "valveweb": "Valve's websites",
          "valvesoftware": "Valve's GitHub", "steamtracking": "SteamTracking", "tf2": "Team Fortress 2"}


def settings(cfg: Config) -> dict[str, Any]:
    raw = cfg.get("bluesky", {}) or {}
    status = str((cfg.get("fast_check", {}) or {}).get("status_url") or "")
    bot_url = raw.get("bot_url") or (status.rsplit("/", 1)[0] + "/annuncio.json" if status else "")
    return {"enabled": bool(raw.get("enabled", False)), "dry_run": bool(raw.get("dry_run", False)),
            "min_score": int(raw.get("min_score", 100)), "max_per_day": int(raw.get("max_per_day", 3)),
            "max_announcements_per_day": int(raw.get("max_announcements_per_day", 6)),
            "max_age_days": int(raw.get("max_age_days", 3)), "serate": bool(raw.get("serate", True)),
            "bot_annunci": bool(raw.get("bot_annunci", True)), "bot_url": str(bot_url)}


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


def _site(cfg: Config) -> str:
    site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
    return site if site.endswith("/") else site + "/"


def link(cfg: Config, f: Finding) -> str:
    if f.card:
        return f.card
    return f"{_site(cfg)}search.html?q={quote((f.token or f.text or '')[:60])}"


def _label(f: Finding, n: int = 70) -> str:
    return " ".join((f.token or f.text or "").split())[:n]


def is_tga(s: dict[str, Any] | None) -> bool:
    return bool(s) and ("game awards" in str(s.get("nome", "")).lower() or str(s.get("id", "")).startswith("tga"))


def tags_for(s: dict[str, Any] | None = None) -> list[str]:
    return HASHTAGS + ([TGA_TAG] if is_tga(s) else [])


def compose(body: str, url: str, tags: list[str]) -> str:
    """Testo, link e hashtag sotto MAX_CHARS: si accorcia solo il testo."""
    tail = "\n" + url + ("\n" + " ".join("#" + t for t in tags) if tags else "")
    room = MAX_CHARS - len(tail)
    if len(body) > room:
        body = body[: room - 1].rstrip() + "…"
    return body + tail


def alert_text(cfg: Config, items: list[Finding], tags: list[str] | None = None) -> tuple[str, str]:
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
    names = ", ".join(_label(f, 60) for f in items[:2])
    more = f" (+{len(items) - 2} more)" if len(items) > 2 else ""
    if KEYWORD in (top.keywords or []):
        body = f"{ANNOUNCE} in {where}: {names}{more}. Not verified yet."
    else:
        body = f"New in {where}: {names}{more}. Why it matters: {why(top)}."
    return compose(body, url, HASHTAGS if tags is None else tags), url


def serata_text(cfg: Config, s: dict[str, Any]) -> tuple[str, str]:
    url = f"{_site(cfg)}previsioni.html?serata={quote(str(s.get('id', '')))}#serata"   # la query rende unico il link
    body = (f"{s.get('nome', 'The show')} is tonight. HLX Watch checks Valve's sites, Steam and game files "
            "every 10 minutes and posts here if anything Half-Life shows up.")
    return compose(body, url, tags_for(s)), url


def bot_alert_text(a: dict[str, Any], s: dict[str, Any] | None) -> tuple[str, str]:
    """Un possibile annuncio trovato dal bot su Cloudflare (testo italiano nel suo elenco): il post in inglese."""
    detail = str(a.get("x") or "").split(": ", 1)[-1]          # toglie "POSSIBILE ANNUNCIO: "
    detail = detail.split(": ", 1)[-1].strip()                  # toglie "pagina nuova su half-life.com: " e simili
    kind = a.get("t")
    if kind == "web":
        what = f"a new page on half-life.com: {detail}"
    elif kind == "video":
        channel = str(a.get("g") or "YouTube").replace("YouTube: ", "")
        what = f"a new video on YouTube ({channel}): {detail}"
    else:
        what = f"a new Valve product on Steam: {detail}"
    url = str(a.get("u") or "")
    return compose(f"{ANNOUNCE}: {what}. Not verified yet.", url, tags_for(s)), url


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


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def active_serata(now: datetime) -> dict[str, Any] | None:
    """La serata vera in corso (mai la prova generale)."""
    from hlxwatch import serata
    for sr in serata.load():
        if sr.get("prova"):
            continue
        start, end = _when(sr.get("inizio")), _when(sr.get("fine"))
        if start and end and start <= now < end:
            return sr
    return None


def plan(cfg: Config, db: Any, alerts: Iterable[Finding], now: datetime | None = None,
         bot_alerts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """I post da fare in questo giro (senza pubblicare): [{"chiave", "testo", "link", "annuncio", "serata"}]."""
    from hlxwatch.announcement import KEYWORD
    s = settings(cfg)
    now = now or datetime.now(timezone.utc)
    state = _state(db)
    done = set(state["chiavi"])
    day = state["giorni"].get(now.date().isoformat(), {})
    normal_left = s["max_per_day"] - int(day.get("n", 0))
    ann_left = s["max_announcements_per_day"] - int(day.get("annunci", 0))
    out: list[dict[str, Any]] = []
    sr = active_serata(now) if s["serate"] else None
    if sr and f"serata:{sr.get('id')}" not in done:
        text, url = serata_text(cfg, sr)
        out.append({"chiave": f"serata:{sr.get('id')}", "testo": text, "link": url, "annuncio": False, "serata": True})
    if sr and s["bot_annunci"]:
        start = _when(sr.get("inizio"))
        for a in bot_alerts or []:
            when, url = _when(a.get("c")), str(a.get("u") or "")
            key = f"bot:{url}"
            if a.get("t") not in BOT_KINDS or not url or key in done or when is None or start is None or when < start:
                continue
            if ann_left <= 0:
                log.info("Bluesky: limite di annunci del giorno raggiunto, salto %s", key)
                continue
            text, link_ = bot_alert_text(a, sr)
            out.append({"chiave": key, "testo": text, "link": link_, "annuncio": True, "serata": False})
            ann_left -= 1
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
        text, url = alert_text(cfg, items, tags_for(sr))
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


def read_bot_alerts(url: str, session: Any = None, timeout: float = 15) -> list[dict[str, Any]]:
    """I possibili annunci recenti del bot su Cloudflare (/fast/annuncio.json, lo stesso che legge l'app Android)."""
    http = session or requests
    r = http.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


# ------------------------------------------------------------------ Bluesky
def facets(text: str, url: str, tags: Iterable[str]) -> list[dict[str, Any]]:
    """Le faccette di Bluesky: il link e gli hashtag, con le posizioni in byte UTF-8."""
    raw = text.encode("utf-8")
    out: list[dict[str, Any]] = []
    start = raw.find(url.encode("utf-8")) if url else -1
    if start >= 0:
        out.append({"index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]})
    for tag in tags:
        mark = ("#" + tag).encode("utf-8")
        start = raw.rfind(mark)                       # gli hashtag sono in fondo
        if start >= 0:
            out.append({"index": {"byteStart": start, "byteEnd": start + len(mark)},
                        "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]})
    return out


class Client:
    def __init__(self, handle: str, password: str, session: Any = None, timeout: float = 20):
        self.handle = handle
        self.password = password
        self.http = session or requests.Session()
        self.timeout = timeout
        self.jwt: str | None = None
        self.did: str | None = None

    def recent_posts(self, limit: int = 40) -> list[dict[str, Any]]:
        """Gli ultimi post dell'account (lettura pubblica, senza accesso): testo, link e ora."""
        r = self.http.get(f"{PUBLIC}/app.bsky.feed.getAuthorFeed",
                          params={"actor": self.handle, "limit": limit, "filter": "posts_no_replies"}, timeout=self.timeout)
        r.raise_for_status()
        posts = []
        for item in r.json().get("feed", []):
            rec = (item.get("post") or {}).get("record") or {}
            links = {feat["uri"] for facet in rec.get("facets") or [] for feat in facet.get("features") or []
                     if feat.get("uri")}
            links |= {w for w in str(rec.get("text") or "").split() if w.startswith("http")}
            posts.append({"text": str(rec.get("text") or ""), "links": links, "at": _when(rec.get("createdAt"))})
        return posts

    def recent_links(self, limit: int = 40) -> set[str]:
        return {u for p in self.recent_posts(limit) for u in p["links"]}

    def login(self) -> None:
        r = self.http.post(f"{PDS}/com.atproto.server.createSession",
                           json={"identifier": self.handle, "password": self.password}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        self.jwt, self.did = data["accessJwt"], data["did"]

    def post(self, text: str, url: str, tags: Iterable[str] = ()) -> str:
        if not self.jwt:
            self.login()
        record: dict[str, Any] = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
                                  "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        found = facets(text, url, tags)
        if found:
            record["facets"] = found
        r = self.http.post(f"{PDS}/com.atproto.repo.createRecord", headers={"Authorization": f"Bearer {self.jwt}"},
                           json={"repo": self.did, "collection": "app.bsky.feed.post", "record": record},
                           timeout=self.timeout)
        r.raise_for_status()
        return str(r.json().get("uri", ""))


def _tags_in(text: str) -> list[str]:
    return [w[1:] for w in text.split() if w.startswith("#") and w[1:] in HASHTAGS + [TGA_TAG]]


def publish(cfg: Config, db: Any, alerts: Iterable[Finding], *, client: Client | None = None,
            now: datetime | None = None, dry_run: bool | None = None,
            bot_alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pubblica i post di questo giro. Non solleva eccezioni: l'esito è nel risultato e nel registro."""
    s = settings(cfg)
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {"post": [], "pubblicati": 0, "errore": None, "tentato": False}
    if not s["enabled"]:
        return result
    if bot_alerts is None and s["serate"] and s["bot_annunci"] and s["bot_url"]:
        try:                                          # solo nella serata vera: fuori non servono
            bot_alerts = read_bot_alerts(s["bot_url"]) if active_serata(now) else []
        except Exception as exc:
            log.warning("Bluesky: annunci del bot non letti (%s)", exc)
            bot_alerts = []
    try:
        posts = plan(cfg, db, alerts, now, bot_alerts)
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
        recent = client.recent_posts()
    except Exception as exc:
        log.warning("Bluesky: ultimi post non letti (%s), salto per non rischiare doppioni", exc)
        result["errore"] = f"lettura dei post: {exc}"
        return result
    seen = {u for p in recent for u in p["links"]}
    last_ann = max((p["at"] for p in recent if p["at"] and p["text"].startswith(ANNOUNCE)), default=None)
    done = []
    for p in posts:
        if p["link"] in seen:
            log.info("Bluesky: %s già pubblicato (link presente), lo segno e salto", p["chiave"])
            done.append(p)
            continue
        if p.get("annuncio") and last_ann and now - last_ann < ANNOUNCE_GAP:
            log.info("Bluesky: c'è già un possibile annuncio di %s, salto %s", last_ann.isoformat(), p["chiave"])
            done.append(p)
            continue
        try:
            uri = client.post(p["testo"], p["link"], _tags_in(p["testo"]))
            log.info("Bluesky: pubblicato %s (%s)", p["chiave"], uri)
            done.append(p)
            result["pubblicati"] += 1
            if p.get("annuncio"):
                last_ann = now
        except Exception as exc:
            log.warning("Bluesky: pubblicazione di %s non riuscita: %s", p["chiave"], exc)
            result["errore"] = str(exc)
            break
    if done:
        _record(db, done, now)
    return result
