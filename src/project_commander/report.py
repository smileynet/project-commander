"""Render `ProjectReport` collections to terminal, markdown, or JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from typing import Iterable

from rich.console import Console
from rich.table import Table

from .models import ProjectReport, Signal


# Single-letter source flags for compact column display.
_SRC_FLAGS = {
    "git": "G", "claude": "C", "gemini": "M", "omp": "O",
    "opencode": "P", "kiro": "K", "docs": "D", "fs": "F",
}


def render_table(reports: Iterable[ProjectReport], console: Console) -> None:
    table = Table(show_lines=False, expand=True)
    table.add_column("Project", style="bold cyan", no_wrap=True)
    table.add_column("Last active", style="green", no_wrap=True)
    table.add_column("Sources", no_wrap=True)
    table.add_column("Git", no_wrap=True)
    table.add_column("Intent", overflow="ellipsis")
    for r in reports:
        table.add_row(
            r.name,
            _fmt_last_active(r.last_active),
            _fmt_sources(r),
            _fmt_git(r),
            _truncate(r.intent or "(none)", limit=140),
        )
    console.print(table)

def _truncate(text: str, *, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"

def render_detail(report: ProjectReport, console: Console) -> None:
    console.rule(f"[bold cyan]{report.name}")
    console.print(f"[dim]{report.path}")
    console.print(f"Last active: [green]{_fmt_last_active(report.last_active)}[/green]")
    console.print(f"Git: {_fmt_git(report)}")
    console.print(f"Sources: {_fmt_sources(report)}")
    console.print()
    console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
    if report.intent_evidence:
        console.print("[dim]Evidence: " + "; ".join(report.intent_evidence) + "[/dim]")
    console.print()
    _section(console, "Recent commits", report.recent(n=8, kinds=["commit"]))
    _section(console, "Recent prompts", report.recent(n=8, kinds=["prompt"]))
    _section(console, "Sessions", report.recent(n=6, kinds=["session"]))
    _section(console, "Plan docs", report.recent(n=6, kinds=["doc"]))


def _section(console: Console, title: str, items: list[Signal]) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for s in items:
        when = s.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
        console.print(f"  [dim]{when}[/dim] [{s.source}] {s.summary}")
    console.print()


def render_markdown(reports: Iterable[ProjectReport]) -> str:
    out: list[str] = []
    out.append("| Project | Last active | Sources | Git | Intent |")
    out.append("| --- | --- | --- | --- | --- |")
    for r in reports:
        out.append(
            f"| {_md_escape(r.name)} | {_fmt_last_active(r.last_active)} "
            f"| {_fmt_sources(r)} | {_fmt_git(r, plain=True)} "
            f"| {_md_escape(r.intent or '')} |"
        )
    return "\n".join(out) + "\n"


def render_json(reports: Iterable[ProjectReport]) -> str:
    payload = []
    for r in reports:
        payload.append({
            "name": r.name,
            "path": str(r.path),
            "last_active": r.last_active.isoformat() if r.last_active else None,
            "sources": r.sources_active,
            "git": {
                "is_repo": r.is_git_repo,
                "branch": r.git_branch,
                "dirty": r.git_dirty,
            },
            "intent": r.intent,
            "intent_evidence": r.intent_evidence,
            "signals": [
                {
                    "source": s.source, "kind": s.kind,
                    "timestamp": s.timestamp.isoformat(),
                    "summary": s.summary, "ref": s.ref,
                }
                for s in sorted(r.signals, key=lambda s: s.timestamp, reverse=True)
            ],
        })
    return json.dumps(payload, indent=2)


def _fmt_last_active(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    delta = datetime.now(tz=timezone.utc) - ts
    days = delta.days
    if days < 0:
        return ts.strftime("%Y-%m-%d")
    if days == 0:
        hours = delta.seconds // 3600
        if hours == 0:
            return f"{delta.seconds // 60}m ago"
        return f"{hours}h ago"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 7}w ago"
    return ts.strftime("%Y-%m-%d")


def _fmt_sources(r: ProjectReport) -> str:
    flags = [_SRC_FLAGS.get(s, "?") for s in r.sources_active]
    return "".join(flags) if flags else "—"


def _fmt_git(r: ProjectReport, *, plain: bool = False) -> str:
    if not r.is_git_repo:
        return "—" if not plain else ""
    label = r.git_branch or "(detached)"
    if r.git_dirty:
        label += "*"
    if plain:
        return label
    style = "yellow" if r.git_dirty else "green"
    return f"[{style}]{label}[/{style}]"


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
