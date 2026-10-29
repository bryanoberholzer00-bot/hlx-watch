from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ENV_CONFIG = "HLXWATCH_CONFIG"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


class Config:
    """Accesso comodo al config YAML + variabili d'ambiente."""

    def __init__(self, raw: dict[str, Any], path: Path):
        self.raw = raw or {}
        self.path = path
        self.base_dir = path.parent.resolve()

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def resolve_path(self, value: str | None, default: str) -> Path:
        p = Path(value or default)
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    # --- percorsi -------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self.resolve_path(self.get("general.data_dir"), "data")

    @property
    def db_path(self) -> Path:
        return self.resolve_path(self.get("general.db_path"), "data/hlxwatch.sqlite")

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    # --- segreti da .env -------------------------------------------------
    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        return value if value not in (None, "") else default

    @property
    def github_token(self) -> str | None:
        return self.env("GITHUB_TOKEN")

    @property
    def discord_webhook(self) -> str | None:
        return self.env("DISCORD_WEBHOOK_URL")

    @property
    def telegram_token(self) -> str | None:
        return self.env("TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> str | None:
        return self.env("TELEGRAM_CHAT_ID")

    @property
    def steam_web_api_key(self) -> str | None:
        return self.env("STEAM_WEB_API_KEY")

    # --- sezioni ---------------------------------------------------------
    @property
    def keywords(self) -> dict[str, Any]:
        return self.get("keywords", {}) or {}

    @property
    def github_repos(self) -> list[dict[str, Any]]:
        return [r for r in (self.get("github.repos", []) or []) if r.get("enabled", True)]

    @property
    def steam_watch_apps(self) -> list[dict[str, Any]]:
        return self.get("steam.watch_apps", []) or []

    def game_title(self, name: str) -> str:
        if name == "steamtracking":
            return "SteamTracking (nuovi repository)"
        if name == "hlxwatch":
            return "HLX Watch (prova)"
        for repo in self.get("github.repos", []) or []:
            if repo.get("name") == name:
                return repo.get("title", name)
        for app in self.steam_watch_apps:
            if app.get("name") == name:
                return app.get("title", name)
        for app in self.get("steam_news.apps", []) or []:                  # app seguite solo per i post ufficiali
            if app.get("name") == name and app.get("title"):
                return app["title"]
        for game in self.get("local.games", []) or []:
            if game.get("name") == name:
                return game.get("title", name)
        for repo in self.get("archaeology.extra_repos", []) or []:      # giochi dismessi, solo archeologia
            if repo.get("name") == name:
                return repo.get("title", name)
        return name


def find_config_path(explicit: str | None = None) -> Path:
    candidates = [explicit, os.environ.get(ENV_CONFIG), "config.yaml", "config.yml",
                  str(PACKAGE_ROOT / "config.yaml")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate).resolve()
    raise ConfigError(
        "config.yaml non trovato: esegui `python -m hlxwatch init` oppure passa --config PERCORSO"
    )


def load_config(explicit: str | None = None) -> Config:
    path = find_config_path(explicit)
    # .env accanto al config e .env nella cartella corrente (le variabili già presenti vincono)
    load_dotenv(path.parent / ".env")
    load_dotenv()
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(raw, path)
    from hlxwatch.timeutil import set_timezone
    set_timezone(cfg.get("general.timezone"))
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
