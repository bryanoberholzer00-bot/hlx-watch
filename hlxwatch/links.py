"""Link verso le fonti di ogni voce e la loro etichetta ("commit", "build", ...).

Le build Steam non hanno un commit: il link porta alla pagina SteamDB della build, che mostra cosa è
cambiato. È solo un link per chi legge: HLX Watch non scarica mai pagine da SteamDB.
"""

from __future__ import annotations


def steam_build_url(buildid: str | int) -> str:
    return f"https://steamdb.info/patchnotes/{buildid}/"


def steam_app_history_url(appid: str | int) -> str:
    return f"https://steamdb.info/app/{appid}/history/"


def steam_store_url(appid: str | int) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


def link_label(url: str | None) -> str:
    u = url or ""
    if "github.com/" in u and "/commit/" in u:
        return "commit"
    if "steamdb.info/patchnotes/" in u:
        return "build"
    if "steamdb.info/app/" in u:
        return "cronologia"
    if "store.steampowered.com/app/" in u:
        return "pagina Steam"
    return "fonte"
