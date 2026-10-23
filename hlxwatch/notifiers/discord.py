from __future__ import annotations

import logging
import time
from typing import Any

import requests

from hlxwatch.notifiers.base import SEVERITY_COLOR, AlertGroup, Notifier, finding_line

log = logging.getLogger(__name__)
MAX_EMBEDS = 10
MAX_TOTAL_CHARS = 5800
MAX_DESCRIPTION = 3900


def _severity(score: int) -> str:
    return "high" if score >= 100 else "medium" if score >= 50 else "low"


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(self, webhook_url: str, username: str = "HLX Watch"):
        self.url = webhook_url
        self.username = username

    def _post(self, payload: dict[str, Any]) -> bool:
        payload.setdefault("username", self.username)
        for _ in range(4):
            try:
                response = requests.post(self.url, json=payload, timeout=30)
            except requests.RequestException as exc:
                log.error("Discord non raggiungibile: %s", exc)
                return False
            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after", 2))
                except ValueError:
                    wait = 2.0
                time.sleep(wait + 0.5)
                continue
            if response.status_code >= 400:
                log.error("Discord ha risposto %s: %s", response.status_code, response.text[:300])
                return False
            return True
        return False

    def send_text(self, title: str, text: str) -> bool:
        return self._post({"content": f"**{title}**\n{text}"[:1990]})

    def send_groups(self, heading: str, groups: list[AlertGroup]) -> bool:
        embeds: list[dict[str, Any]] = []
        for group in groups:
            description = ""
            shown = 0
            for finding in group.findings:
                line = finding_line(finding, "markdown")
                if len(description) + len(line) + 1 > MAX_DESCRIPTION:
                    break
                description += line + "\n"
                shown += 1
            if group.total > shown:
                description += f"… e altre {group.total - shown} voci (vedi dashboard)"
            embeds.append({
                "title": f"{group.title}: {group.total} novità (punteggio max {group.max_score})",
                "description": description or "-",
                "color": SEVERITY_COLOR[_severity(group.max_score)],
            })

        ok = True
        batch: list[dict[str, Any]] = []
        size = 0
        first = True
        for embed in embeds:
            length = len(embed["title"]) + len(embed["description"])
            if batch and (len(batch) >= MAX_EMBEDS or size + length > MAX_TOTAL_CHARS):
                ok &= self._post({"content": heading if first else "", "embeds": batch})
                first = False
                batch, size = [], 0
            batch.append(embed)
            size += length
        if batch:
            ok &= self._post({"content": heading if first else "", "embeds": batch})
        return ok
