"""Riconoscimento di contesti speciali: entità FGD, ConVar, percorsi."""
from __future__ import annotations

import re

# @PointClass base(Targetname) = npc_hlx_soldier : "..."
FGD_CLASS_RE = re.compile(r"^\s*@([A-Za-z]+Class)\b[^=]*=\s*([A-Za-z0-9_]+)", re.IGNORECASE)
# nome_convar "valore" oppure nome_convar : valore oppure nome_convar = valore
CONVAR_LINE_RE = re.compile(r'^\s*"?([a-z][a-z0-9]*_[a-z0-9_]+)"?\s*(?:[:=]|\s+"|\s+-?\d)', re.IGNORECASE)
CONVAR_PATH_RE = re.compile(r"(convar|cvar|concommand|commands?\.txt|cvarlist)", re.IGNORECASE)
PATH_RE = re.compile(r"^[A-Za-z0-9_.\-]+([/\\][A-Za-z0-9_.\-]+)+$")


def fgd_entity_name(line: str) -> str | None:
    m = FGD_CLASS_RE.match(line)
    return m.group(2) if m else None


def convar_bonus(file_path: str, line: str) -> bool:
    if CONVAR_PATH_RE.search(file_path or ""):
        return True
    return bool(CONVAR_LINE_RE.match(line))


def is_path_like(token: str) -> bool:
    return bool(PATH_RE.match(token))
