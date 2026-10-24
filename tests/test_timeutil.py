from datetime import date, datetime, timezone

from hlxwatch import timeutil
from hlxwatch.reports.daily import period_for


def setup_function():
    timeutil.set_timezone("Europe/Rome")


def test_local_conversion_summer_and_winter():
    assert timeutil.local("2026-10-24T11:53:00Z") == "2026-10-24 13:53"      # ora legale: UTC+2
    assert timeutil.local("2026-10-26T11:53:00Z") == "2026-10-26 12:53"      # ora solare: UTC+1
    assert timeutil.local_day("2026-06-26T23:30:00Z") == "2026-06-27"        # commit a mezzanotte passata in Italia
    assert timeutil.local("2026-10-24") == "2026-10-24"                       # una data sola resta com'è
    assert timeutil.local(None) == ""


def test_local_day_bounds_across_dst_change():
    # 25 ottobre 2026: si torna all'ora solare, il giorno dura 25 ore
    start, end = timeutil.local_day_bounds_utc(date(2026, 10, 25))
    assert start == datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)
    assert (end - start).total_seconds() == 25 * 3600


def test_report_period_uses_italian_days():
    start, end = period_for(date(2026, 10, 24), days=1)
    assert start == datetime(2026, 10, 23, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    start7, end7 = period_for(date(2026, 10, 24), days=7)
    assert start7 == datetime(2026, 10, 17, 22, 0, tzinfo=timezone.utc) and end7 == end


def test_unknown_timezone_falls_back():
    timeutil.set_timezone("Marte/Olympus")
    assert timeutil.tz_label() == "ora italiana"
