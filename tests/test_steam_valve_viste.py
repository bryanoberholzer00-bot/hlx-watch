"""Pagina di Valve su Steam: un'app che sparisce per un giro e ricompare non è un prodotto nuovo."""
from hlxwatch.sources.steam_valve import changes


def test_app_back_after_a_missing_reading_is_not_new():
    lista = lambda apps: {"dichiarati": len(apps), "app": apps}          # noqa: E731
    old = {"tutti": lista(["1", "2"]), "arrivo": lista([]), "viste": ["1", "2", "3"]}
    new = {"tutti": lista(["1", "2", "3"]), "arrivo": lista([])}
    assert [c for c in changes(old, new) if c["tipo"] == "nuova"] == []
    new2 = {"tutti": lista(["1", "2", "4"]), "arrivo": lista([])}
    assert [c["appid"] for c in changes(old, new2) if c["tipo"] == "nuova"] == ["4"]
