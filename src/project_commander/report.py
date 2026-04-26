"""Render `ProjectReport` collections to terminal, markdown, or JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from typing import Iterable

from rich.console import Console
from rich.table import Table

from .models import ProjectReport, Signal
from .observations import progress_label


# Single-letter source flags for compact column display.
_SRC_FLAGS = {
    "git": "G", "claude": "C", "gemini": "M", "omp": "O",
    "opencode": "P", "kiro": "K", "docs": "D", "fs": "F",
}


def render_table(reports: Iterable[ProjectReport], console: Console) -> None:
    table = Table(show_lines=False, expand=True)
    table.add_column("Project", style="bold cyan", no_wrap=True)
    table.add_column("Last active", style="green", no_wrap=True)
    table.add_column("Progress", no_wrap=True)
    table.add_column("Sources", no_wrap=True)
    table.add_column("Git", no_wrap=True)
    table.add_column("Intent", overflow="ellipsis")
    for r in reports:
        table.add_row(
            r.name,
            _fmt_last_active(r.last_active),
            _fmt_progress(r),
            _fmt_sources(r),
            _fmt_git(r),
            _truncate(r.intent or "(none)", limit=180),
        )
    console.print(table)

def _truncate(text: str, *, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"

def render_detail(report: ProjectReport, console: Console) -> None:
    obs = report.observations
    console.rule(f"[bold cyan]{report.name}")
    console.print(f"[dim]{report.path}")
    console.print(f"Last active: [green]{_fmt_last_active(report.last_active)}[/green]")
    console.print(f"Git: {_fmt_git(report)}")
    console.print(f"Sources: {_fmt_sources(report)}")
    console.print()
    if obs is not None:
        console.print(f"[bold]Progress:[/bold] {_progress_text(obs)} \u2014 {obs.progress_summary}")
        if obs.purpose:
            console.print(f"[bold]Purpose:[/bold] {obs.purpose}")
        if obs.focus:
            console.print(f"[bold]Focus:[/bold] {obs.focus}")
        else:
            console.print("[bold]Focus:[/bold] [dim](no recent prompts)[/dim]")
        if obs.last_action:
            when = _fmt_last_active(obs.last_action_at)
            console.print(f"[bold]Last action:[/bold] {obs.last_action} [dim]({when}, {obs.last_action_source})[/dim]")
        if obs.flags:
            console.print("[bold]Flags:[/bold] " + " ".join(f"[yellow]{f}[/yellow]" for f in obs.flags))
        if obs.evidence:
            console.print("[dim]Evidence: " + "; ".join(obs.evidence) + "[/dim]")
        w = obs.window_30d
        console.print(
            f"[dim]Activity: 7d \u2192 {obs.window_7d.commits}c/{obs.window_7d.prompts}p across "
            f"{obs.window_7d.distinct_days} day(s); 30d \u2192 {w.commits}c/{w.prompts}p[/dim]"
        )
    else:
        console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
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
    out.append("| Project | Last active | Progress | Sources | Git | Intent |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for r in reports:
        obs = r.observations
        progress = progress_label(obs.progress) if obs is not None else ""
        out.append(
            f"| {_md_escape(r.name)} | {_fmt_last_active(r.last_active)} "
            f"| {progress} | {_fmt_sources(r)} | {_fmt_git(r, plain=True)} "
            f"| {_md_escape(r.intent or '')} |"
        )
    return "\n".join(out) + "\n"


def render_json(reports: Iterable[ProjectReport]) -> str:
    payload = []
    for r in reports:
        obs = r.observations
        obs_block = None
        if obs is not None:
            obs_block = {
                "purpose": obs.purpose,
                "focus": obs.focus,
                "intent": obs.intent,
                "progress": obs.progress.value,
                "progress_label": progress_label(obs.progress),
                "progress_summary": obs.progress_summary,
                "last_action": obs.last_action,
                "last_action_at": obs.last_action_at.isoformat() if obs.last_action_at else None,
                "last_action_source": obs.last_action_source,
                "flags": list(obs.flags),
                "evidence": list(obs.evidence),
                "windows": {
                    "7d": _window_dict(obs.window_7d),
                    "30d": _window_dict(obs.window_30d),
                    "90d": _window_dict(obs.window_90d),
                },
            }
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
            "observations": obs_block,
            "intent": r.intent,
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

def _window_dict(w) -> dict:
    return {
        "days": w.days,
        "commits": w.commits,
        "prompts": w.prompts,
        "sessions": w.sessions,
        "distinct_days": w.distinct_days,
    }


_PROGRESS_STYLE = {
    "hot": "bright_red",
    "active": "green",
    "paused": "yellow",
    "cooling": "cyan",
    "idle": "blue",
    "dormant": "dim",
    "shipped": "bright_green",
    "drifting": "magenta",
    "tracking": "blue",
    "stub": "dim",
    "empty": "dim",
}


def _fmt_progress(r: ProjectReport) -> str:
    obs = r.observations
    if obs is None:
        return "\u2014"
    label = progress_label(obs.progress)
    style = _PROGRESS_STYLE.get(obs.progress.value, "white")
    return f"[{style}]{label}[/{style}]"


def _progress_text(obs) -> str:
    label = progress_label(obs.progress)
    style = _PROGRESS_STYLE.get(obs.progress.value, "white")
    return f"[{style}]{label}[/{style}]"