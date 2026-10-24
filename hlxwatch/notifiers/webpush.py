"""Notifiche di sistema per chi le ha attivate sul sito (Web Push tramite il bot su Cloudflare).

Il run non conosce i browser iscritti: manda il messaggio al Worker (POST /push/send con PUSH_SECRET), che lo
cifra e lo inoltra a ognuno. Il Worker lavora a gruppi di pochi browser per chiamata, quindi qui si
ripete la chiamata finché non restituisce next = null.
Vanno solo le novità: report giornalieri, analisi AI e avvisi tecnici restano su Telegram.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import requests

from hlxwatch.notifiers.base import SEVERITY_ICON, AlertGroup, Notifier

log = logging.getLogger(__name__)
MAX_LINES = 4
MAX_CALLS = 300


class WebPushNotifier(Notifier):
    name = "webpush"

    def __init__(self, worker_url: str, secret: str, site_url: str):
        self.worker_url = worker_url.rstrip("/")
        self.secret = secret
        self.site_url = site_url if site_url.endswith("/") else site_url + "/"

    def send_text(self, title: str, text: str) -> bool:
        return True

    @staticmethod
    def message(groups: list[AlertGroup], site_url: str) -> dict[str, str]:
        items = sorted((f for g in groups for f in g.findings), key=lambda f: f.score, reverse=True)
        titles = {g.game: g.title for g in groups}
        total = sum(g.total for g in groups)
        lines = [f"{SEVERITY_ICON[f.severity]} [{f.score}] {titles.get(f.game, f.game)}: {(f.token or f.text)[:70]}"
                 for f in items[:MAX_LINES]]
        if total > MAX_LINES:
            lines.append(f"… e altre {total - MAX_LINES}")
        top = items[0]
        if total == 1:
            title = f"HLX Watch: novità in {titles.get(top.game, top.game)}"
            url = f"{site_url}search.html?q={quote((top.token or top.text)[:60])}"
        else:
            title = f"HLX Watch: {total} novità (max {top.score})"
            url = f"{site_url}search.html?new=1&min=50"
        return {"title": title, "body": "\n".join(lines), "url": url, "tag": f"hlx-{int(time.time())}"}

    def send_groups(self, heading: str, groups: list[AlertGroup]) -> bool:
        if not groups:
            return True
        payload = {"message": self.message(groups, self.site_url), "cursor": None}
        headers = {"Authorization": f"Bearer {self.secret}"}
        delivered, statuses = 0, {}
        for _ in range(MAX_CALLS):
            try:
                response = requests.post(f"{self.worker_url}/push/send", json=payload, headers=headers, timeout=60)
            except requests.RequestException as exc:
                log.error("Bot delle notifiche non raggiungibile: %s", exc)
                return False
            if response.status_code != 200:
                log.error("Bot delle notifiche: risposta %s %s", response.status_code, response.text[:200])
                return False
            data = response.json()
            delivered += int(data.get("sent", 0))
            for status, n in (data.get("statuses") or {}).items():
                statuses[status] = statuses.get(status, 0) + n
            if not data.get("next"):
                break
            payload["cursor"] = data["next"]
        log.info("Notifiche di sistema: %d browser, esiti %s", delivered, statuses or "nessun iscritto")
        return True
