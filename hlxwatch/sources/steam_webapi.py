"""Rilevamento di app Steam nuove (Valve, senza nome, nomi sospetti).

Due strade, usate insieme quando possibile:
1. IStoreService/GetAppList/v1 (Web API ufficiale): serve una chiave gratuita in .env (STEAM_WEB_API_KEY).
   Dà la lista completa delle app pubbliche: baseline precisa e rilevamento quotidiano.
2. Euristica sugli appid tramite PICS (sempre disponibile, senza chiave): gli appid sono assegnati in modo
   crescente, quindi un appid mai visto e più alto della "frontiera" registrata è un'app creata di recente.
I dettagli (nome, developer, publisher, tipo) arrivano da PICS in anonimo tramite SteamPicsSource.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Iterable

import requests

from hlxwatch import __version__
from hlxwatch import announcement
from hlxwatch.analyzers import KeywordMatcher
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.sources.steam_pics import SteamPicsSource, flatten_kv
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)
STORE_APPLIST = "https://api.steampowered.com/IStoreService/GetAppList/v1/"


def _association(common: dict[str, Any], kind: str) -> str | None:
    assoc = common.get("associations") or {}
    names = [str(v.get("name")) for v in assoc.values()
             if isinstance(v, dict) and v.get("type") == kind and v.get("name")]
    return ", ".join(names) if names else None


class SteamNewAppsSource:
    name = "steam_apps"

    def __init__(self, cfg: Config, db: Database, pics: SteamPicsSource):
        self.cfg = cfg
        self.db = db
        self.pics = pics
        self.ncfg: dict[str, Any] = cfg.get("steam.new_apps", {}) or {}
        self.matcher = KeywordMatcher(cfg.keywords)
        self.name_patterns = [re.compile(p, re.IGNORECASE) for p in (self.ncfg.get("flag_name_patterns") or [])]
        self.flag_devs = [str(d).lower() for d in (self.ncfg.get("flag_developers") or ["Valve"])]
        self._warned_no_key = False

    # ------------------------------------------------------------ app list
    def _fetch_applist(self) -> list[tuple[int, str]] | None:
        key = self.cfg.steam_web_api_key
        if not key:
            return None
        apps: list[tuple[int, str]] = []
        last_appid = 0
        for _ in range(60):
            params = {"key": key, "max_results": 50000, "last_appid": last_appid, "include_games": "true",
                      "include_dlc": "true", "include_software": "true", "include_videos": "true",
                      "include_hardware": "true"}
            response = requests.get(STORE_APPLIST, params=params, timeout=180,
                                    headers={"User-Agent": f"hlx-watch/{__version__}"})
            if response.status_code >= 400:
                # niente URL nel messaggio: contiene la chiave
                raise RuntimeError(f"IStoreService/GetAppList ha risposto {response.status_code} "
                                   f"(chiave non valida o servizio non disponibile)")
            payload = response.json().get("response") or {}
            batch = payload.get("apps") or []
            apps += [(int(a["appid"]), str(a.get("name") or "")) for a in batch if "appid" in a]
            if not batch or not payload.get("have_more_results"):
                break
            last_appid = int(payload.get("last_appid") or batch[-1]["appid"])
        return apps

    # ------------------------------------------------------------------ run
    def run(self, candidate_appids: Iterable[int] = ()) -> list[Finding]:
        if not self.cfg.get("steam.enabled", True) or not self.ncfg.get("enabled", True):
            return []
        new_ids = {int(x) for x in candidate_appids}
        frontier = int(self.db.get_state("steam:appid_frontier") or 0)

        # 1) lista completa, solo con chiave Web API
        if self.cfg.steam_web_api_key:
            baseline_done = self.db.get_state("steam:applist_loaded") == "1"
            last_refresh = parse_iso(self.db.get_state("steam:applist_refreshed_at"))
            hours = float(self.ncfg.get("applist_refresh_hours", 24))
            if not baseline_done or last_refresh is None or utcnow() - last_refresh > timedelta(hours=hours):
                try:
                    apps = self._fetch_applist()
                except Exception as exc:
                    log.error("Lista app Steam non disponibile: %s", exc)
                    apps = None
                if apps:
                    known = self.db.known_appids()
                    fresh = [(appid, name) for appid, name in apps if appid not in known]
                    if not baseline_done:
                        self.db.insert_apps(fresh, checked=True)
                        self.db.set_state("steam:applist_loaded", "1")
                        log.info("Baseline app Steam: %d app registrate, nessun allarme per quelle esistenti", len(fresh))
                    else:
                        self.db.insert_apps(fresh, checked=False)
                        new_ids |= {appid for appid, _ in fresh}
                        log.info("Lista app Steam: %d app mai viste", len(fresh))
                    frontier = max(frontier, max((appid for appid, _ in apps), default=0))
                    self.db.set_state("steam:applist_refreshed_at", iso(utcnow()))
        elif not self._warned_no_key:
            log.info("STEAM_WEB_API_KEY assente: nuove app rilevate solo con l'euristica sugli appid via PICS")
            self._warned_no_key = True

        # 2) app già viste come private che sono cambiate: forse ora hanno nome e developer.
        # Se Steam ha saltato l'elenco delle app cambiate non si sa quali: si ricontrollano tutte, le più recenti
        # per prime, al ritmo di max_lookup_per_run per run
        if getattr(self.pics, "changes_gap", False):
            reopened = self.db.reset_all_private_apps()
            prev = parse_iso(self.db.get_state("last_scan_at"))          # il run precedente (aggiornato a fine run)
            if prev:
                self.db.set_state("steam:changes_gap_hours", f"{(utcnow() - prev).total_seconds() / 3600:.2f}")
            self.db.set_state("steam:changes_gap_at", iso(utcnow()))
            log.info("PICS: elenco dei cambi saltato, %d app private da ricontrollare", reopened)
        else:
            reopened = self.db.reset_private_apps(new_ids)
            if reopened:
                log.info("PICS: %d app private cambiate, le rivaluto", reopened)

        # 3) euristica della frontiera (funziona anche senza chiave)
        unknown = sorted(appid for appid in new_ids if not self.db.app_known(appid))
        if unknown:
            if frontier == 0:
                self.db.insert_apps(((appid, None) for appid in unknown), checked=True)
                frontier = max(unknown)
                log.info("Baseline appid: frontiera %d (%d app registrate); gli appid superiori saranno "
                         "considerati nuovi", frontier, len(unknown))
            else:
                older = [appid for appid in unknown if appid <= frontier]
                newer = [appid for appid in unknown if appid > frontier]
                self.db.insert_apps(((appid, None) for appid in older), checked=True)
                self.db.insert_apps(((appid, None) for appid in newer), checked=False)
                frontier = max(frontier, max(unknown))
                if newer:
                    log.info("PICS: %d appid oltre la frontiera (probabili app nuove)", len(newer))
        if frontier:
            self.db.set_state("steam:appid_frontier", frontier)

        # 4) dettagli delle app da verificare
        to_check = self.db.apps_without_details(int(self.ncfg.get("max_lookup_per_run", 300)))
        if not to_check:
            return []
        log.info("Steam: chiedo a PICS i dettagli di %d app nuove", len(to_check))
        data = self.pics.call_worker(to_check, since=-1)
        if not data.get("ok"):
            log.error("Dettagli app non disponibili (PICS): %s", data.get("error"))
            return []
        findings: list[Finding] = []
        for appid in to_check:
            try:
                findings += self._evaluate(appid, data.get("apps", {}).get(str(appid)) or {})
            except Exception:
                log.exception("Errore valutando l'app %s", appid)
        return findings

    # ------------------------------------------------------------- valuta
    def _evaluate(self, appid: int, info: dict[str, Any]) -> list[Finding]:
        common = info.get("common") or {}
        extended = info.get("extended") or {}
        name = common.get("name") or None
        app_type = common.get("type")
        developer = extended.get("developer") or _association(common, "developer")
        publisher = extended.get("publisher") or _association(common, "publisher")
        score = 0
        reasons: list[str] = []
        tags: list[str] = []

        for who in (developer, publisher):
            if who and any(dev in str(who).lower() for dev in self.flag_devs):
                score = max(score, int(self.ncfg.get("score_developer", 90)))
                reasons.append(f"developer/publisher: {who}")
                tags.append("valve_app")
                break
        if name:
            for pattern in self.name_patterns:
                if pattern.search(name):
                    score = max(score, int(self.ncfg.get("score_name", 120)))
                    reasons.append(f"nome '{name}' corrisponde a /{pattern.pattern}/")
                    tags.append("suspicious_name")
                    break
        if not info or info.get("_missing_token"):
            score = max(score, int(self.ncfg.get("score_private", 25)))
            reasons.append("informazioni non pubbliche (richiede token)")
            tags.append("private_app")

        lines = flatten_kv(info) if info else []
        best = None
        for line in lines:
            res = self.matcher.analyze_line(line, file_path=f"pics/app/{appid}")
            if res and (best is None or res.score > best.score):
                best = res
        if best:
            score = max(score, best.score)
            reasons.append("parole chiave: " + ", ".join(best.keywords))
            tags += best.keywords

        flagged = score >= 50
        self.db.update_app_details(appid, name, app_type, developer, publisher,
                                   info.get("_change_number"), flagged, "; ".join(reasons) or None)
        if score <= 0 or tags == ["private_app"]:
            # app privata senza altri segnali: resta nella tabella delle app (pagina "App Steam"),
            # ma non diventa una voce: ne nascono centinaia al giorno e intaserebbero report e timeline
            return []
        text = (f"Nuova app {appid}: {name or '(senza nome)'} [{app_type or 'tipo ?'}] "
                f"dev={developer or '?'} pub={publisher or '?'}")
        build = BuildInfo(game="steam", source="steam", ref=f"app:{appid}", date=utcnow(),
                          url=f"https://store.steampowered.com/app/{appid}/", message=text)
        build_id = self.db.upsert_build(build)
        finding = Finding(
            game="steam", source="steam_apps", file_path=f"app/{appid}", change_type="new_app", text=text,
            score=score, keywords=tags, token=f"app:{appid}", context="\n".join(reasons + [""] + lines[:80]),
        )
        if "valve_app" in tags and announcement.enabled(self.cfg) and announcement.looks_like_hlx(name):
            # piano annuncio: un'app di Valve con un nome Half-Life è probabilmente la pagina del gioco
            announcement.mark(finding, self.cfg)
            added = announcement.add_watch(self.db, self.cfg, appid, name or "")
            if added:
                finding.context = f"aggiunta alle app seguite su Steam come '{added['name']}'\n" + finding.context
                log.warning("POSSIBILE ANNUNCIO: app %s (%s) di Valve, ora seguita su Steam", appid, name)
        finding.build = build
        self.db.add_finding(finding, build_id)
        if flagged:
            log.info("Nuova app Steam %s (%s) punteggio %d: %s", appid, name or "senza nome", score, "; ".join(reasons))
        return [finding]
