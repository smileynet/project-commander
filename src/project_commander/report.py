"""Render `ProjectReport` collections to terminal, markdown, or JSON.

Three table-shaped views over the same underlying observations:

- `render_table`   fleet check-in: one row per project, grouped by attention band
- `render_review`  period-in-review (Moved forward / Parked dirty / Flagged / New)
- `render_detail`  per-project audit: state, purpose, outstanding, next, history

Plus markdown / JSON serializers that do not group.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Sequence

from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from .models import ProjectReport, Signal
from .observations import (
	Outstanding,
	Progress,
	clean_doc_prose,
	first_sentence,
	is_procedural,
	progress_label,
)


# Single-letter source flags for compact column display.
_SRC_FLAGS = {
	"git": "G", "claude": "C", "gemini": "M", "omp": "O",
	"opencode": "P", "kiro": "K", "docs": "D", "fs": "F",
}

_DASH = "\u2014"

_PURPOSE_LIMIT = 200
_INTENT_LIMIT = 180
_FOCUS_LIMIT = 200
_BULLET_LIMIT = 140
_DOC_BULLET_LIMIT = 160


# ───── public renderers ──────────────────────────────────────────────────────

def render_table(reports: Iterable[ProjectReport], console: Console) -> None:
	"""Fleet check-in: rows grouped by attention band, calmer where outstanding is empty."""
	rows = list(reports)
	if not rows:
		console.print("[dim](no projects)[/dim]")
		return
	bands = _group_by_band(rows)
	for label, band_rows in bands:
		if not band_rows:
			continue
		console.print()
		console.print(Rule(f"[bold]{label}[/bold]", style="dim"))
		_render_band_table(band_rows, console)


def render_review(reports: Iterable[ProjectReport], *, since_days: int, console: Console) -> None:
	"""Period-in-review digest optimized for scan-first triage."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
	from datetime import timedelta
	cutoff = cutoff - timedelta(days=since_days)
	header = f"Last {since_days} day(s) — since {cutoff.date().isoformat()}"
	console.print()
	console.print(Rule(f"[bold]{header}[/bold]", style="cyan"))
	sections = _review_sections(rows, cutoff=cutoff)
	if not any(entries for _, entries in sections):
		console.print()
		console.print("  [dim](nothing moved, nothing needs attention)[/dim]")
		return
	for title, entries in sections:
		if not entries:
			continue
		_print_review_section(console, title, entries)


def render_review_markdown(reports: Iterable[ProjectReport], *, since_days: int) -> str:
	"""Markdown week-in-review digest tuned for cross-project triage."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
	from datetime import timedelta
	cutoff = cutoff - timedelta(days=since_days)
	sections = _review_sections(rows, cutoff=cutoff)
	lines = [f"# Last {since_days} day(s)", "", f"_Since {cutoff.date().isoformat()}_", ""]
	if not any(entries for _, entries in sections):
		lines.append("_Nothing moved, nothing needs attention._")
		return "\n".join(lines).rstrip() + "\n"
	for title, entries in sections:
		if not entries:
			continue
		lines.append(f"## {title} ({len(entries)})")
		lines.append("")
		for entry in entries[:_REVIEW_SECTION_CAP]:
			lines.extend(_review_markdown_entry(entry))
		if len(entries) > _REVIEW_SECTION_CAP:
			lines.append(f"- _… +{len(entries) - _REVIEW_SECTION_CAP} more_")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


def render_detail(report: ProjectReport, console: Console) -> None:
	obs = report.observations
	console.rule(f"[bold cyan]{report.name}")
	console.print(f"[dim]{report.path}[/dim]")
	if obs is None:
		console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
		return
	console.print()
	console.print(f"[bold]State:[/bold] {progress_label(obs.progress)} · {_fmt_last_active(report.last_active)}")
	if obs.workstream:
		console.print(f"[bold]Workstream:[/bold] {_truncate(_oneline(obs.workstream), limit=220)}")
	if obs.open_issue:
		console.print(f"[bold]Open issue:[/bold] {_truncate(_oneline(obs.open_issue), limit=220)}")
	if obs.why_stopped:
		console.print(f"[bold]Why stopped:[/bold] {_truncate(_oneline(obs.why_stopped), limit=220)}")
	if obs.next_action:
		console.print(f"[bold green]First action:[/bold green] {obs.next_action}")
	console.print()
	guide = _source_guide_items(report)
	snapshot = _snapshot_line(report)
	if guide or snapshot:
		console.print("[bold]Where to inspect[/bold]")
		for label, value in guide:
			console.print(f"  [yellow]{label}[/yellow]  {value}")
		if snapshot:
			console.print(f"  [yellow]Snapshot[/yellow]  {snapshot}")
		console.print()
	commits = report.recent(n=5, kinds=["commit"])
	if commits:
		_terminal_section(console, "Git history", commits)
	_terminal_prompts_section(console, report)
	sessions = report.recent(n=2, kinds=["session"])
	if sessions:
		_terminal_section(console, "Sessions", sessions)
	_terminal_plan_docs_section(console, report)


def render_detail_markdown(report: ProjectReport) -> str:
	"""Self-contained markdown writeup for a single project, optimized for resume-first reading."""
	obs = report.observations
	lines: list[str] = []
	lines.append(f"# {report.name}")
	lines.append("")
	lines.append(f"`{report.path}`")
	lines.append("")
	if obs is not None:
		lines.append("> **Resume this project.**  ")
		lines.append(f"> **State:** {progress_label(obs.progress)} · {_fmt_last_active(report.last_active)}.  ")
		if obs.workstream:
			lines.append(f"> **Workstream:** {_md_safe(_truncate(_oneline(obs.workstream), limit=180))}  ")
		if obs.open_issue:
			lines.append(f"> **Open issue:** {_md_safe(_truncate(_oneline(obs.open_issue), limit=180))}  ")
		if obs.why_stopped:
			lines.append(f"> **Why stopped:** {_md_safe(_truncate(_oneline(obs.why_stopped), limit=200))}  ")
		if obs.next_action:
			lines.append(f"> **First action:** {_md_safe(obs.next_action)}")
		lines.append("")
	if obs is not None:
		guide = _source_guide_items(report)
		snapshot = _snapshot_line(report)
		if guide or snapshot:
			lines.append("## Where to inspect")
			lines.append("")
			for label, value in guide:
				lines.append(f"- **{label}:** {_md_safe(value)}")
			if snapshot:
				lines.append(f"- **Snapshot:** {_md_safe(snapshot)}")
			lines.append("")
	commits = report.recent(n=5, kinds=["commit"])
	all_prompts = report.recent(n=20, kinds=["prompt"])
	subst = [p for p in all_prompts if not is_procedural(p.summary)]
	procd = [p for p in all_prompts if is_procedural(p.summary)]
	sessions = report.recent(n=2, kinds=["session"])
	has_raw_sources = bool(commits or subst or procd or sessions or report.plan_summaries or report.recent(n=8, kinds=["doc"]))
	if has_raw_sources:
		lines.append("## Raw sources")
		lines.append("")
	if commits:
		lines.append("### Git history")
		lines.append("")
		for c in commits:
			lines.append(f"- `{c.timestamp.date()}` {_md_safe(_oneline(c.summary))}")
		lines.append("")
	if subst:
		lines.append("### Prompt thread")
		lines.append("")
		commits_set = sorted([s.timestamp for s in report.signals if s.kind == "commit"], reverse=True)
		for p in subst[:3]:
			body = _truncate(_oneline(p.summary), limit=200)
			marker = " ~" if not _has_followup_commit(p, commits_set) else ""
			lines.append(f"- `{p.timestamp.date()}`{marker} {_md_safe(body)} <sub>(`{p.source}`)</sub>")
		lines.append("")
		if any(not _has_followup_commit(p, commits_set) for p in subst[:3]):
			lines.append("<sub>~ marks prompts with no follow-up commit.</sub>")
			lines.append("")
	if procd:
		uniq = sorted({p.summary.strip().lower().rstrip(".") for p in procd})
		lines.append(
			f"<sub>+ {len(procd)} approval prompt(s) omitted: "
			+ ", ".join(f"`{u}`" for u in uniq[:6])
			+ "</sub>"
		)
		lines.append("")
	if sessions:
		lines.append("### Sessions")
		lines.append("")
		for s in sessions:
			body = _truncate(_oneline(s.summary), limit=120)
			lines.append(f"- `{s.timestamp.date()}` {_md_safe(body)} <sub>(`{s.source}`)</sub>")
		lines.append("")
	if report.plan_summaries or report.recent(n=8, kinds=["doc"]):
		lines.append("### Plan docs")
		lines.append("")
		_md_plan_docs(lines, report)
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


def render_markdown(reports: Iterable[ProjectReport]) -> str:
	out: list[str] = []
	out.append("| Project | State | Outstanding | Sources | Git | Intent |")
	out.append("| --- | --- | --- | --- | --- | --- |")
	for r in reports:
		obs = r.observations
		state = _fmt_state(r, plain=True)
		outstanding = obs.outstanding.headline if obs is not None else _DASH
		intent = first_sentence(obs.intent, limit=_INTENT_LIMIT) if obs is not None else r.intent
		out.append(
			f"| {_md_safe(r.name)} | {state} "
			f"| {_md_safe(outstanding)} | {_fmt_sources(r)} | {_fmt_git(r, plain=True)} "
			f"| {_md_safe(_oneline(intent or ''))} |"
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
				"workstream": obs.workstream,
				"open_issue": obs.open_issue,
				"why_stopped": obs.why_stopped,
				"recent_changes": obs.recent_changes,
				"attention": obs.attention,
				"progress": obs.progress.value,
				"progress_label": progress_label(obs.progress),
				"progress_summary": obs.progress_summary,
				"last_action": obs.last_action,
				"last_action_at": obs.last_action_at.isoformat() if obs.last_action_at else None,
				"last_action_source": obs.last_action_source,
				"flags": list(obs.flags),
				"evidence": list(obs.evidence),
				"next_action": obs.next_action,
				"outstanding": _outstanding_dict(obs.outstanding),
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
				"upstream": r.git_upstream,
				"ahead": r.git_ahead,
				"behind": r.git_behind,
				"uncommitted": list(r.git_uncommitted),
			},
			"plan_summaries": {
				path: {
					"path": s.path,
					"total_items": s.total_items,
					"open_items": s.open_items,
					"total_phases": s.total_phases,
					"complete_phases": s.complete_phases,
					"next_item": s.next_item,
				}
				for path, s in r.plan_summaries.items()
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


# ───── fleet table internals ─────────────────────────────────────────────────

# Mapping from progress state to display band. Bands surface attention.
_BAND_ACTIVE = {Progress.HOT, Progress.ACTIVE}
_BAND_ATTN = {Progress.DRIFTING, Progress.PAUSED}
# Everything else is 'Quiet' (Cooling, Idle, Dormant, Shipped, Tracking, Stub, Empty)


def _group_by_band(rows: Sequence[ProjectReport]) -> list[tuple[str, list[ProjectReport]]]:
	active: list[ProjectReport] = []
	attn: list[ProjectReport] = []
	quiet: list[ProjectReport] = []
	for r in rows:
		obs = r.observations
		if obs is None:
			quiet.append(r)
			continue
		if obs.progress in _BAND_ACTIVE:
			active.append(r)
		elif obs.progress in _BAND_ATTN:
			attn.append(r)
		else:
			quiet.append(r)
	# Within attention band, dirty/dirty trees and drift come first.
	attn.sort(key=lambda r: _attn_priority(r))
	return [
		("Needs attention", attn),
		("Active", active),
		("Quiet", quiet),
	]


def _attn_priority(r: ProjectReport) -> tuple[int, float]:
	"""Sort key inside the 'Needs attention' band: drift first, then dirtiest by recency."""
	obs = r.observations
	la = (r.last_active or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp()
	if obs is None:
		return (9, -la)
	if obs.progress is Progress.DRIFTING:
		return (0, -la)
	if obs.outstanding.git_uncommitted_count > 0:
		return (1, -la)
	return (2, -la)


def _render_band_table(rows: Sequence[ProjectReport], console: Console) -> None:
	table = Table(show_lines=False, expand=True, show_edge=False, pad_edge=False)
	table.add_column("Project", style="bold cyan", no_wrap=True)
	table.add_column("State", no_wrap=True)
	table.add_column("Outstanding", no_wrap=True)
	table.add_column("Sources", no_wrap=True)
	table.add_column("Git", no_wrap=True)
	table.add_column("Intent", overflow="ellipsis")
	for r in rows:
		obs = r.observations
		intent = first_sentence(obs.intent, limit=_INTENT_LIMIT) if obs is not None else (r.intent or "(none)")
		table.add_row(
			r.name,
			_fmt_state(r),
			_fmt_outstanding_cell(obs),
			_fmt_sources(r),
			_fmt_git(r),
			intent or "(none)",
		)
	console.print(table)


# ───── period-in-review internals ────────────────────────────────────────────

def _review_counts(report: ProjectReport, *, cutoff: datetime) -> tuple[int, int]:
	commits = sum(1 for s in report.signals if s.kind == "commit" and s.timestamp >= cutoff)
	prompts = sum(
		1 for s in report.signals
		if s.kind == "prompt" and s.timestamp >= cutoff and not is_procedural(s.summary)
	)
	return commits, prompts


def _review_first_commit(report: ProjectReport) -> datetime | None:
	commits = [s.timestamp for s in report.signals if s.kind == "commit"]
	return min(commits) if commits else None


def _review_primary_flag(report: ProjectReport) -> str:
	obs = report.observations
	if obs is None:
		return ""
	priority = ("plan-drift", "prompt-injection-detected", "upstream-only", "tool-cluster")
	return next((flag for flag in priority if flag in obs.flags), "")


def _review_is_parked(report: ProjectReport) -> bool:
	obs = report.observations
	if obs is None:
		return False
	now = datetime.now(tz=timezone.utc)
	if obs.outstanding.git_uncommitted_count > 0:
		idle_days = (now - report.last_active).days if report.last_active else 0
		return idle_days >= 2
	if not report.is_git_repo and report.signals:
		return True
	return False


def _review_outcome_text(text: str) -> str:
	text = _truncate(_oneline(text), limit=200).rstrip(".")
	if text.startswith("Recent commits focused on "):
		return "Advanced " + text[len("Recent commits focused on "):]
	if text.startswith("Recent commit: "):
		return "Advanced " + text[len("Recent commit: "):]
	if text.startswith("Latest concrete action: "):
		return text[len("Latest concrete action: "):]
	return text


def _review_outcome(report: ProjectReport) -> str:
	obs = report.observations
	if obs is None:
		return ""
	for text in (obs.recent_changes, obs.workstream, obs.focus, obs.last_action, obs.purpose):
		if text:
			outcome = _review_outcome_text(text)
			if outcome:
				return outcome
	return ""


def _review_badges(report: ProjectReport, *, cutoff: datetime, flag: str, needs_attention: bool) -> tuple[str, ...]:
	badges: list[str] = []
	first_commit = _review_first_commit(report)
	if first_commit is not None and first_commit >= cutoff:
		badges.append("new")
	obs = report.observations
	if obs is not None and needs_attention:
		if obs.outstanding.git_uncommitted_count > 0:
			badges.append("dirty")
		elif not report.is_git_repo and report.signals:
			badges.append("no-git")
	if flag:
		badges.append(flag)
	return tuple(badges)


def _review_entry(report: ProjectReport, *, cutoff: datetime) -> dict | None:
	obs = report.observations
	if obs is None:
		return None
	commits, prompts = _review_counts(report, cutoff=cutoff)
	flag = _review_primary_flag(report)
	parked = _review_is_parked(report)
	first_commit = _review_first_commit(report)
	is_new = first_commit is not None and first_commit >= cutoff
	needs_attention = bool(
		flag or parked
		or obs.outstanding.git_uncommitted_count > 0
		or obs.outstanding.git_ahead > 0
		or obs.outstanding.git_behind > 0
		or obs.outstanding.orphaned_thread_age_hours is not None
		or (not report.is_git_repo and report.signals)
	)
	if not (commits or prompts or needs_attention):
		return None
	if needs_attention:
		section = "Needs a decision now"
	elif is_new:
		section = "Started this week"
	else:
		section = "Moved this week"
	last_ts = report.last_active.timestamp() if report.last_active else 0.0
	flag_rank = {"plan-drift": 0, "prompt-injection-detected": 1, "upstream-only": 2, "tool-cluster": 3}.get(flag, 4)
	if section == "Needs a decision now":
		sort_key = (flag_rank, -last_ts)
	elif section == "Started this week":
		sort_key = (-last_ts, -(commits + prompts))
	else:
		sort_key = (-(commits + prompts), -last_ts)
	return {
		"name": report.name,
		"section": section,
		"outcome": _review_outcome(report),
		"signals": f"{commits} commit(s), {prompts} substantive prompt(s)",
		"first_action": obs.next_action if section == "Needs a decision now" else "",
		"look_at": _source_locator(report),
		"started": f"Started {first_commit.strftime('%a')}" if section == "Started this week" and first_commit else "",
		"badges": _review_badges(report, cutoff=cutoff, flag=flag, needs_attention=needs_attention),
		"sort_key": sort_key,
	}


def _review_sections(rows: Sequence[ProjectReport], *, cutoff: datetime) -> list[tuple[str, list[dict]]]:
	buckets: dict[str, list[dict]] = {
		"Needs a decision now": [],
		"Moved this week": [],
		"Started this week": [],
	}
	for report in rows:
		entry = _review_entry(report, cutoff=cutoff)
		if entry is None:
			continue
		buckets[entry["section"]].append(entry)
	for entries in buckets.values():
		entries.sort(key=lambda item: item["sort_key"])
	return [(title, buckets[title]) for title in ("Needs a decision now", "Moved this week", "Started this week")]


_REVIEW_SECTION_CAP = 12


def _review_sentence(text: str) -> str:
	text = text.strip()
	if not text:
		return "(no weekly summary)"
	if text.endswith((".", "!", "?", "…")):
		return text
	return text + "."


def _print_review_section(console: Console, title: str, entries: list[dict]) -> None:
	count = len(entries)
	console.print()
	console.print(f"[bold]{title}[/bold] [dim]({count})[/dim]")
	for entry in entries[:_REVIEW_SECTION_CAP]:
		badge_str = f" [dim]({', '.join(entry['badges'])})[/dim]" if entry["badges"] else ""
		console.print(f"  [cyan]{entry['name']}[/cyan]{badge_str} [dim]— {_review_sentence(entry['outcome'])}[/dim]")
		if entry["first_action"]:
			console.print(f"    [green]Start with:[/green] {entry['first_action']}")
		if entry["look_at"]:
			console.print(f"    [dim]Look at: {entry['look_at']}[/dim]")
		elif entry["signals"]:
			console.print(f"    [dim]{entry['signals']}[/dim]")
		if entry["started"]:
			console.print(f"    [dim]{entry['started']}[/dim]")
	if count > _REVIEW_SECTION_CAP:
		console.print(f"  [dim]… +{count - _REVIEW_SECTION_CAP} more[/dim]")


def _review_markdown_entry(entry: dict) -> list[str]:
	name = _md_safe(str(entry["name"]))
	badge_str = f" _({', '.join(entry['badges'])})_" if entry["badges"] else ""
	lines = [f"- **{name}**{badge_str} — {_md_safe(_review_sentence(entry['outcome']))}"]
	if entry["first_action"]:
		lines.append(f"  - Start with: {_md_safe(entry['first_action'])}")
	if entry["look_at"]:
		lines.append(f"  - Look at: {_md_safe(entry['look_at'])}")
	elif entry["signals"]:
		lines.append(f"  - {_md_safe(entry['signals'])}")
	if entry["started"]:
		lines.append(f"  - {_md_safe(entry['started'])}")
	return lines


# ───── detail-view sub-blocks ────────────────────────────────────────────────

def _terminal_outstanding_section(console: Console, report: ProjectReport, o: Outstanding) -> None:
	console.print("[bold]Outstanding[/bold]")
	if o.is_empty:
		console.print("  [dim](clean \u2014 no uncommitted work, no unpushed commits, no orphaned threads)[/dim]")
		console.print()
		return
	if o.git_uncommitted_count > 0:
		examples = ", ".join(e.strip() for e in o.git_examples)
		more = f" \u2026 +{o.git_uncommitted_count - len(o.git_examples)} more" if o.git_uncommitted_count > len(o.git_examples) else ""
		console.print(f"  [yellow]git[/yellow]      {o.git_uncommitted_count} uncommitted file(s) [dim]({examples}{more})[/dim]")
	if o.git_ahead > 0:
		dest = o.git_upstream or "upstream"
		console.print(f"  [yellow]git[/yellow]      branch is {o.git_ahead} commit(s) ahead of [cyan]{dest}[/cyan]")
	if o.git_behind > 0:
		dest = o.git_upstream or "upstream"
		console.print(f"  [yellow]git[/yellow]      branch is {o.git_behind} commit(s) behind [cyan]{dest}[/cyan]")
	if o.plan_open_count > 0 or o.plan_open_phases > 0:
		ref = f"[cyan]{o.plan_doc_ref}[/cyan]: " if o.plan_doc_ref else ""
		bits = []
		if o.plan_open_count > 0:
			bits.append(f"{o.plan_open_count} unchecked item(s)")
		if o.plan_open_phases > 0:
			bits.append(f"{o.plan_open_phases} open phase(s)")
		next_part = f"; next: \"{o.plan_next}\"" if o.plan_next else ""
		console.print(f"  [yellow]plan[/yellow]     {ref}{', '.join(bits)}{next_part}")
	if o.orphaned_thread_age_hours is not None:
		console.print(f"  [yellow]thread[/yellow]   last prompt {o.orphaned_thread_age_hours}h ago has no follow-up commit")
	console.print()


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
	commits_ts = [s.timestamp for s in report.signals if s.kind == "commit"]
	any_orphan = False
	for p in subst[:5]:
		when = p.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
		body = _truncate(_oneline(p.summary), limit=_BULLET_LIMIT)
		orphan = not _has_followup_commit(p, commits_ts)
		marker = "[yellow]~[/yellow] " if orphan else "  "
		any_orphan = any_orphan or orphan
		console.print(f"  [dim]{when}[/dim] {marker}{body}")
	if any_orphan:
		console.print("  [dim]~ marks prompts with no follow-up commit[/dim]")
	if procd:
		uniq = sorted({p.summary.strip().lower().rstrip(".") for p in procd})
		console.print(
			f"  [dim]+ {len(procd)} approval prompt(s): "
			+ ", ".join(uniq[:6])
			+ "[/dim]"
		)
	console.print()


def _terminal_plan_docs_section(console: Console, report: ProjectReport) -> None:
	docs = report.recent(n=8, kinds=["doc"])
	if not docs and not report.plan_summaries:
		return
	console.print("[bold]Plan docs[/bold]")
	# Structured docs first
	rendered: set[str] = set()
	for path, summary in report.plan_summaries.items():
		when_sig = next((d for d in docs if d.ref == path), None)
		when = when_sig.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d") if when_sig else "        "
		bits: list[str] = []
		if summary.total_phases > 0:
			bits.append(f"{summary.total_phases} phases, {summary.complete_phases} complete")
		if summary.total_items > 0:
			done = summary.total_items - summary.open_items
			bits.append(f"{done}/{summary.total_items} items")
		if summary.next_item:
			bits.append(f'next: "{_truncate(summary.next_item, limit=80)}"')
		console.print(f"  [dim]{when}[/dim] [bold]{path}[/bold]   " + "; ".join(bits))
		rendered.add(path)
	# Prose docs (first sentence) for the rest
	for d in docs:
		if d.ref in rendered:
			continue
		when = d.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
		_, rest = _split_doc_summary(d)
		clean = clean_doc_prose(rest) if rest else ""
		summary = first_sentence(clean, limit=_DOC_BULLET_LIMIT) if clean else ""
		suffix = f" \u2014 {summary}" if summary else ""
		console.print(f"  [dim]{when}[/dim] [bold]{d.ref or 'doc'}[/bold]{suffix}")
	console.print()


# ───── markdown sub-blocks ───────────────────────────────────────────────────

def _md_outstanding_block(lines: list[str], report: ProjectReport, o: Outstanding) -> None:
	lines.append("## Outstanding")
	lines.append("")
	if o.is_empty:
		lines.append("_Clean — no uncommitted work, no unpushed commits, no orphaned threads._")
		lines.append("")
		return
	if o.git_uncommitted_count > 0:
		examples = ", ".join(f"`{e.strip()}`" for e in o.git_examples)
		more = f" … +{o.git_uncommitted_count - len(o.git_examples)} more" if o.git_uncommitted_count > len(o.git_examples) else ""
		lines.append(f"- **git:** {o.git_uncommitted_count} uncommitted file(s) ({examples}{more})")
	if o.git_ahead > 0:
		dest = o.git_upstream or "upstream"
		lines.append(f"- **git:** {o.git_ahead} commit(s) ahead of `{dest}`")
	if o.git_behind > 0:
		dest = o.git_upstream or "upstream"
		lines.append(f"- **git:** {o.git_behind} commit(s) behind `{dest}`")
	if o.plan_open_count > 0 or o.plan_open_phases > 0:
		ref = f"`{o.plan_doc_ref}`: " if o.plan_doc_ref else ""
		bits = []
		if o.plan_open_count > 0:
			bits.append(f"{o.plan_open_count} unchecked item(s)")
		if o.plan_open_phases > 0:
			bits.append(f"{o.plan_open_phases} open phase(s)")
		next_part = f"; next: _{o.plan_next}_" if o.plan_next else ""
		lines.append(f"- **plan:** {ref}{', '.join(bits)}{next_part}")
	if o.orphaned_thread_age_hours is not None:
		lines.append(f"- **thread:** last prompt {o.orphaned_thread_age_hours}h ago has no follow-up commit")
	lines.append("")


def _md_plan_docs(lines: list[str], report: ProjectReport) -> None:
	docs = report.recent(n=8, kinds=["doc"])
	rendered: set[str] = set()
	for path, summary in report.plan_summaries.items():
		when_sig = next((d for d in docs if d.ref == path), None)
		when = when_sig.timestamp.date().isoformat() if when_sig else ""
		bits = []
		if summary.total_phases > 0:
			bits.append(f"{summary.total_phases} phases, {summary.complete_phases} complete")
		if summary.total_items > 0:
			done = summary.total_items - summary.open_items
			bits.append(f"{done}/{summary.total_items} items")
		if summary.next_item:
			bits.append(f'next: _{_md_safe(_truncate(summary.next_item, limit=80))}_')
		when_str = f"`{when}` " if when else ""
		lines.append(f"- {when_str}**{path}** \u2014 {'; '.join(bits)}")
		rendered.add(path)
	for d in docs:
		if d.ref in rendered:
			continue
		_, rest = _split_doc_summary(d)
		clean = clean_doc_prose(rest) if rest else ""
		summary = first_sentence(clean, limit=_DOC_BULLET_LIMIT) if clean else ""
		suffix = f" \u2014 {_md_safe(summary)}" if summary else ""
		lines.append(f"- `{d.timestamp.date()}` **{d.ref}**{suffix}")


def _planning_doc_priority(path: str) -> int:
	base = path.rsplit("/", 1)[-1].lower()
	full = path.lower()
	if "/plans/" in full or base == "plan.md" or "plan" in base:
		return 100
	if "next_steps" in base or "next-steps" in base or base.startswith("next"):
		return 90
	if "roadmap" in base:
		return 80
	if "todo" in base:
		return 70
	return 0


def _best_plan_ref(report: ProjectReport) -> str:
	if report.plan_summaries:
		return max(report.plan_summaries, key=_planning_doc_priority)
	docs = [d.ref for d in report.recent(n=8, kinds=["doc"]) if d.ref and _planning_doc_priority(d.ref) > 0]
	return max(docs, key=_planning_doc_priority) if docs else ""


def _latest_substantive_prompt(report: ProjectReport) -> Signal | None:
	prompts = [p for p in report.recent(n=20, kinds=["prompt"]) if not is_procedural(p.summary)]
	return prompts[0] if prompts else None


def _purpose_ref(report: ProjectReport) -> str:
	obs = report.observations
	if obs is None:
		return ""
	for ev in obs.evidence:
		if ev.startswith("doc:"):
			return ev[len("doc:"):]
	return ""


def _source_guide_items(report: ProjectReport) -> list[tuple[str, str]]:
	obs = report.observations
	if obs is None:
		return []
	items: list[tuple[str, str]] = []
	plan_ref = _best_plan_ref(report) or obs.outstanding.plan_doc_ref
	if plan_ref:
		detail = ""
		summary = report.plan_summaries.get(plan_ref)
		if summary is not None and summary.next_item:

			detail = f"next: {_truncate(summary.next_item, limit=100)}"
		items.append(("Plan doc", f"`{plan_ref}`" + (f" — {detail}" if detail else "")))
	prompt = _latest_substantive_prompt(report)
	if prompt is not None:
		items.append(("Prompt thread", f"`{prompt.timestamp.date()}` `{prompt.source}` — {_truncate(_oneline(prompt.summary), limit=120)}"))
	commit = next(iter(report.recent(n=1, kinds=["commit"])), None)
	if commit is not None:
		items.append(("Git history", f"latest `{commit.timestamp.date()}` — {_truncate(_oneline(commit.summary), limit=120)}"))
	if obs.outstanding.git_uncommitted_count > 0:
		examples = ", ".join(f"`{e.strip()}`" for e in obs.outstanding.git_examples)
		more = f" … +{obs.outstanding.git_uncommitted_count - len(obs.outstanding.git_examples)} more" if obs.outstanding.git_uncommitted_count > len(obs.outstanding.git_examples) else ""
		items.append(("Working tree", f"{obs.outstanding.git_uncommitted_count} uncommitted file(s) ({examples}{more})"))
	identity_ref = _purpose_ref(report)
	if obs.purpose and identity_ref:
		items.append(("Project identity", f"`{identity_ref}` — {_truncate(first_sentence(obs.purpose, limit=120), limit=140)}"))
	return items


def _source_locator(report: ProjectReport) -> str:
	parts: list[str] = []
	plan_ref = _best_plan_ref(report) or (report.observations.outstanding.plan_doc_ref if report.observations else "")
	if plan_ref:
		parts.append(f"plan `{plan_ref}`")
	prompt = _latest_substantive_prompt(report)
	if prompt is not None:
		parts.append(f"prompt `{prompt.timestamp.date()}` (`{prompt.source}`)")
	commit = next(iter(report.recent(n=1, kinds=["commit"])), None)
	if commit is not None:
		parts.append(f"latest commit `{commit.timestamp.date()}`")
	if report.observations and report.observations.outstanding.git_uncommitted_count > 0:
		parts.append(f"working tree ({report.observations.outstanding.git_uncommitted_count} files)")
	return "; ".join(parts)


def _snapshot_line(report: ProjectReport) -> str:
	obs = report.observations
	if obs is None:
		return ""
	parts = [f"{progress_label(obs.progress)} · {_fmt_last_active(report.last_active)}"]
	git = _fmt_git(report, plain=True)
	if git:
		parts.append(git)
	parts.append(_fmt_sources(report))
	if obs.flags:
		parts.append(", ".join(obs.flags))
	return " | ".join(parts)


# ───── small formatting helpers ──────────────────────────────────────────────

def _truncate(text: str, *, limit: int) -> str:
	text = " ".join(text.split())
	if len(text) <= limit:
		return text
	return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _oneline(text: str) -> str:
	return " ".join(text.split())


def _split_doc_summary(s: Signal) -> tuple[str, str]:
	"""Pull `[<rel>]` prefix out of a docs Signal's summary."""
	if s.ref:
		body = s.summary
		if body.startswith("["):
			end = body.find("] ")
			if end != -1:
				body = body[end + 2 :]
		return s.ref, body
	if s.summary.startswith("["):
		end = s.summary.find("] ")
		if end != -1:
			return s.summary[1:end], s.summary[end + 2 :]
	return "doc", s.summary


def _has_followup_commit(prompt: Signal, commits_ts: list) -> bool:
	"""True if any commit landed strictly after this prompt's timestamp."""
	return any(ts > prompt.timestamp for ts in commits_ts)


def _outstanding_one_line(o: Outstanding) -> str:
	"""Compact one-line summary for the markdown handoff block."""
	parts = []
	if o.git_uncommitted_count > 0:
		parts.append(f"{o.git_uncommitted_count} uncommitted")
	if o.git_ahead > 0:
		parts.append(f"{o.git_ahead} ahead")
	if o.git_behind > 0:
		parts.append(f"{o.git_behind} behind")
	if o.plan_open_count > 0:
		parts.append(f"{o.plan_open_count} open plan items")
	elif o.plan_open_phases > 0:
		parts.append(f"{o.plan_open_phases} open phases")
	if o.orphaned_thread_age_hours is not None:
		parts.append(f"orphaned thread {o.orphaned_thread_age_hours}h")
	return "; ".join(parts)


def _outstanding_dict(o: Outstanding) -> dict:
	return {
		"git_uncommitted_count": o.git_uncommitted_count,
		"git_ahead": o.git_ahead,
		"git_behind": o.git_behind,
		"git_examples": list(o.git_examples),
		"git_upstream": o.git_upstream,
		"plan_open_count": o.plan_open_count,
		"plan_open_phases": o.plan_open_phases,
		"plan_next": o.plan_next,
		"plan_doc_ref": o.plan_doc_ref,
		"orphaned_thread_age_hours": o.orphaned_thread_age_hours,
		"is_empty": o.is_empty,
		"headline": o.headline,
	}


def _fmt_last_active(ts: datetime | None) -> str:
	if ts is None:
		return _DASH
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
	return "".join(flags) if flags else _DASH


def _fmt_git(r: ProjectReport, *, plain: bool = False) -> str:
	if not r.is_git_repo:
		return _DASH if not plain else ""
	label = r.git_branch or "(detached)"
	if r.git_dirty:
		label += "*"
	if plain:
		return label
	style = _git_style(r)
	return f"[{style}]{label}[/{style}]"


def _git_style(r: ProjectReport) -> str:
	if r.git_dirty:
		return "yellow"
	if r.git_ahead > 0 or r.git_behind > 0:
		return "cyan"
	return "green"


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


# Attention-based row coloring: clean Hot is calmer than Drifting + dirty.
_PROGRESS_BASE_STYLE = {
	"hot": "green",
	"active": "green",
	"paused": "yellow",
	"cooling": "cyan",
	"idle": "blue",
	"dormant": "dim",
	"shipped": "bright_green",
	"drifting": "bright_red",
	"tracking": "blue",
	"stub": "dim",
	"empty": "dim",
}


def _state_style(r: ProjectReport) -> str:
	"""Pick a row color from progress + outstanding pressure."""
	obs = r.observations
	if obs is None:
		return "dim"
	# Drifting always strong red — needs attention regardless of outstanding.
	if obs.progress is Progress.DRIFTING:
		return "bright_red"
	if obs.progress is Progress.PAUSED and obs.outstanding.git_uncommitted_count > 0:
		return "yellow"
	# Hot/Active with outstanding work get attention color
	if obs.progress in _BAND_ACTIVE and not obs.outstanding.is_empty:
		return "yellow"
	return _PROGRESS_BASE_STYLE.get(obs.progress.value, "white")


def _fmt_state(r: ProjectReport, *, plain: bool = False) -> str:
	"""State column: 'Hot · 35m', 'Drifting · 1d', 'Idle · 4w'."""
	obs = r.observations
	if obs is None:
		return _DASH
	label = progress_label(obs.progress)
	when = _fmt_last_active(r.last_active)
	# Drop the trailing " ago" to keep the column tight
	when_short = when.removesuffix(" ago") if when.endswith(" ago") else when
	combined = f"{label} \u00b7 {when_short}"
	if plain:
		return combined
	style = _state_style(r)
	return f"[{style}]{combined}[/{style}]"


def _fmt_outstanding_cell(obs) -> str:
	if obs is None:
		return _DASH
	o = obs.outstanding
	if o.is_empty:
		return f"[dim]{_DASH}[/dim]"
	label = o.headline
	# Color by severity: drifting/dirty=red, plan/orphan=yellow, ahead=cyan
	if o.git_uncommitted_count > 0:
		return f"[yellow]{label}[/yellow]"
	if o.git_behind > 0:
		return f"[red]{label}[/red]"
	if o.git_ahead > 0:
		return f"[cyan]{label}[/cyan]"
	if o.plan_open_count > 0 or o.plan_open_phases > 0:
		return f"[blue]{label}[/blue]"
	return label


def _progress_text(obs) -> str:
	label = progress_label(obs.progress)
	style = _PROGRESS_BASE_STYLE.get(obs.progress.value, "white")
	return f"[{style}]{label}[/{style}]"
