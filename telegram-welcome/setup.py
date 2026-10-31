"""Configura il Worker di benvenuto: carica i segreti su Cloudflare e collega il webhook di Telegram.

Uso (dalla cartella hlx-watch, dopo `npx wrangler login` e `npx wrangler deploy` in telegram-welcome):
    .venv\\Scripts\\python telegram-welcome\\setup.py https://hlx-watch-bot.<tuo-sottodominio>.workers.dev

Legge TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID da .env; genera un segreto casuale per il webhook.
Per tornare indietro: `python telegram-welcome/setup.py --remove` (cancella il webhook).
"""
from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
ENV = dotenv_values(HERE.parent / ".env")


def wrangler_secret(name: str, value: str) -> None:
    npx = shutil.which("npx") or "npx"
    proc = subprocess.run([npx, "--yes", "wrangler@4", "secret", "put", name], input=value, text=True,
                          encoding="utf-8", errors="replace", capture_output=True, cwd=HERE,
                          shell=sys.platform == "win32")
    if proc.returncode != 0:
        raise SystemExit(f"wrangler secret put {name} fallito:\n{proc.stderr[-800:]}")
    print(f"segreto {name}: caricato")


def main() -> int:
    token = ENV.get("TELEGRAM_BOT_TOKEN")
    chat_id = ENV.get("TELEGRAM_CHAT_ID")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante in .env")
    api = f"https://api.telegram.org/bot{token}"

    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        print("deleteWebhook:", requests.post(f"{api}/deleteWebhook", timeout=20).json())
        return 0
    if len(sys.argv) < 2 or not sys.argv[1].startswith("https://"):
        raise SystemExit(__doc__)
    url = sys.argv[1].rstrip("/")

    webhook_secret = secrets.token_urlsafe(32)
    wrangler_secret("BOT_TOKEN", token)
    wrangler_secret("WEBHOOK_SECRET", webhook_secret)
    wrangler_secret("OWNER_CHAT_ID", chat_id or "")

    r = requests.post(f"{api}/setWebhook", timeout=20, json={
        "url": url, "secret_token": webhook_secret, "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True,
    }).json()
    print("setWebhook:", r.get("ok"), r.get("description", ""))
    commands = [
        {"command": "ultime", "description": "Le ultime scoperte rilevanti"},
        {"command": "cerca", "description": "Cerca nell'archivio, es. /cerca hlx_"},
        {"command": "stato", "description": "Ultima scansione, numeri e ultima analisi AI"},
        {"command": "help", "description": "Cosa fa il bot"},
    ]
    c = requests.post(f"{api}/setMyCommands", timeout=20, json={"commands": commands}).json()
    print("menu dei comandi:", c.get("ok"))
    info = requests.get(f"{api}/getWebhookInfo", timeout=20).json().get("result", {})
    print("webhook attivo su:", info.get("url"), "| errori recenti:", info.get("last_error_message", "nessuno"))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
