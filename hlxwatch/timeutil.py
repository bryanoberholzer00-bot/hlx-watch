"""Fuso orario di visualizzazione.

I dati restano in UTC (database, API, confronti): è il formato di GitHub, Steam e dei runner di Actions,
e non ha ambiguità con l'ora legale. Questo modulo converte nel fuso di chi legge (general.timezone,
default Europe/Rome) gli orari mostrati e l'ora del report giornaliero.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Rome"
_tz_name = DEFAULT_TZ
_tz = ZoneInfo(DEFAULT_TZ)


def set_timezone(name: str | None) -> None:
    global _tz, _tz_name
    name = name or DEFAULT_TZ
    try:
        _tz, _tz_name = ZoneInfo(name), name
    except ZoneInfoNotFoundError:
        _tz, _tz_name = ZoneInfo(DEFAULT_TZ), DEFAULT_TZ


def tz() -> ZoneInfo:
    return _tz


def tz_label() -> str:
    return "ora italiana" if _tz_name == "Europe/Rome" else _tz_name


def now_local() -> datetime:
    return datetime.now(_tz)


def today_local() -> str:
    return now_local().strftime("%Y-%m-%d")


def _parse(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        if len(text) == 10:                       # solo data: nessuna conversione possibile
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz)


def local(value: str | datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """'2026-10-24T11:53:00Z' -> '2026-10-24 13:53' (ora italiana). Le sole date passano invariate."""
    dt = _parse(value)
    if dt is None:
        return str(value or "")[:10]
    return dt.strftime(fmt)


def local_day(value: str | datetime | None) -> str:
    return local(value, "%Y-%m-%d")


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Inizio e fine (esclusa) di un giorno del calendario locale, espressi in UTC."""
    start = datetime.combine(day, time.min, tzinfo=_tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def register_filters(env) -> None:
    """Filtri Jinja: {{ x|local }} e {{ x|localday }}, più la variabile tz_label."""
    env.filters["local"] = local
    env.filters["localday"] = local_day
    env.globals["tz_label"] = tz_label()
