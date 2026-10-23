"""Worker isolato per Steam PICS.

Fa login ANONIMO a Steam con la libreria `steam` (ValvePython), chiede le informazioni
prodotto delle app richieste e, se richiesto, l'elenco delle app cambiate da un change number.
Stampa una sola riga JSON su stdout. Gira in un sottoprocesso separato perché la libreria
usa gevent: così un blocco o un crash non tocca il processo principale.

Uso:  python -m hlxwatch.sources.steam_pics_worker --apps 730,570 --since 12345678
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", default="", help="appid separati da virgola")
    parser.add_argument("--since", type=int, default=-1,
                        help="change number da cui chiedere le modifiche (-1 = non chiedere, 0 = solo il numero attuale)")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    result: dict = {"ok": False, "apps": {}, "changes": None, "error": None}
    try:
        from steam.client import SteamClient
        from steam.enums import EResult
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        result["error"] = f"libreria 'steam' non installata ({exc}): pip install \"steam[client]\""
        print(json.dumps(result))
        return 2

    client = SteamClient()
    try:
        res = client.anonymous_login()
        if res != EResult.OK:
            result["error"] = f"login anonimo fallito: {res!r}"
            print(json.dumps(result))
            return 3

        appids = [int(x) for x in args.apps.split(",") if x.strip()]
        if appids:
            info = client.get_product_info(apps=appids, timeout=args.timeout) or {}
            result["apps"] = {str(k): v for k, v in (info.get("apps") or {}).items()}

        if args.since >= 0:
            changes = client.get_changes_since(args.since, app_changes=True, package_changes=False)
            if changes is None:
                result["error"] = "timeout su get_changes_since"
            else:
                result["changes"] = {
                    "current": int(changes.current_change_number),
                    "since": int(changes.since_change_number),
                    "force_full": bool(changes.force_full_update) or bool(getattr(changes, "force_full_app_update", False)),
                    "apps": [{"appid": int(a.appid), "change_number": int(a.change_number)}
                             for a in changes.app_changes],
                }
        result["ok"] = True
    except Exception as exc:  # qualunque cosa succeda, riportala al processo padre
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["trace"] = traceback.format_exc()
    finally:
        for closer in (client.logout, client.disconnect):
            try:
                closer()
            except Exception:
                pass

    sys.stdout.write(json.dumps(result, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
