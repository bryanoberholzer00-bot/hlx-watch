"""L'orologio di Valve: a che ora e in che giorno lavora Valve, e a che ora arrivano le novità in Italia.

Il contatore di Valve (hlxwatch/contatore.py) dice quante modifiche salva tutta Valve tra una build e l'altra; le
build portano l'ora di Seattle in cui sono state compilate. Tra due build vicine (al massimo GAP_HOURS ore) le modifiche
si dividono in parti uguali sulle ore che le separano: sommando anni di build si ottiene il ritmo di ogni ora della
settimana. Di giorno le build sono tante e il numero è solido; di notte e nel fine settimana sono poche, e una casella
con meno di MIN_HOURS ore di dati resta vuota.

La prova fatta a novembre 2026 sullo storico dal 2016: la sera Valve non lavora di più prima degli annunci (prima di
quello di Alyx anzi un po' meno), ma nei tre mesi prima dell'uscita di Alyx la sera e il fine settimana erano più
pieni del solito. L'orologio quindi non annuncia niente: mostra come lavora Valve e se adesso lavora fuori orario.
Le conclusioni della pagina sono calcolate dai numeri a ogni giro, non scritte a mano.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from hlxwatch import contatore as c

GAP_HOURS = 4            # intervalli più lunghi tra due build non dicono a che ora si è lavorato
MIN_HOURS = 2.0          # ore di dati sotto le quali una casella dell'orologio resta vuota
FEW_HOURS = 10.0         # ore di dati sotto le quali un confronto di sera o di fine settimana "ha pochi dati"
DIFF = 0.10              # dieci punti di differenza tra due rapporti: sotto, "come prima"
DAY_HOURS = range(10, 17)        # il cuore della giornata di lavoro (ora di Seattle)
EVENING_HOURS = range(18, 22)    # la sera, lunedì-venerdì
WEEKEND_HOURS = range(9, 18)     # il giorno, sabato e domenica
DAYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
ROME = ZoneInfo("Europe/Rome")
SEATTLE = ZoneInfo("America/Los_Angeles")
# le novità che contano per HLX: i giochi Source 2 e SteamVR, dove compaiono le tracce
NEWS_GAMES = ("cs2", "dota2", "deadlock", "alyx", "steamvr")
ALYX_ANNOUNCE = datetime(2019, 11, 21)
ALYX_RELEASE = datetime(2020, 3, 23)
QUARTERS_FROM = datetime(2017, 1, 1)


def grid(env: c.Envelope, a: datetime, b: datetime) -> tuple[list[list[float]], list[list[float]]]:
    """Modifiche e ore di dati per ogni ora della settimana (giorno 0 = lunedì), ora di Seattle."""
    changes = [[0.0] * 24 for _ in range(7)]
    hours = [[0.0] * 24 for _ in range(7)]
    for i in range(1, len(env.t)):
        t1, t2 = env.t[i - 1], env.t[i]
        if t1 < a or t2 > b:
            continue
        span = (t2 - t1).total_seconds() / 3600
        if span <= 0 or span > GAP_HOURS:
            continue
        rate = (env.v[i] - env.v[i - 1]) / span
        t = t1
        while t < t2:
            nxt = min(t2, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            part = (nxt - t).total_seconds() / 3600
            changes[t.weekday()][t.hour] += rate * part
            hours[t.weekday()][t.hour] += part
            t = nxt
    return changes, hours


def _rate(changes: list[list[float]], hours: list[list[float]], days: range | tuple, hrs: range) -> tuple[float | None, float]:
    ch = sum(changes[d][h] for d in days for h in hrs)
    ho = sum(hours[d][h] for d in days for h in hrs)
    return (ch / ho if ho else None), ho


def ratios(env: c.Envelope, a: datetime, b: datetime) -> dict[str, Any]:
    """Il ritmo di giorno e, in rapporto, quello della sera e del fine settimana."""
    ch, ho = grid(env, a, b)
    day, day_h = _rate(ch, ho, range(5), DAY_HOURS)
    eve, eve_h = _rate(ch, ho, range(5), EVENING_HOURS)
    we, we_h = _rate(ch, ho, (5, 6), WEEKEND_HOURS)
    return {"giorno": day, "ore_giorno": day_h,
            "sera": eve / day if eve is not None and day else None, "ore_sera": eve_h, "pochi_sera": eve_h < FEW_HOURS,
            "weekend": we / day if we is not None and day else None, "ore_weekend": we_h,
            "pochi_weekend": we_h < FEW_HOURS}


def heatmap(env: c.Envelope, a: datetime, b: datetime) -> dict[str, Any]:
    ch, ho = grid(env, a, b)
    cells = [[ch[d][h] / ho[d][h] if ho[d][h] >= MIN_HOURS else None for h in range(24)] for d in range(7)]
    top = max((v for row in cells for v in row if v is not None), default=0) or 1
    rows = [{"giorno": DAYS[d], "celle": [{"ora": h, "ritmo": v, "forza": (v / top) if v is not None else None}
                                          for h, v in enumerate(cells[d])]} for d in range(7)]
    # la giornata tipo: le ore dal lunedì al venerdì sopra metà del picco
    profile = []
    for h in range(24):
        r, hrs = _rate(ch, ho, range(5), range(h, h + 1))
        profile.append(r if hrs >= MIN_HOURS else None)
    known = [(h, v) for h, v in enumerate(profile) if v is not None]
    peak_h, peak = max(known, key=lambda x: x[1]) if known else (None, None)
    busy = [h for h, v in known if peak and v >= peak / 2]
    return {"righe": rows, "massimo": top, "profilo": profile, "picco_ora": peak_h, "picco": peak,
            "inizio": min(busy) if busy else None, "fine": (max(busy) + 1) if busy else None}


def italian_offset(now: datetime) -> int:
    """Quante ore l'Italia è avanti rispetto a Seattle adesso (di solito 9, 8 per qualche settimana l'anno)."""
    aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return round((aware.astimezone(ROME).utcoffset() - aware.astimezone(SEATTLE).utcoffset()).total_seconds() / 3600)


def compare(now: dict[str, Any], before: dict[str, Any], key: str) -> str | None:
    """'di più', 'di meno' o 'come prima'; None se i dati sono troppo pochi."""
    a, b = now.get(key), before.get(key)
    if a is None or b is None or now.get(f"pochi_{key}") or before.get(f"pochi_{key}"):
        return None
    if a >= b + DIFF:
        return "di più"
    if a <= b - DIFF:
        return "di meno"
    return "come prima"


def quarters(env: c.Envelope, end: datetime) -> list[dict[str, Any]]:
    out = []
    t = QUARTERS_FROM
    while t < end:
        e = min(t + timedelta(days=91), end)
        r = ratios(env, t, e)
        out.append({"da": t.date().isoformat(), "a": e.date().isoformat(), **r,
                    "alyx": t <= ALYX_RELEASE and e >= ALYX_ANNOUNCE - timedelta(days=90)})
        t += timedelta(days=91)
    return out


def alyx_check(env: c.Envelope) -> dict[str, Any] | None:
    """Prima di Alyx Valve lavorava di più fuori orario? Tre mesi prima dell'annuncio e tre mesi prima dell'uscita,
    contro un anno normale (i dodici mesi che finiscono tre mesi prima dell'annuncio)."""
    if not env.t or env.t[0] > ALYX_ANNOUNCE - timedelta(days=500):
        return None
    base_end = ALYX_ANNOUNCE - timedelta(days=90)
    base = ratios(env, base_end - timedelta(days=365), base_end)
    ann = ratios(env, ALYX_ANNOUNCE - timedelta(days=90), ALYX_ANNOUNCE)
    rel = ratios(env, ALYX_RELEASE - timedelta(days=90), ALYX_RELEASE)
    return {"base": base, "annuncio": ann, "uscita": rel,
            "annuncio_sera": compare(ann, base, "sera"), "annuncio_weekend": compare(ann, base, "weekend"),
            "uscita_sera": compare(rel, base, "sera"), "uscita_weekend": compare(rel, base, "weekend")}


def arrivals(db: Any, now: datetime, days: int = 365) -> dict[str, Any]:
    """A che ora italiana compaiono gli aggiornamenti pubblici dei giochi (i commit di SteamTracking)."""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marks = ",".join("?" * len(NEWS_GAMES))
    rows = db.conn.execute(f"SELECT date FROM builds WHERE source = 'github' AND game IN ({marks}) AND date >= ?",
                           (*NEWS_GAMES, since)).fetchall()
    hours: Counter[int] = Counter()
    days_c: Counter[int] = Counter()
    for (d,) in rows:
        try:
            t = datetime.fromisoformat(str(d).replace("Z", "+00:00")).astimezone(ROME)
        except ValueError:
            continue
        hours[t.hour] += 1
        days_c[t.weekday()] += 1
    total = sum(hours.values())
    if not total:
        return {"n": 0}
    # la fascia più corta di ore consecutive (anche a cavallo della mezzanotte) che contiene metà degli arrivi
    best = None
    for start in range(24):
        acc = 0
        for length in range(1, 25):
            acc += hours[(start + length - 1) % 24]
            if acc * 2 >= total:
                if best is None or length < best[1]:
                    best = (start, length)
                break
    top = max(hours.values())
    return {"n": total, "ore": [{"ora": h, "n": hours[h], "forza": hours[h] / top} for h in range(24)],
            "giorni": [{"giorno": DAYS[d], "n": days_c[d]} for d in range(7)],
            "metà_da": best[0] if best else None, "metà_a": ((best[0] + best[1]) % 24) if best else None,
            "giorno_top": DAYS[max(range(7), key=lambda d: days_c[d])]}


def summary(db: Any, now: datetime | None = None) -> dict[str, Any] | None:
    pts = c.points(db)
    if len(pts) < 50:
        return None
    env = c.Envelope(pts)
    end = env.t[-1]
    now = now or datetime.now(timezone.utc)
    year = heatmap(env, end - timedelta(days=365), end + timedelta(seconds=1))
    recent = ratios(env, end - timedelta(days=90), end + timedelta(seconds=1))
    before = ratios(env, end - timedelta(days=455), end - timedelta(days=90))
    off = italian_offset(now)
    return {"orologio": year, "adesso": recent, "prima": before,
            "adesso_sera": compare(recent, before, "sera"), "adesso_weekend": compare(recent, before, "weekend"),
            "anno": ratios(env, end - timedelta(days=365), end + timedelta(seconds=1)),
            "trimestri": quarters(env, end + timedelta(seconds=1)), "alyx": alyx_check(env),
            "arrivi": arrivals(db, now), "scarto": off, "ultimo": end.isoformat(timespec="minutes"),
            "gap": GAP_HOURS, "min_ore": MIN_HOURS, "pochi_ore": FEW_HOURS}
