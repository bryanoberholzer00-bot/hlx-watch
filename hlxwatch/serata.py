"""La serata di un evento (The Game Awards...): cosa mostra il sito prima, durante e dopo.

Le serate sono in serate.json, lo stesso file che usa il bot su Cloudflare (telegram-welcome/src/serata.js): è il bot a
controllare ogni 10 minuti e a riavviare i giri. Qui si decide solo cosa dire sul sito:
  - "prima": da FASCIA_PRIMA giorni prima una fascia in cima a ogni pagina con il giorno e l'ora italiana;
  - "in_corso": la fascia dice che HLX Watch controlla ogni 10 minuti (il sito si rigenera a ogni giro);
  - "dopo": per FASCIA_DOPO giorni la fascia dice che la serata è finita.
Il registro delle previsioni mostra sempre la prossima serata, anche quando la fascia non c'è ancora.
Le serate con "prova": true sono le prove generali del bot (pagella al proprietario su Telegram): il sito non le mostra.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("serate.json")
FASCIA_PRIMA = 7
FASCIA_DOPO = 2
GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
        "novembre", "dicembre")


def load(path: Path | None = None) -> list[dict[str, Any]]:
    try:
        return list(json.loads((path or PATH).read_text(encoding="utf-8")).get("serate") or [])
    except (OSError, ValueError):
        return []


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _local(value: datetime) -> datetime:
    from hlxwatch.timeutil import tz
    return value.astimezone(tz())


def _ora(d: datetime) -> str:
    return f"{d.hour}:{d.minute:02d}"


def _giorno(d: datetime) -> str:
    return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]}"


def current(now: datetime | None = None, serate: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """La prossima serata (o quella in corso, o quella finita da meno di FASCIA_DOPO giorni), pronta per il sito."""
    now = now or datetime.now(timezone.utc)
    for s in sorted(serate if serate is not None else load(), key=lambda x: x["inizio"]):
        if s.get("prova"):
            continue
        try:
            start, show, end = _dt(s["inizio"]), _dt(s["spettacolo"]), _dt(s["fine"])
        except (KeyError, ValueError):
            continue
        if now >= end + timedelta(days=FASCIA_DOPO):
            continue
        state = "prima" if now < start else "in_corso" if now < end else "dopo"
        ls, lshow, le = _local(start), _local(show), _local(end)
        days = (lshow.date() - _local(now).date()).days
        return {**s, "stato": state, "giorni": days,
                "fascia": state != "prima" or days <= FASCIA_PRIMA,
                # l'evento è la sera prima, in America: il giorno del calendario americano per il titolo
                "giorno_evento": _giorno(show.astimezone(timezone(timedelta(hours=-8)))),
                "spettacolo_locale": f"{_giorno(lshow)} alle {_ora(lshow)}",
                "inizio_locale": f"{_giorno(ls)} alle {_ora(ls)}", "fine_locale": f"{_giorno(le)} alle {_ora(le)}",
                "ora_spettacolo": _ora(lshow), "ora_inizio": _ora(ls), "ora_fine": _ora(le)}
    return None
