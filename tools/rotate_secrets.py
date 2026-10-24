"""Rotazione dei segreti di HLX Watch senza farli passare dalla chat.

Per ogni segreto: generi il valore nuovo sul sito del servizio, lo COPI e premi Invio qui. Lo script
lo legge dagli appunti (niente incolla, niente valori a schermo), lo verifica con una chiamata reale e,
solo se funziona, aggiorna .env, i Secrets di GitHub e, per Telegram, il Worker di Cloudflare con il
webhook. Poi svuota gli appunti.

Uso (dalla cartella hlx-watch):   .venv\\Scripts\\python tools\\rotate_secrets.py
Scrivi s e premi Invio per saltare un segreto.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
WORKER_URL = "https://hlx-watch-bot.bryan-oberholzer00.workers.dev"
BOT_USERNAME = "half_radar_bot"


def check_github(value: str) -> str | None:
    r = requests.get("https://api.github.com/rate_limit", headers={"Authorization": f"Bearer {value}"}, timeout=20)
    if r.status_code != 200:
        return f"GitHub ha risposto {r.status_code}"
    limit = r.json().get("resources", {}).get("core", {}).get("limit")
    return None if limit and limit >= 5000 else f"limite {limit}: non sembra un token personale valido"


def check_telegram(value: str) -> str | None:
    r = requests.get(f"https://api.telegram.org/bot{value}/getMe", timeout=20).json()
    if not r.get("ok"):
        return "Telegram non accetta il token"
    if r["result"].get("username") != BOT_USERNAME:
        return f"è il token di @{r['result'].get('username')}, non di @{BOT_USERNAME}"
    return None


def check_steam(value: str) -> str | None:
    r = requests.get("https://api.steampowered.com/IStoreService/GetAppList/v1/",
                     params={"key": value, "max_results": 1}, timeout=30)
    return None if r.status_code == 200 else f"Steam ha risposto {r.status_code}"


def check_anthropic(value: str) -> str | None:
    import anthropic
    try:
        anthropic.Anthropic(api_key=value).models.retrieve("claude-opus-5-5")
    except anthropic.AuthenticationError:
        return "chiave non valida"
    except anthropic.APIStatusError as exc:
        return f"errore API {exc.status_code}"
    return None


SECRETS = [
    {
        "label": "Token GitHub",
        "how": "github.com/settings/tokens?type=beta -> token 'hlx-radar' -> Regenerate token (stesse impostazioni).",
        "env": "GITHUB_TOKEN", "gh": "GH_PAT", "check": check_github,
    },
    {
        "label": "Token del bot Telegram",
        "how": "In Telegram scrivi /revoke a @BotFather e scegli @half_radar_bot: ti dà il token nuovo "
               "(quello vecchio smette subito di funzionare).",
        "env": "TELEGRAM_BOT_TOKEN", "gh": "TELEGRAM_BOT_TOKEN", "check": check_telegram, "worker": True,
    },
    {
        "label": "Chiave Steam Web API",
        "how": "steamcommunity.com/dev/apikey -> Revoca chiave, poi registrane una nuova (dominio: hlx-watch).",
        "env": "STEAM_WEB_API_KEY", "gh": "STEAM_WEB_API_KEY", "check": check_steam,
    },
    {
        "label": "Chiave API Anthropic",
        "how": "console.anthropic.com -> API Keys -> Create Key. Dopo l'aggiornamento elimina la chiave vecchia.",
        "env": "ANTHROPIC_API_KEY", "gh": "ANTHROPIC_API_KEY", "check": check_anthropic,
    },
]


def read_clipboard() -> str:
    """Legge gli appunti senza stamparli: niente incolla nel terminale, niente valori a schermo."""
    if sys.platform == "win32":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()
    for cmd in (["pbpaste"], ["xclip", "-selection", "clipboard", "-o"]):
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        except FileNotFoundError:
            continue
    return ""


def clear_clipboard() -> None:
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"], capture_output=True)


def set_env(name: str, value: str) -> None:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    line = f"{name}={value}"
    if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
        text = re.sub(rf"^{re.escape(name)}=.*$", lambda _: line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    ENV.write_text(text, encoding="utf-8")


def set_github_secret(name: str, value: str) -> bool:
    r = subprocess.run(["gh", "secret", "set", name], input=value, text=True, capture_output=True, cwd=ROOT)
    return r.returncode == 0


def update_worker() -> bool:
    """Il Worker usa il token per rispondere a /start: ricarica i suoi segreti e ricollega il webhook."""
    r = subprocess.run([sys.executable, str(ROOT / "telegram-welcome" / "setup.py"), WORKER_URL],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and "setWebhook: True" in r.stdout
    if not ok:
        print("   uscita di setup.py:\n   " + (r.stdout + r.stderr)[-600:].replace("\n", "\n   "))
    return ok


def main() -> int:
    print(__doc__.split("Uso")[0].strip(), "\n")
    done = []
    # facoltativo: "rotate_secrets.py anthropic" chiede solo i segreti il cui nome contiene quella parola
    only = [a.lower() for a in sys.argv[1:]]
    selected = [s for s in SECRETS if not only or any(o in s["label"].lower() for o in only)]
    for s in selected:
        print(f"== {s['label']} ==")
        print(f"   Come: {s['how']}")
        while True:
            typed = input("   Copia il valore nuovo, poi premi SOLO Invio qui (s + Invio per saltare): ").strip()
            if typed.lower() == "s":
                print("   saltato\n")
                break
            if typed:
                # il valore è stato incollato al prompt: lo cancello subito dallo schermo e lo uso
                sys.stdout.write("\033[F\033[2K   (valore incollato: cancellato dallo schermo)\n")
                sys.stdout.flush()
                value = typed
            else:
                value = read_clipboard()
            if not value:
                print("   Gli appunti sono vuoti: copia il valore e riprova.")
                continue
            if any(ch.isspace() for ch in value) or len(value) > 300:
                print("   Negli appunti non c'è un token (testo con spazi o troppo lungo): copia solo il valore.")
                continue
            print("   verifico...", end=" ", flush=True)
            try:
                problem = s["check"](value)
            except Exception as exc:                       # rete, ecc.: non salvo nulla
                problem = f"verifica non riuscita ({type(exc).__name__})"
            if problem:
                print(f"NON valido: {problem}. Copia di nuovo il valore giusto e riprova.")
                continue
            clear_clipboard()
            print("valido.")
            set_env(s["env"], value)
            print(f"   .env aggiornato ({s['env']})")
            print(f"   Secret GitHub {s['gh']}: {'aggiornato' if set_github_secret(s['gh'], value) else 'ERRORE'}")
            if s.get("worker"):
                print(f"   Worker Cloudflare e webhook: {'aggiornati' if update_worker() else 'ERRORE (vedi sopra)'}")
            done.append(s["label"])
            print()
            break

    print("Riepilogo:", ", ".join(done) if done else "nessun segreto cambiato")
    if "Chiave API Anthropic" in done:
        print("Ricorda: elimina la chiave Anthropic vecchia dalla console.")

    answer = input("\nVuoi copiare negli appunti la password della chiave dell'app Android, per salvarla nel "
                   "gestore di password? [s/N] ").strip().lower()
    if answer == "s":
        password = ""
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("ANDROID_KEYSTORE_PASSWORD="):
                password = line.split("=", 1)[1]
        if password and sys.platform == "win32":
            subprocess.run("clip", input=password, text=True, shell=True)
            print("Copiata negli appunti: incollala nel gestore di password, poi copia qualcos'altro per cancellarla.")
        else:
            print("Password non trovata nel .env o sistema non Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
