from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from hlxwatch.config import Config
from hlxwatch.models import Finding

SEVERITY_ICON = {"high": "\U0001F534", "medium": "\U0001F7E0", "low": "⚪"}
SEVERITY_COLOR = {"high": 0xE53935, "medium": 0xFB8C00, "low": 0x9E9E9E}


@dataclass
class AlertGroup:
    game: str
    title: str
    findings: list[Finding]
    max_score: int
    total: int


def group_title(cfg: Config, game: str) -> str:
    if game == "steam":
        return "Steam (nuove app)"
    return cfg.game_title(game)


def merge_siblings(findings: list[Finding]) -> list[Finding]:
    """Una stringa trovata in 9 dump dello stesso commit diventa una riga sola con '+8 file'."""
    merged: dict[tuple[Any, ...], Finding] = {}
    out: list[Finding] = []
    for finding in findings:
        key = (finding.dedup_key, finding.change_type, finding.build.ref if finding.build else None)
        first = merged.get(key)
        if first is None:
            finding.siblings = []
            merged[key] = finding
            out.append(finding)
        else:
            first.siblings.append(finding.file_path)
            first.score = max(first.score, finding.score)
    return out


def group_findings(findings: list[Finding], cfg: Config) -> list[AlertGroup]:
    max_items = int(cfg.get("general.notify_max_items", 25))
    by_game: dict[str, list[Finding]] = {}
    for finding in findings:
        by_game.setdefault(finding.game, []).append(finding)
    groups: list[AlertGroup] = []
    for game, items in by_game.items():
        items = merge_siblings(items)
        items.sort(key=lambda f: f.score, reverse=True)
        groups.append(AlertGroup(game, group_title(cfg, game), items[:max_items], items[0].score, len(items)))
    groups.sort(key=lambda g: g.max_score, reverse=True)
    return groups


def finding_line(finding: Finding, style: str) -> str:
    """Una riga per notifica: markdown (Discord) oppure HTML (Telegram)."""
    label = (finding.token or finding.text)[:100].replace("`", "'")
    where = finding.file_path
    if finding.line_no:
        where += f":{finding.line_no}"
    if finding.siblings:
        where += f" (+{len(finding.siblings)} file)"
    url = finding.build.url if finding.build and finding.build.url else None
    icon = SEVERITY_ICON[finding.severity]
    if style == "html":
        line = (f"{icon} <b>[{finding.score}]</b> <code>{html.escape(label)}</code> "
                f"— {html.escape(where)} ({finding.change_type})")
        if url:
            line += f' <a href="{html.escape(url, quote=True)}">link</a>'
        return line
    line = f"{icon} **[{finding.score}]** `{label}` — {where} ({finding.change_type})"
    if url:
        line += f" [link]({url})"
    return line


class Notifier:
    name = "base"

    def send_text(self, title: str, text: str) -> bool:
        raise NotImplementedError

    def send_groups(self, heading: str, groups: list[AlertGroup]) -> bool:
        raise NotImplementedError
