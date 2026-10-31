from __future__ import annotations

import html
import logging
import time

import requests

from hlxwatch.noise import protected as noise_protected
from hlxwatch.notifiers.base import AlertGroup, Notifier, finding_line

log = logging.getLogger(__name__)
MAX_MESSAGE = 4000
MAX_BUTTONS = 8


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def _send_chunk(self, text: str, markup: dict | None = None) -> bool:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            payload["reply_markup"] = markup
        for _ in range(4):
            try:
                response = requests.post(self.url, json=payload, timeout=30)
            except requests.RequestException as exc:
                log.error("Telegram non raggiungibile: %s", exc)
                return False
            if response.status_code == 429:
                try:
                    wait = float(response.json().get("parameters", {}).get("retry_after", 3))
                except ValueError:
                    wait = 3.0
                time.sleep(wait + 0.5)
                continue
            if response.status_code >= 400:
                log.error("Telegram ha risposto %s: %s", response.status_code, response.text[:300])
                return False
            return True
        return False

    def _send(self, lines: list[str], markup: dict | None = None) -> bool:
        """I pulsanti (markup) vanno sotto l'ultimo messaggio, se il testo va diviso in più parti."""
        ok = True
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > MAX_MESSAGE:
                ok &= self._send_chunk(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            ok &= self._send_chunk(chunk, markup)
        return ok

    def send_text(self, title: str, text: str) -> bool:
        return self._send([f"<b>{html.escape(title)}</b>"] + [html.escape(ln) for ln in text.splitlines()])

    def send_groups(self, heading: str, groups: list[AlertGroup]) -> bool:
        lines = [f"<b>{html.escape(heading)}</b>"]
        buttons: list[dict] = []
        n = 0
        for group in groups:
            lines.append("")
            lines.append(f"<b>{html.escape(group.title)}</b>: {group.total} novità (punteggio max {group.max_score})")
            for finding in group.findings:
                n += 1
                lines.append(f"{n}. {finding_line(finding, 'html')}")
                # pulsante 👎 "è rumore" (telegram-welcome/src/noise.js): non per HLX forti e possibili annunci
                if finding.id is not None and len(buttons) < MAX_BUTTONS and not noise_protected(finding.keywords):
                    buttons.append({"text": f"👎 {n}", "callback_data": f"r:{finding.id}"})
            if group.total > len(group.findings):
                lines.append(f"… e altre {group.total - len(group.findings)} voci (vedi dashboard)")
        markup = None
        if buttons:
            lines.append("")
            lines.append("<i>👎 = è rumore: niente più avvisi per le voci di quel tipo (ripremi per annullare)</i>")
            markup = {"inline_keyboard": [buttons[i:i + 4] for i in range(0, len(buttons), 4)]}
        return self._send(lines, markup)
