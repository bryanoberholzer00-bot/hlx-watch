"""Controllo di salute: avvisa su Telegram quando qualcosa si ferma, e quando riparte.

Dentro ogni run:
- ogni fonte (GitHub, Steam, scansione completa, scoperta repository, analisi AI...) riporta se è
  andata bene; dopo `failure_threshold` fallimenti di fila arriva un avviso, e uno quando torna a funzionare;
- se SteamTracking non pubblica commit da `stale_days` giorni arriva un avviso (i dati a monte si sono fermati);
- alcuni problemi avvisano subito (`immediate`), per esempio il credito Anthropic esaurito.

Che i run non partano affatto lo controlla da fuori il Worker di Cloudflare (telegram-welcome), che ogni
6 ore guarda quando il sito è stato aggiornato l'ultima volta.
"""
from __future__ import annotations

import json
import re
import logging
from datetime import timedelta
from typing import Any

from hlxwatch.config import Config
from hlxwatch.notifiers import notify_text
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow
from hlxwatch.timeutil import local

log = logging.getLogger(__name__)

# messaggio dei commit di build di SteamTracking ("26281801 | 199 files | M ..."; Alyx 2020: "Update | 124 files")
BUILD_MESSAGE = re.compile(r"^(\d+|Update) \| \d+ files? \|")

LABELS = {
    "discovery": "scoperta dei repository SteamTracking",
    "structure": "struttura dei repository (cartelle, depot, Protobuf)",
    "github": "monitoraggio dei commit GitHub",
    "summaries": "riassunti automatici di SteamTracking",
    "steam_news": "post ufficiali di Valve su Steam",
    "steam_pics": "Steam PICS (build e info dei giochi)",
    "steam_apps": "rilevamento delle app Steam nuove",
    "fullscan": "scansione completa giornaliera",
    "first_seen": "date di prima comparsa",
    "ai": "analisi AI con Claude",
    "notify": "invio delle notifiche",
    "local": "file locali dei giochi",
}


class Health:
    def __init__(self, cfg: Config, db: Database, notifiers: list):
        self.cfg = cfg
        self.db = db
        self.notifiers = notifiers
        self.hcfg: dict[str, Any] = cfg.get("health", {}) or {}
        self.threshold = int(self.hcfg.get("failure_threshold", 3))

    def enabled(self) -> bool:
        return bool(self.hcfg.get("enabled", True))

    def _alert(self, title: str, text: str) -> None:
        log.warning("Salute: %s - %s", title, text.replace("\n", " | "))
        if self.notifiers:
            notify_text(self.notifiers, title, text)

    def _state(self, source: str) -> dict[str, Any]:
        try:
            return json.loads(self.db.get_state(f"health:{source}") or "{}")
        except json.JSONDecodeError:
            return {}

    def record(self, source: str, ok: bool, detail: str = "", immediate: bool = False) -> None:
        """Registra l'esito di una fonte in questo run e avvisa al passaggio di soglia o al ripristino."""
        if not self.enabled():
            return
        st = self._state(source)
        label = LABELS.get(source, source)
        if ok:
            if st.get("alerted"):
                self._alert("HLX Watch: problema risolto",
                            f"✅ {label} funziona di nuovo, dopo {st.get('fails', 0)} run con errori.")
            st = {"fails": 0, "alerted": False, "last_ok": utcnow().isoformat()}
        else:
            st["fails"] = int(st.get("fails", 0)) + 1
            st["last_error"] = detail[:300]
            st.setdefault("since", utcnow().isoformat())
            if not st.get("alerted") and (immediate or st["fails"] >= self.threshold):
                when = local(st["since"])
                self._alert("HLX Watch: qualcosa non va",
                            f"⚠️ {label} non funziona" +
                            (f" da {st['fails']} run di fila (dal {when})" if not immediate else "") +
                            f".\nUltimo errore: {detail[:300] or 'sconosciuto'}\n\n"
                            "Il resto continua a girare. Controlla i run su GitHub Actions.")
                st["alerted"] = True
        self.db.set_state(f"health:{source}", json.dumps(st))

    def check_upstream(self) -> None:
        """SteamTracking fermo: nessun commit nuovo nei repository seguiti da stale_days giorni."""
        if not self.enabled():
            return
        days = float(self.hcfg.get("stale_days", 5))
        row = self.db.conn.execute("SELECT MAX(date) FROM builds WHERE source = 'github' AND ref NOT LIKE 'repo:%'").fetchone()
        newest = parse_iso(row[0]) if row and row[0] else None
        if newest is None:
            return
        stale = utcnow() - newest > timedelta(days=days)
        alerted = self.db.get_state("health:upstream_alerted") == "1"
        if stale and not alerted:
            self._alert("HLX Watch: dati a monte fermi",
                        f"⚠️ Nessun commit nuovo da SteamTracking da più di {days:g} giorni "
                        f"(l'ultimo è del {local(newest)}).\nPuò essere solo un periodo senza aggiornamenti di Valve, "
                        "ma se dura controlla https://github.com/SteamTracking.")
            self.db.set_state("health:upstream_alerted", "1")
        elif not stale and alerted:
            self._alert("HLX Watch: dati a monte ripartiti", "✅ SteamTracking ha ripreso a pubblicare commit.")
            self.db.set_state("health:upstream_alerted", "0")

    def check_format(self) -> None:
        """SteamTracking ha cambiato il formato dei commit: senza "<build> | <N> files | ..." lo strumento non riconosce
        più le build. Un avviso distinto da "fonte ferma", una volta sola, e uno quando il formato torna."""
        if not self.enabled():
            return
        since = iso(utcnow() - timedelta(days=30))
        for game in ("cs2", "dota2", "deadlock"):
            rows = self.db.conn.execute(
                "SELECT message FROM builds WHERE game = ? AND source = 'github' AND length(ref) = 40 AND date >= ? "
                "AND COALESCE(message, '') NOT LIKE '[archeologia]%' ORDER BY date DESC LIMIT 10", (game, since)).fetchall()
            if len(rows) < 3:
                continue                 # pochi commit recenti: niente da giudicare
            ok = any(BUILD_MESSAGE.match(r[0] or "") for r in rows)
            key = f"formatcheck:{game}"
            alerted = self.db.get_state(key) == "1"
            title = self.cfg.game_title(game)
            if not ok and not alerted:
                self._alert("HLX Watch: formato di SteamTracking cambiato",
                            f"⚠️ Negli ultimi {len(rows)} commit di {title} non c'è più il formato atteso "
                            "\"<build> | <N> files | ...\". Lo strumento potrebbe non riconoscere le build: va adattato. "
                            "Controlla https://github.com/SteamTracking.")
                self.db.set_state(key, "1")
            elif ok and alerted:
                self._alert("HLX Watch: formato di SteamTracking di nuovo riconosciuto",
                            f"✅ I commit di {title} hanno di nuovo il formato atteso.")
                self.db.set_state(key, "0")

    def summary(self) -> dict[str, Any]:
        """Stato attuale di ogni fonte, per stato.json e /stato."""
        out = {}
        for source in LABELS:
            st = self._state(source)
            if st:
                out[source] = {"ok": not st.get("fails"), "fails": st.get("fails", 0), "label": LABELS[source]}
        return out
