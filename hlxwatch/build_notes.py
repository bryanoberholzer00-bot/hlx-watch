"""Spiegazione sotto gli avvisi delle build: che cosa cambia, in una riga, senza dover aprire GitHub.

Quando Steam pubblica una build di un gioco seguito, SteamTracking fa un commit con lo stesso numero
("26446291 | 30 files | ...") e l'elenco dei file del gioco che sono cambiati. Da quell'elenco si ricava:
  - traduzioni o testi nuovi in inglese (l'inglese cambia quando arrivano testi nuovi; le altre lingue di solito
    seguono giorni dopo, con le traduzioni);
  - file del gioco nuovi, tolti o modificati (dalle righe di pak01_dir.txt);
  - programmi (le stringhe dei .dll/.exe), messaggi di rete (Protobuf), elenchi dei pacchetti di download;
  - se lo stesso commit ha righe da avviso (HLX, nomi nuovi): già elencate a parte nello stesso messaggio.
Si aggiungono le ultime note ufficiali di Valve su Steam degli ultimi 14 giorni (API pubblica ISteamNews).

Se il commit di SteamTracking non c'è ancora quando parte l'avviso, la build resta in attesa (stato
`build_notes:pending`) e la spiegazione arriva al primo giro in cui il commit compare, con un messaggio breve.
Dopo un giorno senza commit si smette di aspettare.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from hlxwatch import __version__
from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

PENDING = "build_notes:pending"
WAIT = timedelta(hours=24)
NEWS_DAYS = 14
NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
COMMIT = re.compile(r"github\.com/([^/]+/[^/]+)/commit/([0-9a-f]{7,40})")
STEAM_TOKEN = re.compile(r"^build:(\d+):([^:]+):(\d+)$")
LANGS = ("english", "brazilian", "bulgarian", "czech", "danish", "dutch", "finnish", "french", "german", "greek",
         "hungarian", "italian", "japanese", "koreana", "latam", "norwegian", "polish", "portuguese", "romanian",
         "russian", "schinese", "spanish", "swedish", "tchinese", "thai", "turkish", "ukrainian", "vietnamese",
         "indonesian", "arabic")
LANG_FILE = re.compile(r"_(" + "|".join(LANGS) + r")\.txt$", re.IGNORECASE)
PAK_DIR = re.compile(r"(?:^|/)pak\d*_dir\.txt$")
PAK_LINE = re.compile(r"^([+-])(\S.*?)\s+CRC:")
SERVICE = re.compile(r"(?:^|/)(?:files\.json|README\.md|\.github/.*|\.gitignore|\.stringsignore|update\.sh)$")
BUILD_COMMIT = re.compile(r"^\d+ \| \d+ files? \|")
# CS2 e Dota 2 numerano i commit con la versione del gioco: si prende il primo commit di build dopo la build Steam
MATCH_BEFORE = timedelta(minutes=15)
MATCH_AFTER = timedelta(hours=6)


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def describe(files: list[dict[str, Any]]) -> list[str]:
    """Parti della frase "Cosa cambia", dalla lista dei file del commit (formato dell'API di GitHub)."""
    langs: set[str] = set()
    english_plus = 0
    manifests = programs = protobufs = version = 0
    new_files: list[str] = []
    others: list[str] = []
    pak = {"new": 0, "gone": 0, "changed": 0, "known": False}
    for entry in files:
        path = str(entry.get("filename") or "")
        if SERVICE.search(path):
            continue
        lang = LANG_FILE.search(path)
        if lang:
            langs.add(lang.group(1).lower())
            if lang.group(1).lower() == "english":
                english_plus += int(entry.get("additions") or 0)
            continue
        if path.startswith("manifests/"):
            manifests += 1
        elif path.endswith("steam.inf"):
            version += 1
        elif path.endswith("_strings.txt"):
            programs += 1
        elif path.startswith("Protobufs/") or path.endswith(".proto"):
            protobufs += 1
        elif PAK_DIR.search(path):
            plus, minus = set(), set()
            for line in (entry.get("patch") or "").splitlines():
                m = PAK_LINE.match(line)
                if m:
                    (plus if m.group(1) == "+" else minus).add(m.group(2))
            # i file di testo delle lingue sono anche nell'elenco dei file: contano già come traduzioni
            plus = {x for x in plus if not LANG_FILE.search(x)}
            minus = {x for x in minus if not LANG_FILE.search(x)}
            if plus or minus or entry.get("patch"):
                pak["known"] = True
                pak["new"] += len(plus - minus)
                new_files += sorted(plus - minus)
                pak["gone"] += len(minus - plus)
                pak["changed"] += len(plus & minus)
            else:
                others.append(path.rsplit("/", 1)[-1])
        else:
            others.append(path.rsplit("/", 1)[-1])
    parts: list[str] = []
    if "english" in langs:
        rest = len(langs) - 1
        parts.append(f"testi nuovi in inglese (+{_n(english_plus, 'riga', 'righe')})"
                     + (f" e in {_n(rest, 'altra lingua', 'altre lingue')}" if rest else ""))
    elif langs:
        only = not (programs or protobufs or others or version or pak["new"] or pak["gone"] or pak["changed"])
        parts.append(("solo traduzioni (" if only else "traduzioni (") + _n(len(langs), "lingua", "lingue") + ")")
    if pak["known"] and (pak["new"] or pak["gone"] or pak["changed"]):
        names = ", ".join(n.rsplit("/", 1)[-1] for n in new_files[:2]) + (" …" if len(new_files) > 2 else "")
        bits = [_n(pak["new"], "nuovo", "nuovi") + f" ({names})" if pak["new"] else "",
                _n(pak["gone"], "tolto", "tolti") if pak["gone"] else "",
                _n(pak["changed"], "modificato", "modificati") if pak["changed"] else ""]
        text = ", ".join(b for b in bits if b)
        parts.append("file del gioco: " + text + ("" if pak["new"] else ", nessuno nuovo"))
    if programs:
        parts.append("programmi del gioco (" + _n(programs, "file", "file") + ")")
    if protobufs:
        parts.append("messaggi di rete (" + _n(protobufs, "file", "file") + " Protobuf)")
    if others:
        sample = ", ".join(others[:2]) + (f" e altri {len(others) - 2}" if len(others) > 2 else "")
        parts.append("altri file: " + sample)
    if version:
        parts.append("numero di versione")
    if manifests:
        parts.append("elenchi dei pacchetti di download")
    return parts


def find_commit(db: Database, finding: Finding) -> dict[str, Any] | None:
    """Il commit di SteamTracking che corrisponde alla build dell'avviso (riga della tabella builds)."""
    build = finding.build
    if build is not None and build.source == "github" and build.url and COMMIT.search(build.url):
        row = db.conn.execute("SELECT * FROM builds WHERE game = ? AND source = 'github' AND ref = ?",
                              (finding.game, build.ref)).fetchone()
        return dict(row) if row else {"id": None, "url": build.url, "ref": build.ref}
    m = STEAM_TOKEN.match(finding.token or "")
    if not m:
        return None
    row = db.conn.execute("SELECT * FROM builds WHERE game = ? AND source = 'github' AND message LIKE ? "
                          "ORDER BY date DESC LIMIT 1", (finding.game, f"{m.group(3)} |%")).fetchone()
    if row:
        return dict(row)
    when = build.date if build is not None else None
    if m.group(2) != "public" or when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    for row in db.conn.execute("SELECT * FROM builds WHERE game = ? AND source = 'github' AND date >= ? AND date <= ? "
                               "ORDER BY date", (finding.game, iso(when - MATCH_BEFORE), iso(when + MATCH_AFTER))):
        if BUILD_COMMIT.match(row["message"] or ""):
            return dict(row)
    return None


def flagged_lines(db: Database, build_row: dict[str, Any], min_score: int) -> int:
    if not build_row.get("id"):
        return 0
    return int(db.conn.execute(
        "SELECT COUNT(*) FROM findings WHERE build_id = ? AND score >= ? AND change_type NOT IN ('build', 'baseline')",
        (build_row["id"], min_score)).fetchone()[0])


class BuildNotes:
    def __init__(self, cfg: Config, db: Database, client: Any, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.min_score = int(cfg.get("general.notify_min_score", 50))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", f"hlx-watch/{__version__}")
        self._news: dict[int, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------- pezzi
    def changes(self, build_row: dict[str, Any]) -> str | None:
        m = COMMIT.search(str(build_row.get("url") or ""))
        if not m:
            return None
        detail = self.client.commit_detail(m.group(1), m.group(2), 3)
        if not detail:
            return None
        parts = describe(detail.get("files") or [])
        lines = flagged_lines(self.db, build_row, self.min_score)
        parts.append(f"{_n(lines, 'riga', 'righe')} da avviso, " + ("elencata" if lines == 1 else "elencate") + " a parte"
                     if lines
                     else "nessuna riga HLX o nome nuovo")
        return "; ".join(parts)

    def news(self, appid: int, when: datetime) -> str | None:
        if appid not in self._news:
            try:
                r = self.session.get(NEWS_API, params={"appid": appid, "count": 5, "maxlength": 1,
                                                       "feeds": "steam_community_announcements"}, timeout=20)
                r.raise_for_status()
                self._news[appid] = (r.json().get("appnews") or {}).get("newsitems") or []
            except (requests.RequestException, ValueError) as exc:
                log.info("Note di Steam per %s non lette: %s", appid, exc)
                self._news[appid] = []
        for item in sorted(self._news[appid], key=lambda i: -int(i.get("date") or 0)):
            date = datetime.fromtimestamp(int(item.get("date") or 0), tz=timezone.utc)
            if when - timedelta(days=NEWS_DAYS) <= date <= when + timedelta(days=1):
                return f"«{str(item.get('title') or 'senza titolo').strip()[:80]}» ({date.strftime('%d/%m')})"
        return None

    def note(self, finding: Finding, build_row: dict[str, Any]) -> str:
        text = "Cosa cambia: " + (self.changes(build_row) or "elenco dei file non disponibile")
        m = STEAM_TOKEN.match(finding.token or "")
        if m:
            when = finding.build.date if finding.build and finding.build.date else utcnow()
            news = self.news(int(m.group(1)), when)
            text += ". Note ufficiali di Valve: " + (news or f"nessuna negli ultimi {NEWS_DAYS} giorni")
        return text

    # ------------------------------------------------------------- avvisi
    def annotate(self, alerts: list[Finding]) -> None:
        """Aggiunge la spiegazione alle build dell'avviso; quelle senza commit restano in attesa."""
        pending = self._pending()
        for finding in alerts:
            if finding.change_type != "build" or "steam_build" not in (finding.keywords or []):
                continue
            try:
                row = find_commit(self.db, finding)
                if row is None:
                    if STEAM_TOKEN.match(finding.token or "") and finding.id is not None:
                        pending.append({"id": finding.id, "at": iso(utcnow())})
                    continue
                finding.note = self.note(finding, row)
            except Exception:                    # la spiegazione non deve mai fermare l'avviso
                log.exception("Spiegazione della build non riuscita: %s", finding.token)
        self._save(pending)

    def followups(self, notifiers: list) -> int:
        """Spiegazioni arrivate dopo l'avviso: un messaggio breve per ogni build il cui commit ora c'è."""
        from hlxwatch.notifiers import notify_text
        pending, keep, sent = self._pending(), [], 0
        for item in pending:
            finding = self._load(item["id"])
            if finding is None:
                continue
            row = find_commit(self.db, finding)
            if row is None:
                started = parse_iso(item.get("at"))
                if started is not None and utcnow() - started < WAIT:
                    keep.append(item)
                else:
                    log.info("Spiegazione della build %s: nessun commit di SteamTracking in un giorno", finding.token)
                continue
            try:
                note = self.note(finding, row)
            except Exception:
                log.exception("Spiegazione della build non riuscita: %s", finding.token)
                continue
            title = self.cfg.game_title(finding.game)
            buildid = (STEAM_TOKEN.match(finding.token or "") or [None] * 4)[3]
            if notify_text(notifiers, f"HLX Watch: build {buildid} di {title}", note):
                sent += 1
            else:
                keep.append(item)
        self._save(keep)
        return sent

    # ------------------------------------------------------------- stato
    def _pending(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.db.get_state(PENDING) or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        seen: set[int] = set()
        unique = [i for i in items if not (i["id"] in seen or seen.add(i["id"]))]
        self.db.set_state(PENDING, json.dumps(unique[-50:]))

    def _load(self, finding_id: int) -> Finding | None:
        row = self.db.conn.execute(
            "SELECT f.*, b.date AS b_date, b.source AS b_source, b.ref AS b_ref, b.url AS b_url "
            "FROM findings f LEFT JOIN builds b ON b.id = f.build_id WHERE f.id = ?", (finding_id,)).fetchone()
        if row is None:
            return None
        from hlxwatch.models import BuildInfo
        finding = Finding(game=row["game"], source=row["source"], file_path=row["file_path"],
                          change_type=row["change_type"], text=row["text"], score=row["score"],
                          keywords=json.loads(row["keywords"] or "[]"), token=row["token"], id=row["id"])
        if row["b_ref"]:
            finding.build = BuildInfo(game=row["game"], source=row["b_source"], ref=row["b_ref"],
                                      date=parse_iso(row["b_date"]) or utcnow(), url=row["b_url"])
        return finding
