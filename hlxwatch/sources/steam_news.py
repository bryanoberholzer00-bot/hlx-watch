"""Post ufficiali di Valve su Steam: la fonte che vede un annuncio nel momento in cui Valve lo pubblica.

Le altre fonti vedono un annuncio dagli effetti (un'app nuova, un repository nuovo). Qui si leggono i post della
sezione "Annunci" delle pagine Steam dei giochi Half-Life, di SteamVR, del client Steam e dello Steam Deck, con
l'API pubblica ISteamNews/GetNewsForApp (senza chiave, una chiamata per app a ogni run).
  - un titolo o un testo che parla di un Half-Life nuovo (HLX, Half-Life 3, "Half-Life: <titolo mai visto>")
    diventa un avviso di possibile annuncio, anche nell'orario di silenzio; i titoli già usciti (Alyx, Episode
    One...) non contano;
  - ogni altro post finisce in archivio con punteggio basso, senza avviso, a meno che le parole chiave lo valutino
    di più; per le app che pubblicano note di aggiornamento ogni settimana (Steam, SteamVR, Steam Deck) si tengono
    solo i post che non sono note di aggiornamento;
  - i post delle app Half-Life aggiunte da sole dal piano annuncio hanno sempre l'avviso.
Il primo giro di ogni app registra i post esistenti e basta (stato `news:<appid>:seen`).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from hlxwatch import __version__, announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import utcnow

log = logging.getLogger(__name__)

API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
FEED = "steam_community_announcements"          # solo i post di Valve, non gli articoli dei siti esterni
KEEP_SEEN = 80
# Titoli Half-Life già usciti: tolti dal testo prima di cercare un Half-Life nuovo
KNOWN_TITLES = re.compile(
    r"half[\s-]*life\s*(?:2\s*:?\s*)?(?:[:\-–]\s*)?(?:alyx|source|opposing force|blue shift|decay|deathmatch(?: source)?"
    r"|episode (?:one|two|1|2)|lost coast|uplink|25th anniversary|20th anniversary)\b", re.IGNORECASE)
NEW_IN_TEXT = re.compile(r"\bhlx\b|\bhl[\s_-]?3\b|half[\s-]*life\s*(?:3|iii)\b|episode (?:three|3)\b", re.IGNORECASE)
NEW_IN_TITLE = re.compile(NEW_IN_TEXT.pattern + r"|half[\s-]*life\s*:\s*[a-z0-9]", re.IGNORECASE)
BBCODE = re.compile(r"\[/?[a-z0-9*]+(?:=[^\]]*)?\]", re.IGNORECASE)


def clean(text: str | None) -> str:
    """Il testo del post senza BBCode né link lunghi."""
    t = BBCODE.sub(" ", text or "")
    t = re.sub(r"\{STEAM_CLAN_IMAGE\}\S*|https?://\S+", " ", t)
    return "\n".join(" ".join(line.split()) for line in t.splitlines() if line.strip())


def new_half_life(title: str, contents: str) -> bool:
    """Vero se il post parla di un Half-Life che non è ancora uscito."""
    title_rest = KNOWN_TITLES.sub(" ", title or "")
    body_rest = KNOWN_TITLES.sub(" ", contents or "")
    return bool(NEW_IN_TITLE.search(title_rest) or NEW_IN_TEXT.search(body_rest))


def post_url(appid: int, gid: str) -> str:
    return f"https://store.steampowered.com/news/app/{appid}/view/{gid}"


class SteamNewsSource:
    name = "steam_news"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.ncfg: dict[str, Any] = cfg.get("steam_news", {}) or {}
        self.enabled = bool(self.ncfg.get("enabled", True))
        self.score = int(self.ncfg.get("score", 40))
        self.score_auto = int(self.ncfg.get("score_auto_app", 120))
        self.min_keyword = int(self.ncfg.get("min_keyword_score", 90))
        self.count = int(self.ncfg.get("count", 10))
        self.max_age = timedelta(days=float(self.ncfg.get("max_age_days", 3)))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", f"hlx-watch/{__version__}")
        self.errors: list[str] = []

    # ------------------------------------------------------------ quali app
    def apps(self) -> list[dict[str, Any]]:
        """Le app del config, completate con nome e titolo di steam.watch_apps, più quelle aggiunte dal piano annuncio."""
        known = {int(a["appid"]): a for a in self.cfg.steam_watch_apps if a.get("appid")}
        out: dict[int, dict[str, Any]] = {}
        for entry in self.ncfg.get("apps") or []:
            appid = int(entry["appid"])
            base = known.get(appid, {})
            out[appid] = {"appid": appid, "name": entry.get("name") or base.get("name") or f"app{appid}",
                          "title": entry.get("title") or base.get("title") or f"App {appid}",
                          "patchnotes": bool(entry.get("patchnotes", True)),
                          "ignore_keywords": list(entry.get("ignore_keywords") or base.get("ignore_keywords") or []),
                          "auto": False}
        for app in announcement.extra_watch(self.db):
            appid = int(app["appid"])
            out.setdefault(appid, {"appid": appid, "name": app["name"], "title": app["title"], "patchnotes": True,
                                   "ignore_keywords": list(app.get("ignore_keywords") or []), "auto": True})
        return list(out.values())

    # ------------------------------------------------------------------ run
    def run(self) -> list[Finding]:
        self.errors = []
        if not self.enabled:
            return []
        findings: list[Finding] = []
        for app in self.apps():
            try:
                findings += self.check(app, self.fetch(app["appid"]))
            except Exception as exc:          # una app che non risponde non ferma le altre
                log.warning("[%s] notizie di Steam non lette: %s", app["name"], exc)
                self.errors.append(f"{app['name']}: {exc}")
        return findings

    def fetch(self, appid: int) -> list[dict[str, Any]]:
        response = self.session.get(API, params={"appid": appid, "count": self.count, "maxlength": 0, "feeds": FEED},
                                    timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"GetNewsForApp ha risposto {response.status_code}")
        return list(((response.json() or {}).get("appnews") or {}).get("newsitems") or [])

    def check(self, app: dict[str, Any], items: list[dict[str, Any]]) -> list[Finding]:
        key = f"news:{app['appid']}:seen"
        raw = self.db.get_state(key)
        first_run = raw is None
        try:
            seen: list[str] = json.loads(raw) if raw else []
        except ValueError:
            seen = []
        matcher = KeywordMatcher(self.cfg.keywords, ignore_ids=app["ignore_keywords"])
        findings: list[Finding] = []
        now = utcnow()
        for item in sorted(items, key=lambda i: int(i.get("date") or 0)):
            gid = str(item.get("gid") or "")
            if not gid or gid in seen or item.get("feedname", FEED) != FEED:
                continue
            seen.append(gid)
            if first_run:
                continue
            finding = self._finding(app, item, matcher)
            if finding is None:
                continue
            when = finding.build.date if finding.build else now
            # un post vecchio mai visto (dopo un lungo stop): in archivio con la sua data, senza avviso
            fresh = now - when <= self.max_age
            build_id = self.db.upsert_build(finding.build)
            self.db.add_finding(finding, build_id, created_at=None if fresh else when)
            if fresh:
                findings.append(finding)
                level = log.warning if finding.score >= 50 else log.info
                level("[%s] post ufficiale su Steam: %s", app["name"], finding.text[:160])
        self.db.set_state(key, json.dumps(seen[-KEEP_SEEN:]))
        if first_run:
            log.info("[%s] notizie di Steam: %d post registrati, senza avvisi", app["name"], len(seen))
        return findings

    def _finding(self, app: dict[str, Any], item: dict[str, Any], matcher: KeywordMatcher) -> Finding | None:
        title = " ".join(str(item.get("title") or "").split())[:300]
        body = clean(str(item.get("contents") or ""))
        tags = [str(t) for t in item.get("tags") or []]
        is_new_hl = new_half_life(title, body)
        if "patchnotes" in tags and not app["patchnotes"] and not is_new_hl:
            return None
        score, keywords, best = self.score, ["steam_news"], ""
        for line in [title, *body.splitlines()[:200]]:
            res = matcher.analyze_line(line, file_path="", change_type="added")
            if res and res.score >= self.min_keyword and res.score > score:
                score, best = res.score, line
                keywords = ["steam_news", *res.keywords]
        if app["auto"]:
            score = max(score, self.score_auto)
        gid, appid = str(item["gid"]), int(app["appid"])
        date = datetime.fromtimestamp(int(item.get("date") or 0), tz=timezone.utc) if item.get("date") else utcnow()
        build = BuildInfo(game=app["name"], source="steam_news", ref=f"news:{gid}", date=date, url=post_url(appid, gid),
                          message=f"post ufficiale su Steam: {title[:200]}")
        context = "post ufficiale di Valve nella sezione Annunci di " + app["title"]
        if "patchnotes" in tags:
            context += " (note di aggiornamento)"
        if best and best != title:
            context += f"; riga segnalata: {best[:200]}"
        elif body:
            context += f"; inizio: {body.splitlines()[0][:200]}"
        finding = Finding(game=app["name"], source="steam_news", file_path=f"Annunci di Steam: {app['title']}",
                          change_type="news", text=title or f"post {gid} senza titolo", score=score, keywords=keywords,
                          context=context)
        finding.build = build
        if is_new_hl and announcement.enabled(self.cfg):
            announcement.mark(finding, self.cfg)
        return finding
