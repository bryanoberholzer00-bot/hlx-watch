"""Il contatore di Valve: quante modifiche al giorno salva Valve nel suo sistema interno (Perforce).

Ogni build di un gioco Source 2 porta nel file steam.inf il numero della modifica da cui è stata compilata
(SourceRevision) e la data e l'ora della compilazione (VersionDate, VersionTime, ora di Seattle). Il numero è unico
per tutta Valve: CS2, Dota 2 e Deadlock prendono numeri dalla stessa serie, che sale sempre (su 4.964 build dal
2016, solo 49 stanno sotto il massimo già visto, di poco: sono build di rami più vecchi). Quanto cresce tra una build
e l'altra dice quante modifiche ha salvato tutta Valve nel frattempo, su tutti i progetti, compresi quelli segreti.

Cosa si guarda:
  - il ritmo degli ultimi 60 giorni contro i 365 giorni prima: un'"ondata" quando supera del 30% (avviso, una volta);
  - le fasce orarie: nelle ore di lavoro di Seattle lavorano le persone, di notte e nel fine settimana soprattutto
    i programmi automatici. Se il salto è nelle ore di lavoro, è gente che lavora di più;
  - il lavoro che si vede: quanti aggiornamenti pubblici hanno avuto i progetti noti (CS2, Dota 2, Deadlock,
    SteamVR e quindi lo Steam Frame, Team Fortress 2, il client di Steam), contati dai commit di SteamTracking.
    Se il contatore cresce molto più di loro, la differenza è lavoro su qualcosa che non è uscito.

Cosa NON dice: su quale progetto lavora Valve. Il contatore misura il lavoro di tutti.
Lo storico dal 2016 è in contatore_seed.json (ricostruito da tutti i commit dei steam.inf, novembre 2026); a ogni
giro si leggono i tre steam.inf di oggi (tre file piccoli da raw.githubusercontent.com).
"""
from __future__ import annotations

import bisect
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

SEED = Path(__file__).with_name("contatore_seed.json")
GAMES = (("cs2", "game/csgo/steam.inf"), ("dota2", "game/dota/steam.inf"), ("deadlock", "game/citadel/steam.inf"))
GAME_TITLES = {"cs2": "Counter-Strike 2", "dota2": "Dota 2", "deadlock": "Deadlock", "underlords": "Dota Underlords",
               "artifact": "Artifact", "artifact_beta": "Artifact (beta)", "dota2_test": "Dota 2 Test"}
VISIBLE = (("cs2", "SteamTracking/GameTracking-CS2", "Counter-Strike 2"),
           ("dota2", "SteamTracking/GameTracking-Dota2", "Dota 2"),
           ("deadlock", "SteamTracking/GameTracking-Deadlock", "Deadlock"),
           ("steamvr", "SteamTracking/GameTracking-SteamVR", "SteamVR (anche lo Steam Frame)"),
           ("tf2", "SteamTracking/GameTracking-TF2", "Team Fortress 2"),
           ("steam", "SteamTracking/SteamTracking", "client di Steam"))
WINDOW = 60                  # giorni del ritmo "di adesso"
BASE = 365                   # giorni di confronto, subito prima
WAVE_ON, WAVE_OFF = 1.30, 1.15
FEW = 8                      # intervalli sotto i quali una fascia oraria ha "pochi dati"
MAX_GAP_HOURS = 16           # intervalli più lunghi tra due build non si assegnano a una fascia
EVENTS = [
    {"data": "2019-11-21", "testo": "annuncio di Half-Life: Alyx"},
    {"data": "2020-03-23", "testo": "uscita di Half-Life: Alyx"},
    {"data": "2021-07-15", "testo": "annuncio di Steam Deck"},
    {"data": "2023-03-22", "testo": "annuncio di Counter-Strike 2"},
    {"data": "2023-09-27", "testo": "uscita di Counter-Strike 2"},
    {"data": "2024-08-23", "testo": "Deadlock annunciato"},
    {"data": "2025-11-12", "testo": "annunciati Steam Machine, Steam Frame e il nuovo Steam Controller"},
]
STATE_WAVE = "contatore:ondata"
STATE_VISIBLE = "contatore:visibile"
TITLE_WAVE = "HLX Watch: il contatore di Valve corre"
TITLE_CALM = "HLX Watch: il contatore di Valve è tornato normale"
BUCKETS = ("lavoro", "sera", "weekend")
BUCKET_LABELS = {"lavoro": "ore di lavoro (lun-ven, 9-18)", "sera": "notte e sera (lun-ven)", "weekend": "fine settimana"}


def num(n: float | int) -> str:
    """Numero all'italiana: 11.209.448."""
    return f"{int(round(n)):,}".replace(",", ".")


def pct(ratio: float | None) -> str:
    if ratio is None:
        return "—"
    v = round((ratio - 1) * 100)
    return f"+{v}%" if v > 0 else f"{v}%"


# ------------------------------------------------------------------ dati
def parse_inf(text: str) -> tuple[int, str] | None:
    """(SourceRevision, ora della build ISO) da un steam.inf, None se mancano."""
    d = dict(re.findall(r"^(\w+)=(.*?)\s*$", text or "", re.M))
    rev = d.get("SourceRevision", "")
    if not rev.isdigit():
        return None
    try:
        when = datetime.strptime(f"{d.get('VersionDate', '')} {d.get('VersionTime', '')}", "%b %d %Y %H:%M:%S")
    except ValueError:
        return None
    return int(rev), when.strftime("%Y-%m-%dT%H:%M:%S")


def load_seed(db: Database) -> int:
    """Lo storico dal 2016 entra nell'archivio la prima volta (e se il file cresce). Restituisce le righe nuove."""
    try:
        seed = json.loads(SEED.read_text(encoding="utf-8"))["punti"]
    except (OSError, ValueError, KeyError):
        return 0
    have = db.conn.execute("SELECT COUNT(*) FROM valve_counter").fetchone()[0]
    if have >= len(seed):
        return 0
    before = db.conn.total_changes
    db.conn.executemany("INSERT OR IGNORE INTO valve_counter (game, rev, built_at) VALUES (?, ?, ?)", seed)
    db.conn.commit()
    return db.conn.total_changes - before


def add_point(db: Database, game: str, rev: int, built_at: str) -> bool:
    cur = db.conn.execute("INSERT OR IGNORE INTO valve_counter (game, rev, built_at, seen_at) VALUES (?, ?, ?, ?)",
                          (game, rev, built_at, iso(utcnow())))
    db.conn.commit()
    return cur.rowcount > 0


def points(db: Database) -> list[tuple[datetime, int, str]]:
    rows = db.conn.execute("SELECT built_at, rev, game FROM valve_counter").fetchall()
    return sorted((datetime.fromisoformat(r[0]), int(r[1]), r[2]) for r in rows)


def update(cfg: Any, db: Database, client: Any) -> int:
    """Legge i steam.inf di oggi di CS2, Dota 2 e Deadlock. Restituisce quante build nuove ha trovato."""
    from hlxwatch.repos import tracked_repos
    repos = {r["name"]: r for r in tracked_repos(cfg, db)}
    new = 0
    for game, path in GAMES:
        repo = repos.get(game)
        if not repo or not repo.get("repo"):
            continue
        text = client.raw_file(repo["repo"], repo.get("branch") or "master", path, 64 * 1024)
        parsed = parse_inf(text or "")
        if parsed and add_point(db, game, *parsed):
            new += 1
    return new


def visible_work(db: Database, client: Any, now: datetime | None = None, force: bool = False) -> dict[str, Any] | None:
    """Aggiornamenti pubblici dei progetti noti (commit dei repository SteamTracking) negli ultimi 60 giorni, nei 60
    prima e negli stessi 60 giorni di un anno fa. Una volta al giorno: 18 richieste piccole all'API di GitHub."""
    old = _json_state(db, STATE_VISIBLE)
    now = now or utcnow()
    if old and not force:
        at = parse_iso(old.get("at"))
        if at and now - at < timedelta(hours=20):
            return old
    windows = {"ora": (now - timedelta(days=WINDOW), now),
               "prima": (now - timedelta(days=2 * WINDOW), now - timedelta(days=WINDOW)),
               "anno": (now - timedelta(days=365 + WINDOW), now - timedelta(days=365))}
    repos = []
    for key, repo, title in VISIBLE:
        row: dict[str, Any] = {"key": key, "repo": repo, "titolo": title}
        for name, (a, b) in windows.items():
            row[name] = client.count_commits(repo, a.strftime("%Y-%m-%dT%H:%M:%SZ"), b.strftime("%Y-%m-%dT%H:%M:%SZ"))
        repos.append(row)
    ok = [r for r in repos if all(isinstance(r[n], int) for n in windows)]
    totals = {n: sum(r[n] for r in ok) for n in windows}
    data = {"at": iso(now), "repos": repos, "totale": totals,
            "rispetto_anno": totals["ora"] / totals["anno"] if totals["anno"] else None,
            "rispetto_prima": totals["ora"] / totals["prima"] if totals["prima"] else None}
    db.set_state(STATE_VISIBLE, json.dumps(data, ensure_ascii=False))
    return data


def _json_state(db: Database, key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(db.get_state(key) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------ analisi
class Envelope:
    """Il contatore nel tempo: per ogni istante il numero più alto già visto (le build di rami vecchi restano sotto)."""

    def __init__(self, pts: list[tuple[datetime, int, str]]):
        self.t: list[datetime] = []
        self.v: list[int] = []
        top = 0
        for t, rev, _ in pts:
            if rev > top:
                self.t.append(t)
                self.v.append(rev)
                top = rev

    def at(self, t: datetime) -> int | None:
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else None

    def rate(self, a: datetime, b: datetime) -> float | None:
        """Modifiche al giorno tra a e b (None se prima dell'inizio dei dati)."""
        if not self.t or a < self.t[0]:
            return None
        va, vb = self.at(a), self.at(b)
        days = (b - a).total_seconds() / 86400
        return (vb - va) / days if va is not None and vb is not None and days > 0 else None

    def interp(self, t: datetime) -> float | None:
        """Il contatore all'istante t, in linea retta tra la build prima e quella dopo: per periodi brevi, come una
        settimana di feste con poche build, è più giusto del gradino."""
        i = bisect.bisect_right(self.t, t)
        if i == 0 or i >= len(self.t):
            return None
        t0, t1, v0, v1 = self.t[i - 1], self.t[i], self.v[i - 1], self.v[i]
        return v0 + (v1 - v0) * (t - t0).total_seconds() / (t1 - t0).total_seconds()

    def rate_interp(self, a: datetime, b: datetime) -> float | None:
        va, vb = self.interp(a), self.interp(b)
        days = (b - a).total_seconds() / 86400
        return (vb - va) / days if va is not None and vb is not None and days > 0 else None

    def builds(self, a: datetime, b: datetime) -> int:
        return bisect.bisect_left(self.t, b) - bisect.bisect_left(self.t, a)


def bucket(t: datetime) -> str:
    if t.weekday() >= 5:
        return "weekend"
    return "lavoro" if 9 <= t.hour < 18 else "sera"


def hourly(env: Envelope, a: datetime, b: datetime) -> dict[str, dict[str, Any]]:
    """Modifiche all'ora per fascia oraria, dagli intervalli brevi tra due build consecutive nella stessa fascia."""
    agg = {k: [0, 0.0, 0] for k in BUCKETS}
    for i in range(1, len(env.t)):
        t1, t2 = env.t[i - 1], env.t[i]
        if t1 < a or t2 > b:
            continue
        hours = (t2 - t1).total_seconds() / 3600
        if hours <= 0 or hours > MAX_GAP_HOURS or bucket(t1) != bucket(t2):
            continue
        g = agg[bucket(t1 + (t2 - t1) / 2)]
        g[0] += env.v[i] - env.v[i - 1]
        g[1] += hours
        g[2] += 1
    return {k: {"ora": g[0] / g[1] if g[1] else None, "n": g[2], "pochi": g[2] < FEW} for k, g in agg.items()}


def waves(env: Envelope, step_days: int = 15, top: int = 6) -> list[dict[str, Any]]:
    """Le ondate più forti della storia: finestre di 60 giorni con il ritmo più alto rispetto all'anno prima,
    almeno 120 giorni l'una dall'altra."""
    if not env.t:
        return []
    out = []
    d = env.t[0] + timedelta(days=BASE)
    end = env.t[-1]
    while d + timedelta(days=WINDOW) <= end + timedelta(days=1):
        cur, base = env.rate(d, d + timedelta(days=WINDOW)), env.rate(d - timedelta(days=BASE), d)
        if cur and base:
            out.append({"da": d.date().isoformat(), "a": (d + timedelta(days=WINDOW)).date().isoformat(),
                        "ritmo": cur, "rapporto": cur / base})
        d += timedelta(days=step_days)
    picked: list[dict[str, Any]] = []
    for w in sorted(out, key=lambda w: -w["rapporto"]):
        if all(abs((datetime.fromisoformat(w["da"]) - datetime.fromisoformat(p["da"])).days) >= 120 for p in picked):
            picked.append(w)
        if len(picked) >= top:
            break
    return picked


# ------------------------------------------------------------------ la prova delle feste
FESTE = {"ringraziamento": "settimana del Ringraziamento", "natale": "Natale (22 dicembre - 1 gennaio)"}
FESTE_DAL = 2017
STATE_FESTE = "contatore:feste:"
ALYX_YEAR = 2019             # annuncio il 21 novembre 2019, uscita il 23 marzo 2020


def thanksgiving(year: int) -> date:
    """Il quarto giovedì di novembre."""
    d = date(year, 11, 1)
    return d + timedelta(days=(3 - d.weekday()) % 7 + 21)


def holiday_window(kind: str, year: int) -> tuple[datetime, datetime]:
    if kind == "ringraziamento":
        start = datetime.combine(thanksgiving(year) - timedelta(days=3), datetime.min.time())   # dal lunedì
        return start, start + timedelta(days=7)
    return datetime(year, 12, 22), datetime(year + 1, 1, 2)


def holidays(env: Envelope) -> dict[str, Any]:
    """Per ogni festa e anno: il ritmo nei giorni di festa diviso per quello delle 3 settimane prima.
    Valve a Natale quasi si ferma (di solito 13-25% del normale), anche nel 2019 prima di Alyx: un Natale senza
    calo sarebbe un segnale più forte di quelli di allora. Una festa è "completa" quando c'è una build dopo."""
    if not env.t:
        return {"righe": [], "riassunto": {}}
    last = env.t[-1]
    rows = []
    for year in range(FESTE_DAL, last.year + 1):
        for kind in FESTE:
            a, b = holiday_window(kind, year)
            if a > last:
                continue
            base = env.rate_interp(a - timedelta(days=21), a)
            if b <= last:
                state, now = "completa", env.rate_interp(a, b)
            else:                                     # in corso: fino all'ultima build
                state, now = "in_corso", env.rate_interp(a, last) if last > a + timedelta(days=1) else None
            rows.append({"festa": kind, "anno": year, "da": a.date().isoformat(), "a": (b - timedelta(days=1)).date().isoformat(),
                         "ritmo": now, "base": base, "rapporto": now / base if now is not None and base else None,
                         "build": env.builds(a, min(b, last + timedelta(seconds=1))), "stato": state})
    # la stagione di adesso è l'anno dell'ultima festa cominciata: il Natale 2026 si giudica anche a gennaio 2027
    season = max((r["anno"] for r in rows), default=last.year)
    summary = {}
    for kind in FESTE:
        past = sorted(r["rapporto"] for r in rows if r["festa"] == kind and r["stato"] == "completa"
                      and r["rapporto"] is not None and r["anno"] < season)
        if past:
            summary[kind] = {"min": past[0], "max": past[-1], "mediana": past[len(past) // 2], "anni": len(past),
                             "alyx": next((r["rapporto"] for r in rows if r["festa"] == kind and r["anno"] == ALYX_YEAR), None)}
    for r in rows:
        s = summary.get(r["festa"])
        if s and r["rapporto"] is not None and r["anno"] >= season:
            r["giudizio"] = ("mai così tanto lavoro" if r["rapporto"] > s["max"] else
                             "più del solito" if r["rapporto"] > (s["mediana"] + s["max"]) / 2 else
                             "come al solito" if r["rapporto"] >= s["min"] else "meno del solito")
    return {"righe": rows, "riassunto": summary}


def holiday_text(r: dict[str, Any], s: dict[str, Any], site_url: str) -> str:
    alyx = f" Nel {ALYX_YEAR}, prima di Alyx: {round(s['alyx'] * 100)}%." if s.get("alyx") else ""
    return (f"{FESTE[r['festa']].capitalize()} {r['anno']}: Valve ha lavorato al {round(r['rapporto'] * 100)}% del "
            f"ritmo delle tre settimane prima ({num(r['ritmo'])} modifiche al giorno, {r['build']} build).\n"
            f"Gli anni scorsi: dal {round(s['min'] * 100)}% al {round(s['max'] * 100)}%, di solito "
            f"{round(s['mediana'] * 100)}%.{alyx}\nGiudizio: {r.get('giudizio', '—')}."
            + ("\nUna festa senza il solito calo vuol dire di solito una scadenza vicina, ma non dice quale progetto."
               if r.get("giudizio") in ("più del solito", "mai così tanto lavoro") else "")
            + f"\n{site_url}contatore.html#feste")


def check_holidays(db: Database, h: dict[str, Any], notifiers: list, site_url: str) -> list[str]:
    """Un messaggio per festa, quando finisce (la prima build dopo), solo per le feste finite da meno di 14 giorni."""
    from hlxwatch.notifiers import notify_text
    sent = []
    for r in h["righe"]:
        s = h["riassunto"].get(r["festa"])
        if r["stato"] != "completa" or r["rapporto"] is None or not s or "giudizio" not in r:
            continue
        if datetime.now() - datetime.fromisoformat(r["a"]) > timedelta(days=14):
            continue
        key = f"{STATE_FESTE}{r['festa']}:{r['anno']}"
        if db.get_state(key):
            continue
        notify_text(notifiers, f"HLX Watch: la prova delle feste ({FESTE[r['festa']].split(' (')[0]} {r['anno']})",
                    holiday_text(r, s, site_url))
        db.set_state(key, json.dumps({"rapporto": r["rapporto"], "giudizio": r["giudizio"], "at": iso(utcnow())}))
        sent.append(key)
    return sent


def monthly(env: Envelope) -> list[dict[str, Any]]:
    """Modifiche al giorno mese per mese (per il grafico), dal 2017: nel 2016 c'erano solo poche build di Dota 2 e
    Artifact, e il primo salto del contatore non è un ritmo."""
    if not env.t:
        return []
    out = []
    d = max(datetime(2017, 1, 1), (env.t[0].replace(day=1) + timedelta(days=32)).replace(day=1))
    while d < env.t[-1]:
        nxt = (d + timedelta(days=32)).replace(day=1)
        end = min(nxt, env.t[-1])
        r = env.rate(d, end)
        if r is not None and (end - d).days >= 20:
            out.append({"mese": d.strftime("%Y-%m"), "ritmo": round(r)})
        d = nxt
    return out


def analyse(db: Database, visible: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Tutto quello che il sito e l'avviso mostrano. None se l'archivio è vuoto."""
    pts = points(db)
    env = Envelope(pts)
    if len(env.t) < 10:
        return None
    end = env.t[-1]
    w0 = end - timedelta(days=WINDOW)
    cur = env.rate(w0, end)
    base = env.rate(w0 - timedelta(days=BASE), w0)
    last_year = env.rate(w0 - timedelta(days=365), end - timedelta(days=365))
    prev = env.rate(w0 - timedelta(days=WINDOW), w0)
    history = waves(env, top=40)
    best_before = max((w["ritmo"] for w in history if w["a"] < w0.date().isoformat()), default=0)
    per_game = []
    for game in ("cs2", "dota2", "deadlock"):
        g = Envelope([p for p in pts if p[2] == game])
        a, b = g.rate(w0, end), g.rate(w0 - timedelta(days=BASE), w0)
        if a and b:
            per_game.append({"game": game, "titolo": GAME_TITLES[game], "ora": a, "prima": b, "rapporto": a / b})
    hours_now = hourly(env, w0, end)
    hours_before = hourly(env, w0 - timedelta(days=BASE), w0)
    for k in BUCKETS:
        a, b = hours_now[k]["ora"], hours_before[k]["ora"]
        hours_now[k]["rapporto"] = a / b if a and b else None
    visible = visible or _json_state(db, STATE_VISIBLE)
    return {
        "ultimo": {"rev": env.v[-1], "quando": end.isoformat(timespec="minutes"),
                   "gioco": next((p[2] for p in reversed(pts) if p[1] == env.v[-1]), "")},
        "n_build": len(pts), "dal": pts[0][0].date().isoformat(),
        "ora": cur, "base": base, "anno_scorso": last_year, "prima": prev,
        "rapporto": cur / base if cur and base else None,
        "rapporto_anno": cur / last_year if cur and last_year else None,
        "rapporto_prima": cur / prev if cur and prev else None,
        "record": bool(cur and cur > best_before), "record_prima": best_before,
        "finestra": {"da": w0.date().isoformat(), "a": end.date().isoformat()},
        "fasce": hours_now, "fasce_prima": hours_before, "giochi": per_game,
        "ondate": waves(env), "mesi": monthly(env), "visibile": visible, "eventi": EVENTS,
        "feste": holidays(env),
    }


# ------------------------------------------------------------------ grafico
def chart_svg(a: dict[str, Any] | None, hlx_days: list[str] | None = None) -> str:
    """Barre mese per mese (modifiche al giorno), linee dei fatti noti e tacche dei giorni delle tracce HLX."""
    import html
    months = (a or {}).get("mesi") or []
    if not months:
        return ""
    w, ht, left, right, top, bottom = 1000, 300, 46, 10, 16, 34
    n = len(months)
    bw = (w - left - right) / n
    vmax = max(m["ritmo"] for m in months) * 1.08
    step = 1000

    def y(v: float) -> float:
        return top + (1 - v / vmax) * (ht - top - bottom)

    def x_day(day: str) -> float | None:
        key = day[:7]
        idx = next((i for i, m in enumerate(months) if m["mese"] == key), None)
        if idx is None:
            return None
        frac = (int(day[8:10]) - 1) / 31 if len(day) >= 10 else 0.5
        return left + (idx + frac) * bw

    win = (a or {}).get("finestra") or {}
    parts = [f'<svg viewBox="0 0 {w} {ht}" class="contatore" role="img" '
             'aria-label="Modifiche salvate da Valve al giorno, mese per mese">']
    v = 0
    while v <= vmax:
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="currentColor" '
                     f'opacity="0.13"/><text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="currentColor" opacity="0.6">{num(v)}</text>')
        v += step
    for i, m in enumerate(months):
        x0 = left + i * bw
        hot = bool(win) and m["mese"] >= win.get("da", "9")[:7]
        parts.append(f'<rect x="{x0 + 0.6:.1f}" y="{y(m["ritmo"]):.1f}" width="{max(bw - 1.2, 1):.1f}" '
                     f'height="{y(0) - y(m["ritmo"]):.1f}" fill="{"var(--accent)" if hot else "#7a8699"}" '
                     f'opacity="{0.95 if hot else 0.55}"><title>{m["mese"]}: {num(m["ritmo"])} al giorno</title></rect>')
        if m["mese"].endswith("-01"):
            parts.append(f'<text x="{x0 + 2:.1f}" y="{ht - 12}" font-size="12" fill="currentColor" '
                         f'opacity="0.7">{m["mese"][:4]}</text>')
    for e in (a or {}).get("eventi") or []:
        ex = x_day(e["data"])
        if ex is not None:
            parts.append(f'<line x1="{ex:.1f}" x2="{ex:.1f}" y1="{top}" y2="{y(0):.1f}" stroke="#2f80ed" '
                         f'stroke-dasharray="4 3" stroke-width="1.3"><title>{e["data"]}: '
                         f'{html.escape(e["testo"])}</title></line>')
    for day in sorted(set(hlx_days or [])):
        ex = x_day(day)
        if ex is not None:
            parts.append(f'<rect x="{ex - 1:.1f}" y="{y(0) + 2:.1f}" width="2" height="7" fill="#e53935">'
                         f'<title>{day}: traccia HLX</title></rect>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ avviso
def wave_text(a: dict[str, Any], site_url: str) -> str:
    lines = [f"Negli ultimi {WINDOW} giorni Valve ha salvato in media {num(a['ora'])} modifiche al giorno nel suo "
             f"sistema interno: {pct(a['rapporto'])} rispetto all'anno prima ({num(a['base'])} al giorno)."]
    if a["record"]:
        lines.append("È il ritmo più alto da quando ci sono i dati (2016).")
    f = a["fasce"]
    if f["lavoro"].get("rapporto"):
        tail = "" if f["lavoro"]["pochi"] else " (dati solidi)"
        lines.append(f"Nelle ore di lavoro di Seattle: {pct(f['lavoro']['rapporto'])}{tail}: sono soprattutto persone, "
                     "non programmi automatici.")
    off = [f"{BUCKET_LABELS[k]} {pct(f[k]['rapporto'])}" for k in ("sera", "weekend")
           if f[k].get("rapporto") and not f[k]["pochi"]]
    if off:
        lines.append("Fuori orario: " + ", ".join(off) + ".")
    else:
        lines.append("Notti e fine settimana: ancora pochi dati per dire se si lavora anche fuori orario.")
    vis = a.get("visibile") or {}
    if vis.get("rispetto_anno") and a.get("rapporto_anno"):
        gap = a["rapporto_anno"] > vis["rispetto_anno"] * 1.1
        lines.append(f"Rispetto agli stessi giorni di un anno fa: contatore {pct(a['rapporto_anno'])}, aggiornamenti "
                     f"pubblici dei progetti noti (CS2, Dota 2, Deadlock, SteamVR, TF2, Steam) "
                     f"{pct(vis['rispetto_anno'])}." + (" La differenza è lavoro che non si vede." if gap else ""))
    lines.append("Il contatore misura il lavoro di tutta Valve: dice quanto, non su cosa.")
    lines.append(f"{site_url}contatore.html")
    return "\n".join(lines)


def check_wave(db: Database, a: dict[str, Any] | None, notifiers: list, site_url: str) -> str | None:
    """Avvisa una volta quando parte un'ondata (+30% sull'anno prima) e una volta quando finisce (sotto +15%).
    Restituisce 'inizio', 'fine' o None."""
    if not a or a.get("rapporto") is None:
        return None
    from hlxwatch.notifiers import notify_text
    state = _json_state(db, STATE_WAVE) or {}
    fresh = (datetime.fromisoformat(a["ultimo"]["quando"]) > datetime.now() - timedelta(days=7))
    if not state.get("attiva") and a["rapporto"] >= WAVE_ON and fresh:
        notify_text(notifiers, TITLE_WAVE, wave_text(a, site_url))
        db.set_state(STATE_WAVE, json.dumps({"attiva": True, "da": a["finestra"]["a"], "rapporto": a["rapporto"],
                                             "ritmo": a["ora"], "at": iso(utcnow())}))
        return "inizio"
    if state.get("attiva") and a["rapporto"] < WAVE_OFF:
        notify_text(notifiers, TITLE_CALM,
                    f"Il ritmo delle modifiche di Valve è sceso a {num(a['ora'])} al giorno ({pct(a['rapporto'])} "
                    f"rispetto all'anno prima). L'ondata iniziata il {state.get('da')} è finita.\n{site_url}contatore.html")
        db.set_state(STATE_WAVE, json.dumps({"attiva": False, "finita": a["finestra"]["a"], "da": state.get("da"),
                                             "at": iso(utcnow())}))
        return "fine"
    return None


def run(cfg: Any, db: Database, notifiers: list, client: Any = None, send: bool = True) -> dict[str, Any] | None:
    """Un giro: storico, build di oggi, lavoro visibile (una volta al giorno), analisi e avviso sulle ondate."""
    if not (cfg.get("contatore", {}) or {}).get("enabled", True):
        return None
    load_seed(db)
    if client is None:
        from hlxwatch.sources.github_tracking import GitHubClient
        client = GitHubClient(cfg.get("github.api_url", "https://api.github.com"),
                              cfg.get("github.raw_url", "https://raw.githubusercontent.com"), cfg.github_token)
    new = update(cfg, db, client)
    try:
        visible = visible_work(db, client)
    except Exception as exc:                       # noqa: BLE001 - il contatore funziona anche senza
        log.warning("Contatore di Valve: lavoro visibile non contato (%s)", exc)
        visible = None
    a = analyse(db, visible)
    if a:
        log.info("Contatore di Valve: %s (build nuove: %d), %s modifiche al giorno negli ultimi %d giorni, %s "
                 "sull'anno prima", num(a["ultimo"]["rev"]), new, num(a["ora"] or 0), WINDOW, pct(a["rapporto"]))
        site = str(cfg.get("general.site_url", "https://bryanoberholzer00-bot.github.io/hlx-watch/"))
        if send and notifiers:            # senza invio lo stato dell'ondata non cambia: l'avviso partirà al giro vero
            site = site if site.endswith("/") else site + "/"
            check_wave(db, a, notifiers, site)
            check_holidays(db, a["feste"], notifiers, site)
    return a
