"""Rinomine: una riga con HLX che sparisce e ricompare, nello stesso commit e nello stesso file, con un altro prefisso.

Valve non toglie la tecnologia nata per HLX: la rinomina quando passa al motore comune o a un altro gioco.
Verificato il 2026-10-29 su sette nomi HLX "tolti" dal 2021, cinque esistono ancora con un altro nome:
  hlx.hrtf_basic -> core.hrtf_basic (CS2 e Deadlock, 2026-01-21; segnalato anche da Gabe Follower)
  hlx_physics_particle_spawner -> generic_physics_particle_spawner (tutti i giochi)
  r_hlx_fsr3_min_reactiveness -> r_citadel_fsr3_min_reactiveness (Deadlock, che nei file si chiama "citadel")
  hlx_2d_basic -> citadel_2d_basic (Deadlock)
  hlx_group_soundevent_cluster_default_3d_test -> group_soundevent_cluster_default_3d_test (prefisso tolto)
Senza questo controllo la rinomina risulta solo una rimozione. Con questo diventa una voce "renamed" che dice dove
è finita la tecnologia: nel motore comune ("core", "generic"), in un gioco ("citadel", "dota") o senza prefisso.
"""
from __future__ import annotations

import re
from typing import Iterable

HLX_PART = re.compile(r"hlx[_.\-]?", re.IGNORECASE)
HAS_HLX = re.compile(r"hlx", re.IGNORECASE)
MIN_REST = 6            # lettere e cifre fuori da HLX: "hlx" da solo combacerebbe con qualunque parola
MAX_LINE = 300


def _pattern(old: str) -> re.Pattern[str] | None:
    pieces = HLX_PART.split(old)
    if len(pieces) < 2 or sum(ch.isalnum() for ch in "".join(pieces)) < MIN_REST:
        return None
    # al posto di ogni "hlx" (con il suo separatore) un altro prefisso, oppure niente
    return re.compile("([A-Za-z0-9]+[_.\\-]?|)".join(re.escape(p) for p in pieces))


def find_renames(removed: Iterable[str], added: Iterable[str]) -> list[tuple[str, str, str]]:
    """(riga vecchia, riga nuova, prefisso nuovo) per ogni riga HLX tolta che ricompare con un altro prefisso."""
    candidates = [a.strip() for a in added if a.strip() and len(a.strip()) <= MAX_LINE and not HAS_HLX.search(a)]
    if not candidates:
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for line in removed:
        old = line.strip()
        if not old or len(old) > MAX_LINE or old in seen or not HAS_HLX.search(old):
            continue
        pat = _pattern(old)
        if pat is None:
            continue
        for new in candidates:
            m = pat.fullmatch(new)
            if m:
                seen.add(old)
                prefix = next((g for g in m.groups() if g), "")
                out.append((old, new, prefix.rstrip("_.-")))
                break
    return out
