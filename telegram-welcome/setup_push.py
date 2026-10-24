"""Configura le notifiche di sistema del sito: chiavi in .env, sul Worker Cloudflare e su GitHub.

Uso (dalla cartella hlx-watch, dopo `npx wrangler login`):
    .venv\\Scripts\\python telegram-welcome\\setup_push.py            crea ciò che manca e lo carica
    .venv\\Scripts\\python telegram-welcome\\setup_push.py --rotate   cambia PUSH_SECRET (il segreto tra run e bot)

VAPID_PRIVATE_JWK è la chiave con cui il bot firma le notifiche: se cambia, ogni browser deve riattivarle,
quindi lo script non la rigenera mai da solo. Nessun valore viene stampato.
"""
from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import wrangler_secret  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def b64u(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


def new_vapid_jwk() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_numbers()
    return json.dumps({"kty": "EC", "crv": "P-256", "d": b64u(key.private_numbers().private_value),
                       "x": b64u(pub.x), "y": b64u(pub.y)}, separators=(",", ":"))


def set_env(name: str, value: str) -> None:
    """Scrive o sostituisce NOME='valore' in .env lasciando intatto il resto del file."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    line = f"{name}='{value}'"
    for i, existing in enumerate(lines):
        if existing.startswith(f"{name}="):
            lines[i] = line
            break
    else:
        lines += ["", "# notifiche di sistema del sito (telegram-welcome/setup_push.py)", line]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gh_secret(name: str, value: str) -> None:
    gh = shutil.which("gh") or "gh"
    proc = subprocess.run([gh, "secret", "set", name], input=value, text=True, capture_output=True, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"gh secret set {name} fallito:\n{proc.stderr[-500:]}")
    print(f"segreto GitHub {name}: caricato")


def main() -> int:
    env = dotenv_values(ENV_FILE)
    rotate = "--rotate" in sys.argv[1:]
    if not env.get("VAPID_PRIVATE_JWK"):
        set_env("VAPID_PRIVATE_JWK", new_vapid_jwk())
        print("VAPID_PRIVATE_JWK: creata in .env")
    if rotate or not env.get("PUSH_SECRET"):
        set_env("PUSH_SECRET", secrets.token_urlsafe(32))
        print("PUSH_SECRET:", "cambiato" if rotate else "creato", "in .env")
    env = dotenv_values(ENV_FILE)
    json.loads(env["VAPID_PRIVATE_JWK"])                       # controllo: deve essere un JSON valido
    wrangler_secret("VAPID_PRIVATE_JWK", env["VAPID_PRIVATE_JWK"])
    wrangler_secret("PUSH_SECRET", env["PUSH_SECRET"])
    gh_secret("PUSH_SECRET", env["PUSH_SECRET"])
    print("fatto: notifiche di sistema configurate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
