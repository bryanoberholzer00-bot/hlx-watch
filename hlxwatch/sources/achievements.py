"""Obiettivi (achievement) delle app Steam seguite, dall'API pubblica di Steam senza chiave.

ISteamUserStats/GetGlobalAchievementPercentagesForApp elenca i nomi interni degli obiettivi di un'app
(TRACK_A1_INTRO_WORLD_2, PLAY_CS2...). Un aggiornamento che aggiunge obiettivi li mostra qui, a volte prima delle note
di aggiornamento; un nome con HLX o Half-Life 3 sarebbe un possibile annuncio. Per le app riservate (Deadlock, le app
private di Valve) Steam oggi risponde vuoto: si legge solo ciò che Steam rende pubblico, niente chiavi né trucchi. Se
un'app passa da "nascosti" a "visibili" (come per l'errore di Valve di settembre 2026) è una voce a parte.
Si seguono le app di steam.watch_apps e quelle aggiunte dal piano annuncio. Il primo giro registra.
"""
from __future__ import annotations

import json
import logging
import time
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

API = "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"
LAST_KEY = "achievements:last_run"


class AchievementsSource:
    name = "achievements"

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.acfg: dict[str, Any] = cfg.get("achievements", {}) or {}
        self.interval = timedelta(hours=float(self.acfg.get("interval_hours", 3)))
        self.score_new = int(self.acfg.get("score_new", 55))
        self.score_visible = int(self.acfg.get("score_visible", 90))
        self.pause = float(self.acfg.get("pause_seconds", 1.0))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", f"hlx-watch/{__version__}")
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.acfg.get("enabled", True))

    def due(self) -> bool:
        last = parse_iso(self.db.get_state(LAST_KEY))
        return last is None or utcnow() - last >= self.interval

    def apps(self) -> list[dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for app in [*self.cfg.steam_watch_apps, *announcement.extra_watch(self.db)]:
            try:
                appid = int(app["appid"])
            except (KeyError, TypeError, ValueError):
                continue
            out.setdefault(appid, {"appid": appid, "name": app.get("name") or str(appid),
                                   "title": app.get("title") or app.get("name") or str(appid),
                                   "ignore_keywords": app.get("ignore_keywords") or []})
        return list(out.values())

    def fetch(self, appid: int) -> list[str] | None:
        """Nomi degli obiettivi; None se Steam non li mostra (app riservata o senza obiettivi pubblici)."""
        r = self.session.get(API, params={"gameid": appid}, timeout=30)
        if r.status_code == 403 or r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"Steam ha risposto {r.status_code} per l'app {appid}")
        data = (r.json() or {}).get("achievementpercentages")
        if not data:
            return None
        return sorted({str(a.get("name")) for a in data.get("achievements") or [] if a.get("name")})

    def run(self, force: bool = False) -> list[Finding]:
        self.errors = []
        if not self.enabled or (not force and not self.due()):
            return []
        findings: list[Finding] = []
        for n, app in enumerate(self.apps()):
            if n and self.pause:
                time.sleep(self.pause)
            try:
                names = self.fetch(app["appid"])
            except Exception as exc:
                log.warning("[%s] obiettivi non letti: %s", app["name"], exc)
                self.errors.append(f"{app['name']}: {exc}")
                continue
            key = f"achievements:{app['appid']}"
            raw = self.db.get_state(key)
            self.db.set_state(key, json.dumps({"visibili": names is not None, "nomi": names or []}))
            if raw is None:
                log.info("[%s] obiettivi: %s", app["name"], f"{len(names)} registrati" if names is not None
                         else "nascosti da Steam")
                continue
            old = json.loads(raw)
            if names is None:
                continue
            new = [x for x in names if x not in set(old.get("nomi") or [])]
            if not new:
                continue
            findings.append(self._finding(app, new, became_visible=not old.get("visibili")))
        if not self.errors:
            self.db.set_state(LAST_KEY, iso(utcnow()))
        return findings

    def _finding(self, app: dict[str, Any], new: list[str], became_visible: bool) -> Finding:
        if became_visible:
            text = f"{app['title']}: obiettivi ora visibili su Steam ({len(new)}): {', '.join(new[:8])}"
            score = self.score_visible
        else:
            text = f"{app['title']}: {len(new)} obiettivi nuovi: {', '.join(new[:8])}"
            score = self.score_new
        keywords = ["achievement_new"]
        matcher = KeywordMatcher(self.cfg.keywords, ignore_ids=app["ignore_keywords"])
        for name in new:
            res = matcher.analyze_line(f"{name} {name.replace('_', ' ')}", file_path="achievements", change_type="added")
            if res and res.score > score:
                score, keywords = res.score, ["achievement_new", *res.keywords]
        url = f"https://steamcommunity.com/stats/{app['appid']}/achievements/"
        build = BuildInfo(game=app["name"], source="achievements", ref=f"ach:{app['appid']}:{utcnow().isoformat()}",
                          date=utcnow(), url=url, message=text[:500])
        finding = Finding(game=app["name"], source="achievements", file_path=f"Obiettivi di Steam: {app['title']}",
                          change_type="achievement", text=text[:500], score=min(score, 250), keywords=keywords,
                          context=json.dumps({"appid": app["appid"], "nuovi": new}, ensure_ascii=False))
        finding.build = build
        words = " ".join(x.replace("_", " ") for x in new)
        if announcement.enabled(self.cfg) and new_half_life(words, words):
            announcement.mark(finding, self.cfg)
        self.db.add_finding(finding, self.db.upsert_build(build))
        log.warning("[%s] %s", app["name"], text[:200])
        return finding
