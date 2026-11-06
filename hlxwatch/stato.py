"""Stato di HLX Watch: il giro su GitHub controlla che il bot su Cloudflare sia vivo.

Il bot avvia un giro ogni 30 minuti e segnala i giri bloccati, falliti o fermi (telegram-welcome/src/giri.js). Ma se
si ferma il bot, si fermano anche i suoi avvisi: restano solo i run pianificati di GitHub, che partono quando GitHub ha
posto (ogni 4-5 ore). Questi run, a ogni giro, leggono lo stato del bot (/fast/stato.json) e avvisano il proprietario
se non risponde o se il suo controllo veloce non gira da più di LIMIT_MIN minuti. Due letture brutte di fila prima
dell'avviso, perché un errore di rete passeggero non diventi un messaggio; un altro avviso quando il bot riparte.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

LIMIT_MIN = 150          # il bot segna il battito ogni 55 minuti: oltre due ore e mezza è fermo
KEY, COUNT_KEY = "bot:fermo", "bot:letture_brutte"
TITLE = "HLX Watch: il bot su Cloudflare"


def bot_problem(fast: dict[str, Any] | None) -> str | None:
    if fast is None:                         # controllo veloce non configurato: niente da sorvegliare
        return None
    if fast.get("irraggiungibile"):
        return "non risponde"
    minutes = fast.get("minuti")
    if minutes is None:
        return "non ha mai segnato un giro riuscito"
    if minutes > LIMIT_MIN:
        return f"non segna un giro riuscito da {minutes // 60} ore e {minutes % 60} minuti"
    return None


def watch_bot(db: Any, notifiers: list, fast: dict[str, Any] | None) -> str | None:
    """Ritorna il problema attuale (o None) e manda al più un avviso per guasto."""
    from hlxwatch.notifiers import notify_text
    from hlxwatch.storage.db import iso, utcnow
    problem = bot_problem(fast)
    warned = db.get_state(KEY) or ""
    if not problem:
        if db.get_state(COUNT_KEY):
            db.set_state(COUNT_KEY, "")
        if warned:
            db.set_state(KEY, "")
            notify_text(notifiers, TITLE, "✅ Il bot su Cloudflare è ripartito: avvia di nuovo un giro ogni 30 minuti e "
                                          "sorveglia i giri su GitHub.")
            log.info("Bot su Cloudflare ripartito")
        return None
    count = int(db.get_state(COUNT_KEY) or 0) + 1
    db.set_state(COUNT_KEY, count)
    log.warning("Bot su Cloudflare: %s (lettura %d)", problem, count)
    if count >= 2 and not warned:
        db.set_state(KEY, iso(utcnow()))
        notify_text(notifiers, TITLE,
                    f"⚠️ Il bot su Cloudflare {problem}.\nFinché resta fermo non parte un giro ogni 30 minuti (restano "
                    "solo i run pianificati di GitHub, ogni 4-5 ore), non arrivano gli avvisi veloci dell'annuncio e "
                    "nessuno sorveglia i giri. I log sono nella dashboard di Cloudflare, alla voce del Worker "
                    "hlx-watch-bot.")
    return problem
