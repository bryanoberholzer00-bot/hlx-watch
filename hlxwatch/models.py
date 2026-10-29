from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Voci che non nascono da una parola chiave: la rivalutazione dell'archivio (reevaluate.py) non le tocca.
# Ogni nuovo tipo di voce senza parole chiave va aggiunto qui, non nelle query.
NON_KEYWORD_TYPES = ("build", "new_app", "new_repo", "codename", "structure")
NON_KEYWORD_KEYWORDS = ("watched_file", "codename_new", "steam_build", "new_repo", "private_app",
                        "structure_new", "structure_removed")


@dataclass
class BuildInfo:
    """Una 'versione' osservata: commit GameTracking, build Steam o snapshot locale."""

    game: str
    source: str            # github | steam | local
    ref: str               # sha del commit, buildid, hash dello snapshot
    date: datetime
    url: str | None = None
    message: str | None = None
    steam_buildid: str | None = None
    files_changed: int | None = None
    id: int | None = None


@dataclass
class Finding:
    """Una stringa / percorso / variabile interessante trovata in una modifica."""

    game: str
    source: str            # github | steam_pics | steam_apps | local
    file_path: str
    change_type: str       # added | removed | changed | file_added | file_removed | file_renamed | build | new_app | baseline
    text: str
    score: int
    keywords: list[str] = field(default_factory=list)
    token: str | None = None
    line_no: int | None = None
    context: str = ""
    old_text: str | None = None
    siblings: list[str] = field(default_factory=list)   # altri file con lo stesso token nello stesso commit (notifiche)
    # valorizzati dallo storage
    id: int | None = None
    build_id: int | None = None
    is_new: bool = False
    build: BuildInfo | None = None

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Chiave di deduplica: il token (es. hlx_fsr3_min_reactiveness) se c'è, altrimenti la riga normalizzata."""
        if self.token:
            return ("token", self.token.lower())
        return ("line", " ".join(self.text.split()).lower())

    @property
    def severity(self) -> str:
        if self.score >= 100:
            return "high"
        if self.score >= 50:
            return "medium"
        return "low"
