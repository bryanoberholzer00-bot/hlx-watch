import re
from datetime import date

from hlxwatch.dashboard.app import _scale_top, timeline_svg


def rows():
    return [
        {"day": "2026-10-07", "n": 2936, "n_new": 0, "n_high": 0, "n_mid": 0, "max_score": 15},
        {"day": "2026-10-29", "n": 30, "n_new": 8, "n_high": 15, "n_mid": 1, "max_score": 115},
        {"day": "2026-10-30", "n": 31, "n_new": 20, "n_high": 1, "n_mid": 1, "max_score": 100},
        {"day": "2026-09-01", "n": 1, "n_new": 1, "n_high": 0, "n_mid": 1, "max_score": 55},
    ]


def test_one_column_per_day_with_months_and_info():
    svg = timeline_svg(rows(), 90, link="search.html?since={day}&until={day}", today=date(2026, 11, 1))
    assert svg.count("<a href=") == 90                                # anche i giorni vuoti
    assert "1 ott" in svg and "1 nov" in svg and 'text-anchor="end">1 nov' in svg
    assert "29/10/2026: 15 importanti (≥100), 1 media (50-99), 14 a bassa rilevanza, 8 mai viste prima" in svg
    assert "07/10/2026: 0 importanti (≥100), 0 medie (50-99), 2936 a bassa rilevanza" in svg
    heights = [float(h) for h in re.findall(r'height="([\d.]+)" fill="#e53935"', svg)]
    assert max(heights) > 100                                         # il rumore non schiaccia più i giorni importanti


def test_scale_cuts_a_lonely_peak():
    assert _scale_top([2, 6, 10]) == 10                                # nessun picco isolato: scala intera
    assert _scale_top([2, 3, 100]) == 4                               # 100 esce dal grafico con il suo numero
    assert _scale_top([]) == 1


def test_peak_out_of_scale_shows_its_number():
    data = rows() + [{"day": "2026-10-15", "n": 400, "n_new": 0, "n_high": 400, "n_mid": 0, "max_score": 120}]
    svg = timeline_svg(data, 90, today=date(2026, 11, 1))
    assert "400&#8593;" in svg
