"""Tipo di prova di una voce e filtro dei dati personali.

Il punteggio serve a filtrare, ma non dice che tipo di prova si ha davanti. Qui si ricava dall'area del file (le
dieci aree in cui SteamTracking divide ogni aggiornamento) un'etichetta leggibile, con la stessa gerarchia che
SteamTracking impone ai suoi riassunti AI: dati di gioco e traduzioni, poi classi, variabili e protocolli, poi elenchi
di file, infine le sole stringhe dei binari ("le stringhe suggeriscono"). Il lessico resta neutro: mai "leak",
"in arrivo" o date dedotte, solo ciò che i dati mostrano.

`redact_personal` toglie dalle righe indirizzi email e nomi utente nei percorsi (C:\\Users\\nome, /home/nome): i dump
possono contenere dati di dipendenti, e HLX Watch non li salva né li pubblica.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ aree (come l'azione commit-summary di SteamTracking)
AREAS: list[tuple[str, str]] = [
    ("build", "build e depot"),
    ("convar", "variabili, comandi, rete ed entità"),
    ("schema", "classi del motore (schemi)"),
    ("data", "dati di gioco"),
    ("loc", "traduzioni"),
    ("ui", "interfaccia"),
    ("assets", "elenchi di file"),
    ("game_strings", "stringhe del gioco"),
    ("engine_strings", "stringhe del motore e degli strumenti"),
    ("news", "post ufficiali di Valve"),
    ("code", "codice pubblico di Valve"),
    ("web", "siti di Valve"),
    ("other", "altro"),
]
AREA_LABELS = dict(AREAS)
_AREA_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("build", re.compile(r"(^|/)steam\.inf$|^manifests/|(^|/)files\.json$|^depots/branches/|^pics/app/", re.I)),
    ("schema", re.compile(r"^DumpSource2/schemas/", re.I)),
    ("convar", re.compile(r"^DumpSource2/|^Protobufs/|\.proto$|\.fgd$", re.I)),
    ("loc", re.compile(r"/localization/|_english\.txt$|(^|/)resource/[^/]*_[a-z]+\.txt$", re.I)),
    ("ui", re.compile(r"/panorama/|/webui/|\.(vjs|vxml|vcss|js|css|html)$|^ClientExtracted/", re.I)),
    ("assets", re.compile(r"_dir\.txt$|asset_info|(^|/)files\.txt$", re.I)),
    ("engine_strings", re.compile(r"^game/bin/.*_strings\.txt$|^bin/.*_strings\.txt$|/tools/.*_strings\.txt$", re.I)),
    ("game_strings", re.compile(r"_strings\.txt$", re.I)),
    ("data", re.compile(r"\.(vdata|kv3|txt|vsndevts|vsndstck|vpulse|gameevents|vrsettings|vrdrivermanifest|vrfirmware|json|lua|cfg|gi)$", re.I)),
]
_TYPE_AREAS = {"build": "build", "new_app": "build", "new_repo": "build", "structure": "build", "pulse": "schema",
               "orphan": "convar", "summary": "other",
               "news": "news", "commit": "code", "appid": "code",
               "domain": "web"}


def area_of(path: str, change_type: str = "") -> str:
    """Area SteamTracking di una voce, dal suo tipo o dal percorso del file."""
    if change_type in _TYPE_AREAS:
        return _TYPE_AREAS[change_type]
    for key, rule in _AREA_RULES:
        if rule.search(path or ""):
            return key
    return "other"


# ------------------------------------------------------------------ tipo di prova
# (chiave, etichetta lunga per il sito, etichetta breve per le notifiche)
_EVIDENCE = {
    "data": ("dati", "dati di gioco o traduzioni", "dati di gioco"),
    "loc": ("dati", "dati di gioco o traduzioni", "traduzioni"),
    "ui": ("dati", "file dell'interfaccia", "interfaccia"),
    "schema": ("motore", "classi, variabili o protocolli del motore", "classi del motore"),
    "convar": ("motore", "classi, variabili o protocolli del motore", "motore"),
    "assets": ("elenco", "elenco di file: il file esiste, il contenuto non si vede", "elenco di file"),
    "game_strings": ("stringhe", "solo stringhe dei binari: le stringhe suggeriscono", "solo stringhe"),
    "engine_strings": ("stringhe", "solo stringhe dei binari: le stringhe suggeriscono", "solo stringhe"),
    "build": ("build", "build, depot o struttura dei repository", "build"),
    "news": ("ufficiale", "post ufficiale di Valve su Steam", "post ufficiale"),
    "code": ("codice", "codice pubblico di Valve su GitHub", "codice di Valve"),
    "web": ("siti", "domini nei certificati HTTPS dei siti di Valve", "siti di Valve"),
    "other": ("altro", "altro", ""),
}


def evidence_of(area: str) -> tuple[str, str, str]:
    return _EVIDENCE.get(area, _EVIDENCE["other"])


# ------------------------------------------------------------------ dati personali
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_HOME = re.compile(r"(?P<pre>[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+|/home/|/Users/)(?P<name>[^\\/\s\"'<>|:*?]+)",
                   re.IGNORECASE)
_NOT_PERSONAL = {"public", "default", "shared", "all users", "default user", "steam", "user", "username", "%s", "%username%",
                 "<user>", "buildbot", "builder", "runner"}


def redact_personal(text: str | None) -> str | None:
    """Email e nomi utente nei percorsi diventano segnaposto; il resto della riga resta com'è."""
    if not text:
        return text
    out = _EMAIL.sub("[email]", text)
    return _HOME.sub(lambda m: m.group(0) if m.group("name").lower() in _NOT_PERSONAL
                     else m.group("pre") + "[utente]", out)
