"""Render `ProjectReport` collections to terminal, markdown, or JSON."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterable

from rich.console import Console
from rich.table import Table

from .models import ProjectReport, Signal
from .observations import is_procedural, progress_label


# Single-letter source flags for compact column display.
_SRC_FLAGS = {
	"git": "G", "claude": "C", "gemini": "M", "omp": "O",
	"opencode": "P", "kiro": "K", "docs": "D", "fs": "F",
}

_DASH = "\u2014"

_PURPOSE_LIMIT = 220
_FOCUS_LIMIT = 200
_BULLET_LIMIT = 140
_DOC_BULLET_LIMIT = 160

_BLOCK_QUOTE_RE = re.compile(r"^\s*>\s*")
_HEADING_RE = re.compile(r"^\s*#+\s*")
_LIST_MARKER_RE = re.compile(r"^\s*[-*]\s+")
_DOC_TAG_RE = re.compile(r"^\[(?P<rel>[^\]]+)\]\s*")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


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


def render_detail(report: ProjectReport, console: Console) -> None:
	obs = report.observations
	console.rule(f"[bold cyan]{report.name}")
	console.print(f"[dim]{report.path}[/dim]")
	console.print(f"Last active: [green]{_fmt_last_active(report.last_active)}[/green]")
	console.print(f"Git: {_fmt_git(report)}")
	console.print(f"Sources: {_fmt_sources(report)}")
	console.print()
	if obs is not None:
		console.print(f"[bold]Progress:[/bold] {_progress_text(obs)} \u2014 {obs.progress_summary}")
		if obs.purpose:
			console.print(f"[bold]Purpose:[/bold] {_truncate(_clean_prose(obs.purpose), limit=_PURPOSE_LIMIT)}")
		if obs.focus:
			console.print(f"[bold]Focus:[/bold] {_truncate(_oneline(obs.focus), limit=_FOCUS_LIMIT)}")
		else:
			console.print("[bold]Focus:[/bold] [dim](no recent prompts)[/dim]")
		if obs.last_action:
			when = _fmt_last_active(obs.last_action_at)
			console.print(f"[bold]Last action:[/bold] {_oneline(obs.last_action)} [dim]({when}, {obs.last_action_source})[/dim]")
		if obs.flags:
			console.print("[bold]Flags:[/bold] " + " ".join(f"[yellow]{f}[/yellow]" for f in obs.flags))
		w7, w30 = obs.window_7d, obs.window_30d
		console.print(
			f"[dim]Activity: 7d \u2192 {w7.commits}c / {w7.prompts}p across {w7.distinct_days} day(s); "
			f"30d \u2192 {w30.commits}c / {w30.prompts}p[/dim]"
		)
		if obs.evidence:
			console.print("[dim]Evidence: " + "; ".join(obs.evidence) + "[/dim]")
	else:
		console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
	console.print()
	_terminal_section(console, "Recent commits", report.recent(n=10, kinds=["commit"]))
	_terminal_prompts_section(console, report)
	_terminal_section(console, "Sessions", report.recent(n=6, kinds=["session"]))
	_terminal_docs_section(console, report)


def render_detail_markdown(report: ProjectReport) -> str:
	"""Self-contained markdown writeup for a single project."""
	obs = report.observations
	lines: list[str] = []
	lines.append(f"# {report.name}")
	lines.append("")
	lines.append(f"`{report.path}`")
	lines.append("")
	lines.append(f"- **Last active:** {_fmt_last_active(report.last_active)}")
	if obs is not None:
		lines.append(
			f"- **Progress:** {progress_label(obs.progress)} \u2014 {obs.progress_summary}"
		)
	lines.append(f"- **Git:** {_fmt_git(report, plain=True) or _DASH}")
	lines.append(f"- **Sources:** {_fmt_sources(report)}")
	if obs is not None and obs.flags:
		lines.append("- **Flags:** " + ", ".join(f"`{f}`" for f in obs.flags))
	lines.append("")

	if obs is not None:
		if obs.purpose:
			lines.append("## Purpose")
			lines.append("")
			lines.append(_truncate(_clean_prose(obs.purpose), limit=_PURPOSE_LIMIT))
			lines.append("")
		if obs.focus:
			lines.append("## Currently")
			lines.append("")
			lines.append(_truncate(_oneline(obs.focus), limit=_FOCUS_LIMIT))
			lines.append("")
		if obs.last_action:
			when = _fmt_last_active(obs.last_action_at)
			lines.append(
				f"**Last action:** {_oneline(obs.last_action)}  "
				f"<sub>{when} \u00b7 `{obs.last_action_source}`</sub>"
			)
			lines.append("")

		w7, w30, w90 = obs.window_7d, obs.window_30d, obs.window_90d
		lines.append("## Activity")
		lines.append("")
		lines.append("| Window | Commits | Prompts | Sessions | Active days |")
		lines.append("| --- | ---: | ---: | ---: | ---: |")
		for label, w in [("7d", w7), ("30d", w30), ("90d", w90)]:
			lines.append(
				f"| {label} | {w.commits} | {w.prompts} | {w.sessions} | {w.distinct_days} |"
			)
		lines.append("")

		if obs.evidence:
			lines.append("<sub>**Evidence:** " + "; ".join(obs.evidence) + "</sub>")
			lines.append("")

	commits = report.recent(n=10, kinds=["commit"])
	if commits:
		lines.append("## Recent commits")
		lines.append("")
		for c in commits:
			lines.append(f"- `{c.timestamp.date()}` {_md_safe(_oneline(c.summary))}")
		lines.append("")

	all_prompts = report.recent(n=20, kinds=["prompt"])
	subst = [p for p in all_prompts if not is_procedural(p.summary)]
	procd = [p for p in all_prompts if is_procedural(p.summary)]
	if subst:
		lines.append("## Recent prompts")
		lines.append("")
		for p in subst[:8]:
			body = _truncate(_oneline(p.summary), limit=200)
			lines.append(f"- `{p.timestamp.date()}` {_md_safe(body)} <sub>(`{p.source}`)</sub>")
		lines.append("")
	if procd:
		uniq = sorted({p.summary.strip().lower().rstrip(".") for p in procd})
		lines.append(
			f"<sub>+ {len(procd)} approval prompt(s) omitted: "
			+ ", ".join(f"`{u}`" for u in uniq[:6])
			+ "</sub>"
		)
		lines.append("")

	sessions = report.recent(n=6, kinds=["session"])
	if sessions:
		lines.append("## Sessions")
		lines.append("")
		for s in sessions:
			body = _truncate(_oneline(s.summary), limit=120)
			lines.append(f"- `{s.timestamp.date()}` {_md_safe(body)} <sub>(`{s.source}`)</sub>")
		lines.append("")

	docs = report.recent(n=8, kinds=["doc"])
	if docs:
		lines.append("## Plan docs")
		lines.append("")
		for d in docs:
			ref, rest = _split_doc_summary(d)
			rest_clean = _truncate(_clean_prose(rest), limit=_DOC_BULLET_LIMIT) if rest else ""
			suffix = f" \u2014 {_md_safe(rest_clean)}" if rest_clean else ""
			lines.append(f"- `{d.timestamp.date()}` **{ref}**{suffix}")
		lines.append("")

	return "\n".join(lines).rstrip() + "\n"


def render_markdown(reports: Iterable[ProjectReport]) -> str:
	out: list[str] = []
	out.append("| Project | Last active | Progress | Sources | Git | Intent |")
	out.append("| --- | --- | --- | --- | --- | --- |")
	for r in reports:
		obs = r.observations
		progress = progress_label(obs.progress) if obs is not None else ""
		out.append(
			f"| {_md_safe(r.name)} | {_fmt_last_active(r.last_active)} "
			f"| {progress} | {_fmt_sources(r)} | {_fmt_git(r, plain=True)} "
			f"| {_md_safe(_oneline(r.intent or ''))} |"
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


# ---------- terminal section helpers ----------

def _terminal_section(console: Console, title: str, items: list[Signal]) -> None:
	if not items:
		return
	console.print(f"[bold]{title}[/bold]")
	for s in items:
		when = s.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
		body = _truncate(_oneline(s.summary), limit=_BULLET_LIMIT)
		console.print(f"  [dim]{when}[/dim] {body}")
	console.print()


def _terminal_prompts_section(console: Console, report: ProjectReport) -> None:
	all_prompts = report.recent(n=20, kinds=["prompt"])
	subst = [p for p in all_prompts if not is_procedural(p.summary)]
	procd = [p for p in all_prompts if is_procedural(p.summary)]
	if not subst and not procd:
		return
	console.print("[bold]Recent prompts[/bold]")
	for p in subst[:8]:
		when = p.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
		body = _truncate(_oneline(p.summary), limit=_BULLET_LIMIT)
		console.print(f"  [dim]{when}[/dim] {body}")
	if procd:
		uniq = sorted({p.summary.strip().lower().rstrip(".") for p in procd})
		console.print(
			f"  [dim]+ {len(procd)} approval prompt(s): "
			+ ", ".join(uniq[:6])
			+ "[/dim]"
		)
	console.print()


def _terminal_docs_section(console: Console, report: ProjectReport) -> None:
	docs = report.recent(n=8, kinds=["doc"])
	if not docs:
		return
	console.print("[bold]Plan docs[/bold]")
	for d in docs:
		when = d.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
		ref, rest = _split_doc_summary(d)
		rest_clean = _truncate(_clean_prose(rest), limit=_DOC_BULLET_LIMIT) if rest else ""
		suffix = f" \u2014 {rest_clean}" if rest_clean else ""
		console.print(f"  [dim]{when}[/dim] [bold]{ref}[/bold]{suffix}")
	console.print()


# ---------- formatting helpers ----------

def _truncate(text: str, *, limit: int) -> str:
	text = " ".join(text.split())
	if len(text) <= limit:
		return text
	return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _oneline(text: str) -> str:
	"""Collapse runs of whitespace (incl. newlines/tabs) to single spaces."""
	return " ".join(text.split())


def _clean_prose(text: str) -> str:
	"""Strip line-leading markdown noise from prose taken from a doc.

	Removes blockquotes (`>`), heading hashes, list bullets, then collapses
	whitespace and unwraps `**bold**` / inline `code` markup so the result
	reads as plain text.
	"""
	cleaned_lines: list[str] = []
	for raw in text.splitlines():
		line = _BLOCK_QUOTE_RE.sub("", raw)
		line = _HEADING_RE.sub("", line)
		line = _LIST_MARKER_RE.sub("", line)
		cleaned_lines.append(line)
	cleaned = " ".join(cleaned_lines)
	cleaned = _BOLD_RE.sub(r"\1", cleaned)
	cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
	return _oneline(cleaned)


def _split_doc_summary(s: Signal) -> tuple[str, str]:
	"""Pull the `[<rel>]` prefix out of a docs Signal's summary."""
	if s.ref:
		ref = s.ref
		body = _DOC_TAG_RE.sub("", s.summary)
		return ref, body
	m = _DOC_TAG_RE.match(s.summary)
	if m:
		return m.group("rel"), s.summary[m.end():]
	return "doc", s.summary


def _fmt_last_active(ts: datetime | None) -> str:
	if ts is None:
		return "\u2014"
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
	return "".join(flags) if flags else "\u2014"


def _fmt_git(r: ProjectReport, *, plain: bool = False) -> str:
	if not r.is_git_repo:
		return "\u2014" if not plain else ""
	label = r.git_branch or "(detached)"
	if r.git_dirty:
		label += "*"
	if plain:
		return label
	style = "yellow" if r.git_dirty else "green"
	return f"[{style}]{label}[/{style}]"


def _md_safe(s: str) -> str:
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
