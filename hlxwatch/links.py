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


def duplicates_label(main_file: str, others: list[str], short: bool = False) -> str:
    """Le voci accorpate con la stessa stringa: altri file dello stesso commit, oppure altre righe dello stesso
    file (una classe ripetuta in più punti di un dump). Prima diceva sempre "altri N file", anche per le righe."""
    files = len({f for f in others if f != main_file})
    lines = sum(1 for f in others if f == main_file)
    if short:
        parts = ([f"+{files} file"] if files else []) + ([f"+{lines} righe" if lines > 1 else "+1 riga"] if lines else [])
        return f"({', '.join(parts)})" if parts else ""
    parts = ([f"+ altri {files} file nello stesso commit" if files > 1 else "+ un altro file nello stesso commit"]
             if files else [])
    parts += ([f"+ altre {lines} righe nello stesso file" if lines > 1 else "+ un'altra riga nello stesso file"]
              if lines else [])
    return ", ".join(parts)


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
