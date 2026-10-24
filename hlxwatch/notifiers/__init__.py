from __future__ import annotations

import logging

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.notifiers.base import Notifier, group_findings
from hlxwatch.notifiers.discord import DiscordNotifier
from hlxwatch.notifiers.telegram import TelegramNotifier
from hlxwatch.notifiers.webpush import WebPushNotifier

log = logging.getLogger(__name__)

__all__ = ["Notifier", "DiscordNotifier", "TelegramNotifier", "WebPushNotifier", "load_notifiers", "notify_findings", "notify_text"]


def load_notifiers(cfg: Config) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if cfg.get("notifiers.discord.enabled", False):
        if cfg.discord_webhook:
            notifiers.append(DiscordNotifier(cfg.discord_webhook, cfg.get("notifiers.discord.username", "HLX Watch")))
        else:
            log.warning("Discord abilitato ma DISCORD_WEBHOOK_URL manca in .env")
    if cfg.get("notifiers.telegram.enabled", False):
        if cfg.telegram_token and cfg.telegram_chat_id:
            notifiers.append(TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id))
        else:
            log.warning("Telegram abilitato ma TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID mancano in .env")
    if cfg.get("notifiers.webpush.enabled", False):
        secret, worker = cfg.env("PUSH_SECRET"), cfg.get("notifiers.webpush.worker_url")
        if secret and worker:
            site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
            notifiers.append(WebPushNotifier(worker, secret, site))
        else:
            log.warning("Notifiche di sistema abilitate ma mancano PUSH_SECRET in .env o notifiers.webpush.worker_url")
    return notifiers


def notify_findings(notifiers: list[Notifier], findings: list[Finding], cfg: Config, heading: str) -> bool:
    if not notifiers or not findings:
        return False
    groups = group_findings(findings, cfg)
    ok = True
    for notifier in notifiers:
        try:
            sent = notifier.send_groups(heading, groups)
            log.info("Notifica %s: %s (%d voci)", notifier.name, "inviata" if sent else "FALLITA", len(findings))
            ok &= sent
        except Exception:
            log.exception("Errore nel notificatore %s", notifier.name)
            ok = False
    return ok


def notify_text(notifiers: list[Notifier], title: str, text: str) -> bool:
    ok = bool(notifiers)
    for notifier in notifiers:
        try:
            ok &= notifier.send_text(title, text)
        except Exception:
            log.exception("Errore nel notificatore %s", notifier.name)
            ok = False
    return ok
