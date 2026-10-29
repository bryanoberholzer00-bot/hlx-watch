"""Famiglie di token nei file di localizzazione: il posto in cui Valve è obbligata a scrivere il nome.

Ogni testo a schermo di un gioco Source 2 è un token `"Famiglia_Nome" "testo"` nei file `*_english.txt`
(le altre lingue ripetono gli stessi token). La famiglia è quasi sempre il nome del progetto o del suo
sistema: `CSGO_`, `SFUI_`, `DOTA_`, `Citadel_`, `HLVR_`. Un gioco nuovo porta con sé una famiglia nuova fin
dalla prima stringa di menu, e di solito un file nuovo.

- la prima scansione completa impara le famiglie presenti in ogni repository (stato `locprefix:<gioco>`,
  famiglia -> numero di token), senza avvisi;
- poi un file `*_english.txt` nuovo è una voce "localization" alta, e una famiglia che per la prima volta
  arriva ad almeno `min_tokens` token (sommando i commit, o nella scansione completa) una voce media, una volta sola.
In Deadlock ogni eroe nuovo porta circa 1900 token col suo nome: il repository può abbassare il punteggio
con `loc_prefix_score` (sul sito e nel report, senza notifica).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Iterable

from hlxwatch.config import Config
from hlxwatch.models import Finding
from hlxwatch.storage import Database

log = logging.getLogger(__name__)

LOC_FILE = re.compile(r"_english\.txt$", re.IGNORECASE)
TOKEN_RE = re.compile(r'^\s*"(?P<prefix>[A-Za-z][A-Za-z0-9]*)_[^"\n]*"\s+"')
# regex estesa per `git grep -o` della scansione completa: la parte iniziale della riga fino al secondo apice
GREP_PATTERN = r'^[[:space:]]*"[A-Za-z][A-Za-z0-9]*_[^"]*"[[:space:]]+"'
STATE_PREFIX = "locprefix:"


def is_loc_file(path: str) -> bool:
    return bool(LOC_FILE.search(path or ""))


def prefix_counts(lines: Iterable[str]) -> Counter[str]:
    """famiglia (minuscola) -> numero di token nelle righe."""
    counts: Counter[str] = Counter()
    for line in lines:
        m = TOKEN_RE.match(line)
        if m:
            counts[m.group("prefix").lower()] += 1
    return counts


class LocalizationTracker:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        lcfg: dict[str, Any] = cfg.get("localization", {}) or {}
        self.enabled = bool(lcfg.get("enabled", True))
        self.min_tokens = int(lcfg.get("min_tokens", 20))
        self.score_new_file = int(lcfg.get("score_new_file", 100))
        self.score_new_prefix = int(lcfg.get("score_new_prefix", 60))

    # ------------------------------------------------------------- stato
    def known(self, game: str) -> dict[str, int] | None:
        """famiglie imparate per il gioco; None finché la prima scansione completa non le ha lette."""
        raw = self.db.get_state(f"{STATE_PREFIX}{game}")
        if raw is None:
            return None
        try:
            return {str(k): int(v) for k, v in json.loads(raw).items()}
        except (ValueError, AttributeError):
            return {}

    def _save(self, game: str, counts: dict[str, int]) -> None:
        self.db.set_state(f"{STATE_PREFIX}{game}", json.dumps(dict(sorted(counts.items())), ensure_ascii=False))

    # ----------------------------------------------------------- scansione
    def learn(self, game: str, counts: dict[str, int], *, score_override: int | None = None) -> list[Finding]:
        """Scansione completa: la prima volta impara e basta; poi segnala le famiglie nuove e aggiorna."""
        if not self.enabled:
            return []
        known = self.known(game)
        if known is None:
            self._save(game, counts)
            log.info("[%s] localizzazione: %d famiglie di token imparate (%d token), senza avvisi",
                     game, len(counts), sum(counts.values()))
            return []
        out: list[Finding] = []
        for prefix, n in sorted(counts.items()):
            # "mai vista" = mai arrivata alla soglia: le famiglie piccole restano in attesa finché non crescono
            if known.get(prefix, 0) < self.min_tokens and n >= self.min_tokens:
                out.append(self._prefix_finding(game, prefix, n, "", "present", score_override))
        merged = dict(known)
        merged.update(counts)          # una famiglia sparita resta nota: non va segnalata se ricompare
        if merged != known:
            self._save(game, merged)
        return out

    # -------------------------------------------------------------- commit
    def check_file(self, game: str, path: str, added_lines: Iterable[str], *, file_added: bool = False,
                   score_override: int | None = None) -> list[Finding]:
        """Righe aggiunte a un file *_english.txt in un commit: file nuovo e famiglie mai viste."""
        if not self.enabled or not is_loc_file(path):
            return []
        known = self.known(game)
        if known is None:
            return []                  # le famiglie non sono ancora state imparate: lo farà la scansione completa
        counts = prefix_counts(added_lines)
        out: list[Finding] = []
        base = path.rsplit("/", 1)[-1]
        if file_added:
            top = ", ".join(f"{p}_ ({n})" for p, n in counts.most_common(3)) or "nessun token"
            out.append(Finding(
                game=game, source="github", file_path=path, change_type="localization",
                text=f"nuovo file di localizzazione {base}: famiglie di token {top}",
                score=self.score_new_file, keywords=["loc_file_new"], token=f"locfile:{game}:{path.lower()}",
                context=f"{sum(counts.values())} token in tutto",
            ))
        merged = dict(known)
        for prefix, n in sorted(counts.items()):
            before = merged.get(prefix, 0)
            merged[prefix] = before + n
            # i token si sommano tra i commit: la famiglia viene segnalata la prima volta che supera la soglia
            if before < self.min_tokens <= before + n:
                out.append(self._prefix_finding(game, prefix, before + n, path, "added", score_override))
        if counts:
            self._save(game, merged)
        return out

    def _prefix_finding(self, game: str, prefix: str, n: int, path: str, change_type: str,
                        score_override: int | None) -> Finding:
        base = path.rsplit("/", 1)[-1] if path else "scansione completa"
        log.warning("[%s] FAMIGLIA DI TOKEN MAI VISTA: %s_* (%d token, %s)", game, prefix, n, base)
        return Finding(
            game=game, source="github", file_path=path, change_type="localization",
            text=f"famiglia di token di localizzazione mai vista: {prefix}_* ({n} testi, {base})",
            score=int(score_override) if score_override is not None else self.score_new_prefix,
            keywords=["loc_prefix_new"], token=f"locprefix:{game}:{prefix}",
            context=f"famiglie note per {game}: {len(self.known(game) or {})}",
        )
