"""Analisi dei diff: parsing di patch unificate, diff per insiemi di righe, produzione dei Finding."""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass

from hlxwatch.analyzers.keywords import KeywordMatcher
from hlxwatch.models import Finding

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_PAIRING_PRODUCT = 40_000   # oltre questo le coppie -/+ non vengono cercate per similarità


@dataclass
class DiffLine:
    op: str            # ' ', '+', '-'
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Change:
    change_type: str   # added | removed | changed
    text: str
    old_text: str | None
    line_no: int | None
    context: str


def parse_patch(patch: str) -> list[list[DiffLine]]:
    hunks: list[list[DiffLine]] = []
    current: list[DiffLine] | None = None
    old = new = 0
    for raw in patch.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            old, new = int(m.group(1)), int(m.group(3))
            current = []
            hunks.append(current)
            continue
        if current is None or raw.startswith("\\"):
            continue
        op = raw[:1] if raw[:1] in "+- " else " "
        text = raw[1:] if raw[:1] in "+- " else raw
        if op == "+":
            current.append(DiffLine("+", None, new, text))
            new += 1
        elif op == "-":
            current.append(DiffLine("-", old, None, text))
            old += 1
        else:
            current.append(DiffLine(" ", old, new, text))
            old += 1
            new += 1
    return hunks


def _context(lines: list[DiffLine], idx: int, n: int) -> str:
    lo, hi = max(0, idx - n), min(len(lines), idx + n + 1)
    return "\n".join(f"{ln.op}{ln.text}" for ln in lines[lo:hi])


def hunk_changes(hunk: list[DiffLine], context_lines: int = 2, similarity: float = 0.6) -> list[Change]:
    """Converte un hunk in modifiche atomiche, accoppiando righe -/+ simili come 'changed'."""
    changes: list[Change] = []
    i, n = 0, len(hunk)
    while i < n:
        if hunk[i].op == " ":
            i += 1
            continue
        minus: list[int] = []
        plus: list[int] = []
        j = i
        while j < n and hunk[j].op == "-":
            minus.append(j)
            j += 1
        while j < n and hunk[j].op == "+":
            plus.append(j)
            j += 1

        used_plus: set[int] = set()
        pair_by_similarity = minus and plus and len(minus) * len(plus) <= MAX_PAIRING_PRODUCT
        for k, mi in enumerate(minus):
            best, best_ratio = None, 0.0
            if pair_by_similarity:
                for pi in plus:
                    if pi in used_plus:
                        continue
                    sm = difflib.SequenceMatcher(None, hunk[mi].text, hunk[pi].text)
                    if sm.real_quick_ratio() < similarity or sm.quick_ratio() < similarity:
                        continue
                    ratio = sm.ratio()
                    if ratio > best_ratio:
                        best, best_ratio = pi, ratio
            elif plus and len(minus) == len(plus):
                best, best_ratio = plus[k], 1.0  # accoppiamento posizionale per hunk enormi
            if best is not None and best_ratio >= similarity:
                used_plus.add(best)
                changes.append(Change("changed", hunk[best].text, hunk[mi].text, hunk[best].new_no,
                                      _context(hunk, best, context_lines)))
            else:
                changes.append(Change("removed", hunk[mi].text, None, hunk[mi].old_no,
                                      _context(hunk, mi, context_lines)))
        for pi in plus:
            if pi not in used_plus:
                changes.append(Change("added", hunk[pi].text, None, hunk[pi].new_no,
                                      _context(hunk, pi, context_lines)))
        i = j
    return changes


def diff_by_sets(old_lines: list[str], new_lines: list[str], context_lines: int = 2) -> list[Change]:
    """Diff veloce e insensibile all'ordine (multinsieme di righe): adatto a liste di file ed elenchi di stringhe."""
    old_count = Counter(old_lines)
    new_count = Counter(new_lines)
    changes: list[Change] = []
    for idx, line in enumerate(new_lines):
        if new_count[line] > old_count.get(line, 0):
            new_count[line] -= 1
            lo, hi = max(0, idx - context_lines), min(len(new_lines), idx + context_lines + 1)
            ctx = "\n".join(("+" if j == idx else " ") + new_lines[j] for j in range(lo, hi))
            changes.append(Change("added", line, None, idx + 1, ctx))
    old_count = Counter(old_lines)
    new_total = Counter(new_lines)
    for idx, line in enumerate(old_lines):
        if old_count[line] > new_total.get(line, 0):
            old_count[line] -= 1
            lo, hi = max(0, idx - context_lines), min(len(old_lines), idx + context_lines + 1)
            ctx = "\n".join(("-" if j == idx else " ") + old_lines[j] for j in range(lo, hi))
            changes.append(Change("removed", line, None, idx + 1, ctx))
    return changes


def changes_to_findings(
    changes: list[Change],
    *,
    game: str,
    source: str,
    file_path: str,
    matcher: KeywordMatcher,
    max_findings: int = 400,
) -> list[Finding]:
    findings: list[Finding] = []
    for ch in changes:
        res = matcher.analyze_line(ch.text, file_path=file_path, change_type=ch.change_type)
        text, change_type = ch.text, ch.change_type
        if res is None and ch.change_type == "changed" and ch.old_text:
            # la parola chiave era nella riga vecchia ed è sparita: è una rimozione
            res = matcher.analyze_line(ch.old_text, file_path=file_path, change_type="removed")
            text, change_type = ch.old_text, "removed"
        if res is None:
            continue
        findings.append(Finding(
            game=game, source=source, file_path=file_path, change_type=change_type, text=text.strip(),
            score=res.score, keywords=res.keywords, token=res.token, line_no=ch.line_no,
            context=ch.context, old_text=ch.old_text if change_type == "changed" else None,
        ))
    if len(findings) > max_findings:
        findings.sort(key=lambda f: f.score, reverse=True)
        findings = findings[:max_findings]
    return findings


def analyze_patch(patch: str, *, game: str, source: str, file_path: str, matcher: KeywordMatcher,
                  context_lines: int = 2, max_findings: int = 400) -> list[Finding]:
    changes: list[Change] = []
    for hunk in parse_patch(patch):
        changes.extend(hunk_changes(hunk, context_lines))
    return changes_to_findings(changes, game=game, source=source, file_path=file_path,
                               matcher=matcher, max_findings=max_findings)


def analyze_line_sets(old_lines: list[str], new_lines: list[str], *, game: str, source: str, file_path: str,
                      matcher: KeywordMatcher, context_lines: int = 2, max_findings: int = 400) -> list[Finding]:
    changes = diff_by_sets(old_lines, new_lines, context_lines)
    return changes_to_findings(changes, game=game, source=source, file_path=file_path,
                               matcher=matcher, max_findings=max_findings)
