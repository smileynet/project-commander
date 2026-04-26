"""Render `ProjectReport` collections to terminal, markdown, or JSON.

The renderers are organized around what the reader is trying to do:

- `render_table`             fleet check-in: one row per project, grouped by attention band
- `render_review`            period digest: short triage list for "what should I do this week?"
- `render_detail`            project briefing card answering four questions:
                             What is it? What's been happening? Where does it stand? What's planned?

Plus markdown / JSON serializers. The markdown variants are the canonical
shareable form; the terminal variants are styled equivalents.

The detail and review renderers deliberately avoid evidence dumps. They lead
with synthesis (what the reader needs to decide) and end with a one-line
pointer back to the underlying source files for deeper inspection.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from .models import ProjectReport, Signal
from .observations import (
	Observations,
	Outstanding,
	Progress,
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

_INTENT_LIMIT = 180
_PURPOSE_LIMIT = 320
_RECENT_LIMIT = 240
_STANDS_LIMIT = 320
_PLAN_LIMIT = 320

_REVIEW_SECTION_CAP = 8


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


# ───── period digest (week-in-review) ────────────────────────────────────────

def render_review(reports: Iterable[ProjectReport], *, since_days: int, console: Console) -> None:
	"""Period digest, terminal styling. Three sections, one line per project."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = _cutoff(now, since_days)
	sections = _review_sections(rows, cutoff=cutoff)
	totals = _review_totals_str(rows, cutoff=cutoff)
	console.print()
	console.print(Rule(f"[bold]Last {since_days} day(s)[/bold]", style="cyan"))
	subtitle = f"since {cutoff.date().isoformat()}"
	if totals:
		subtitle = f"{subtitle}  ·  {totals}"
	console.print(f"[dim]{subtitle}[/dim]")
	if not any(entries for _, _, entries in sections):
		console.print()
		console.print("  [dim](nothing moved, nothing needs attention)[/dim]")
		return
	for title, blurb, entries in sections:
		if not entries:
			continue
		_print_review_section(console, title, blurb, entries)


def render_review_markdown(reports: Iterable[ProjectReport], *, since_days: int) -> str:
	"""Period digest, markdown form. Mirror of `render_review`."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = _cutoff(now, since_days)
	sections = _review_sections(rows, cutoff=cutoff)
	totals = _review_totals_str(rows, cutoff=cutoff)
	subtitle = f"_Since {cutoff.date().isoformat()}_"
	if totals:
		subtitle = f"_Since {cutoff.date().isoformat()} · {totals}_"
	lines = [f"# Last {since_days} day(s)", "", subtitle, ""]
	if not any(entries for _, _, entries in sections):
		lines.append("_Nothing moved, nothing needs attention._")
		return "\n".join(lines).rstrip() + "\n"
	for title, blurb, entries in sections:
		if not entries:
			continue
		lines.append(f"## {title} ({len(entries)})")
		lines.append("")
		if blurb:
			lines.append(f"_{blurb}_")
			lines.append("")
		for entry in entries[:_REVIEW_SECTION_CAP]:
			lines.append(_review_row_md(entry))
		if len(entries) > _REVIEW_SECTION_CAP:
			lines.append(f"- _… +{len(entries) - _REVIEW_SECTION_CAP} more_")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── per-project briefing card ─────────────────────────────────────────────

def render_detail(report: ProjectReport, console: Console) -> None:
	"""Project briefing card: 4 narrative answers, 1 footer line of source pointers."""
	obs = report.observations
	console.rule(f"[bold cyan]{report.name}")
	console.print(f"[dim]{report.path}[/dim]")
	if obs is None:
		console.print()
		console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
		return
	console.print()
	console.print(_status_header_term(report, obs))
	console.print()
	_print_section(console, "What is it?", _what_is_it(report, obs))
	_print_section(console, "What's been happening?", _whats_been_happening(report, obs))
	_print_section(console, "Where it stands", _where_it_stands(report, obs))
	planned = _whats_planned_next(report, obs)
	first = obs.next_action
	console.print("[bold cyan]What's planned next[/bold cyan]")
	console.print(f"  {planned}")
	if first:
		console.print()
		console.print(f"  [bold green]Your first action:[/bold green] {first}")
	console.print()
	footer = _inspect_footer(report, obs)
	if footer:
		console.print(f"[dim]Inspect: {footer}[/dim]")


def render_detail_markdown(report: ProjectReport) -> str:
	"""Markdown briefing card: 4 narrative sections, footer pointer line."""
	obs = report.observations
	lines: list[str] = [f"# {report.name}", "", f"`{report.path}`", ""]
	if obs is None:
		lines.append(f"_Intent: {report.intent or '(none)'}_")
		return "\n".join(lines).rstrip() + "\n"
	lines.append(_status_header_md(report, obs))
	lines.append("")
	lines.append("### What is it?")
	lines.append("")
	lines.append(_md_safe(_what_is_it(report, obs)))
	lines.append("")
	lines.append("### What's been happening?")
	lines.append("")
	lines.append(_md_safe(_whats_been_happening(report, obs)))
	lines.append("")
	lines.append("### Where it stands")
	lines.append("")
	lines.append(_md_safe(_where_it_stands(report, obs)))
	lines.append("")
	lines.append("### What's planned next")
	lines.append("")
	lines.append(_md_safe(_whats_planned_next(report, obs)))
	if obs.next_action:
		lines.append("")
		lines.append(f"**Your first action:** {_md_safe(obs.next_action)}")
	lines.append("")
	footer = _inspect_footer(report, obs)
	if footer:
		lines.append("---")
		lines.append("")
		lines.append(f"<sub>Inspect: {_md_safe(footer)}</sub>")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── tabular renderers (unchanged shape — fleet table + JSON) ──────────────

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


# ───── fleet table internals (existing — unchanged behavior) ─────────────────

_BAND_ACTIVE = {Progress.HOT, Progress.ACTIVE}
_BAND_ATTN = {Progress.DRIFTING, Progress.PAUSED}


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
	attn.sort(key=lambda r: _attn_priority(r))
	return [
		("Needs attention", attn),
		("Active", active),
		("Quiet", quiet),
	]


def _attn_priority(r: ProjectReport) -> tuple[int, float]:
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


# ───── period digest internals ───────────────────────────────────────────────

# Section descriptors: (title, blurb-for-reader)
_SECTION_BLURB = {
	"Needs your attention": "These have a clear next move. Pick one and finish it.",
	"New this week": "Repos that landed in your worktrees for the first time.",
	"Moved this week": "Quiet activity — commits, prompts, or upstream sync.",
}


def _cutoff(now: datetime, since_days: int) -> datetime:
	midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
	return midnight - timedelta(days=since_days)


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


def _review_totals_str(rows: Sequence[ProjectReport], *, cutoff: datetime) -> str:
	active = 0
	commits = 0
	prompts = 0
	for r in rows:
		c, p = _review_counts(r, cutoff=cutoff)
		if c or p:
			active += 1
		commits += c
		prompts += p
	if not (active or commits or prompts):
		return ""
	bits = [f"{active} active project(s)"]
	if commits:
		bits.append(f"{commits} commit(s)")
	if prompts:
		bits.append(f"{prompts} substantive prompt(s)")
	return " · ".join(bits)


def _review_outcome_text(text: str) -> str:
	"""Strip lead-in phrases so we can present a clean clause."""
	text = _truncate(_oneline(text), limit=200).rstrip(".")
	for prefix in (
		"Recent commits focused on ",
		"Recent commit: ",
		"Latest concrete action: ",
	):
		if text.startswith(prefix):
			text = text[len(prefix):]
			break
	# Conventional-commit prefixes ('feat: foo bar') and verb-stripping in
	# observations sometimes leave the lead clause lowercase. Capitalize so the
	# bullet reads as a sentence.
	if text and text[0].islower():
		text = text[0].upper() + text[1:]
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


def _review_age_hint(report: ProjectReport) -> str:
	if report.last_active is None:
		return ""
	return _fmt_last_active(report.last_active)


def _classify_review(report: ProjectReport, *, cutoff: datetime) -> str | None:
	"""Bucket key for this project: 'attention', 'new', 'moved', or None."""
	obs = report.observations
	if obs is None:
		return None
	c, p = _review_counts(report, cutoff=cutoff)
	first_commit = _review_first_commit(report)
	is_new = first_commit is not None and first_commit >= cutoff
	o = obs.outstanding
	has_action = bool(
		o.git_uncommitted_count > 0
		or o.git_ahead > 0
		or o.git_behind > 0
		or o.orphaned_thread_age_hours is not None
		or "plan-drift" in obs.flags
		or (not report.is_git_repo and report.signals)
	)
	if not (c or p or has_action or is_new):
		return None
	if has_action:
		return "attention"
	if is_new:
		return "new"
	return "moved"


def _review_badges(report: ProjectReport, *, cutoff: datetime) -> tuple[str, ...]:
	obs = report.observations
	badges: list[str] = []
	first_commit = _review_first_commit(report)
	if first_commit is not None and first_commit >= cutoff:
		badges.append("new")
	if obs is not None:
		o = obs.outstanding
		if o.git_uncommitted_count > 0:
			badges.append("dirty")
		if o.git_behind > 0:
			badges.append("behind")
		elif o.git_ahead > 0:
			badges.append("ahead")
		if o.orphaned_thread_age_hours is not None:
			badges.append("orphan")
		if "plan-drift" in obs.flags:
			badges.append("drift")
		if not report.is_git_repo and report.signals:
			badges.append("no-git")
	return tuple(badges)


def _review_entry(report: ProjectReport, *, cutoff: datetime) -> dict | None:
	bucket = _classify_review(report, cutoff=cutoff)
	if bucket is None:
		return None
	obs = report.observations
	assert obs is not None  # _classify_review returns None if obs is None
	commits, prompts = _review_counts(report, cutoff=cutoff)
	last_ts = report.last_active.timestamp() if report.last_active else 0.0
	# Sort priority within "attention" bucket: drift > behind > dirty > ahead > orphan > plan-next
	priority = 9
	o = obs.outstanding
	if "plan-drift" in obs.flags:
		priority = 0
	elif o.git_behind > 0:
		priority = 1
	elif o.git_uncommitted_count > 0:
		priority = 2
	elif o.git_ahead > 0:
		priority = 3
	elif o.orphaned_thread_age_hours is not None:
		priority = 4
	elif not report.is_git_repo and report.signals:
		priority = 5
	if bucket == "attention":
		sort_key = (priority, -last_ts)
	elif bucket == "new":
		sort_key = (-last_ts, -(commits + prompts))
	else:
		sort_key = (-(commits + prompts), -last_ts)
	first_commit = _review_first_commit(report)
	return {
		"name": report.name,
		"bucket": bucket,
		"action": obs.next_action,
		"outcome": _review_outcome(report),
		"badges": _review_badges(report, cutoff=cutoff),
		"age": _review_age_hint(report),
		"started": first_commit.strftime("%a") if first_commit and bucket == "new" else "",
		"signals": (commits, prompts),
		"sort_key": sort_key,
	}


def _review_sections(rows: Sequence[ProjectReport], *, cutoff: datetime) -> list[tuple[str, str, list[dict]]]:
	buckets: dict[str, list[dict]] = {"attention": [], "new": [], "moved": []}
	for report in rows:
		entry = _review_entry(report, cutoff=cutoff)
		if entry is None:
			continue
		buckets[entry["bucket"]].append(entry)
	for entries in buckets.values():
		entries.sort(key=lambda item: item["sort_key"])
	titles = [
		("Needs your attention", "attention"),
		("New this week", "new"),
		("Moved this week", "moved"),
	]
	return [(title, _SECTION_BLURB.get(title, ""), buckets[key]) for title, key in titles]


def _review_row_md(entry: dict) -> str:
	"""Render one project as a single markdown bullet."""
	name = _md_safe(str(entry["name"]))
	bucket = entry["bucket"]
	if bucket == "attention":
		lead = entry["action"] or entry["outcome"] or "Review project state."
		hint_bits: list[str] = []
		if entry["age"]:
			hint_bits.append(entry["age"])
		badges = [b for b in entry["badges"] if b != "new"]
		if badges:
			hint_bits.append(", ".join(badges))
		hint = f" _{' · '.join(hint_bits)}._" if hint_bits else ""
		return f"- **{name}** — {_md_safe(_review_sentence(lead))}{hint}"
	if bucket == "new":
		started = f" _({entry['started']})_" if entry["started"] else ""
		outcome = entry["outcome"] or "Initial commits landed."
		return f"- **{name}**{started} — {_md_safe(_review_sentence(outcome))}"
	# moved
	outcome = entry["outcome"] or "Quiet activity."
	hint = f" _{entry['age']}._" if entry["age"] else ""
	return f"- **{name}** — {_md_safe(_review_sentence(outcome))}{hint}"


def _review_sentence(text: str) -> str:
	text = text.strip()
	if not text:
		return "(no summary)"
	if text.endswith((".", "!", "?", "…")):
		return text
	return text + "."


def _print_review_section(console: Console, title: str, blurb: str, entries: list[dict]) -> None:
	count = len(entries)
	console.print()
	console.print(f"[bold]{title}[/bold] [dim]({count})[/dim]")
	if blurb:
		console.print(f"[dim]{blurb}[/dim]")
	for entry in entries[:_REVIEW_SECTION_CAP]:
		bucket = entry["bucket"]
		if bucket == "attention":
			lead = entry["action"] or entry["outcome"] or "Review project state."
			tail_bits: list[str] = []
			if entry["age"]:
				tail_bits.append(entry["age"])
			badges = [b for b in entry["badges"] if b != "new"]
			if badges:
				tail_bits.append(", ".join(badges))
			tail = f"  [dim]({' · '.join(tail_bits)})[/dim]" if tail_bits else ""
			console.print(f"  [cyan]{entry['name']}[/cyan]  [white]{_review_sentence(lead)}[/white]{tail}")
		elif bucket == "new":
			outcome = entry["outcome"] or "Initial commits landed."
			started = f"  [dim]({entry['started']})[/dim]" if entry["started"] else ""
			console.print(f"  [cyan]{entry['name']}[/cyan]{started}  [dim]{_review_sentence(outcome)}[/dim]")
		else:
			outcome = entry["outcome"] or "Quiet activity."
			tail = f"  [dim]({entry['age']})[/dim]" if entry["age"] else ""
			console.print(f"  [cyan]{entry['name']}[/cyan]  [dim]{_review_sentence(outcome)}[/dim]{tail}")
	if count > _REVIEW_SECTION_CAP:
		console.print(f"  [dim]… +{count - _REVIEW_SECTION_CAP} more[/dim]")


# ───── briefing card synthesis ───────────────────────────────────────────────

def _status_header_md(report: ProjectReport, obs: Observations) -> str:
	bits = [f"**{progress_label(obs.progress)}**", f"last touched {_fmt_last_active(report.last_active)}"]
	if report.is_git_repo:
		branch = (report.git_branch or "(detached)") + ("*" if report.git_dirty else "")
		bits.append(f"`{branch}`")
	tail = _outstanding_short(obs.outstanding)
	if tail:
		bits.append(tail)
	return " · ".join(bits)


def _status_header_term(report: ProjectReport, obs: Observations):
	"""Same content as `_status_header_md` but with rich styling."""
	state_color = _PROGRESS_BASE_STYLE.get(obs.progress.value, "white")
	bits = [f"[bold {state_color}]{progress_label(obs.progress)}[/bold {state_color}]",
	        f"last touched {_fmt_last_active(report.last_active)}"]
	if report.is_git_repo:
		branch = (report.git_branch or "(detached)") + ("*" if report.git_dirty else "")
		git_color = _git_style(report)
		bits.append(f"[{git_color}]{branch}[/{git_color}]")
	tail = _outstanding_short(obs.outstanding)
	if tail:
		bits.append(f"[yellow]{tail}[/yellow]")
	return "  ·  ".join(bits)


def _outstanding_short(o: Outstanding) -> str:
	"""A short tail clause for the status header (or empty if clean)."""
	if o.git_uncommitted_count > 0:
		return f"{o.git_uncommitted_count} uncommitted file(s)"
	if o.git_ahead > 0:
		return f"{o.git_ahead} unpushed commit(s)"
	if o.git_behind > 0:
		return f"{o.git_behind} commit(s) to pull"
	if o.plan_open_count > 0:
		return f"{o.plan_open_count} plan item(s) open"
	if o.orphaned_thread_age_hours is not None:
		return f"{o.orphaned_thread_age_hours}h orphan thread"
	return ""


def _what_is_it(report: ProjectReport, obs: Observations) -> str:
	if obs.purpose:
		return first_sentence(obs.purpose, limit=_PURPOSE_LIMIT)
	if not report.signals and not report.is_git_repo:
		return "Empty folder — no description, no git history."
	return "_(no description doc found — README/ROADMAP/AGENTS not present)_"


def _whats_been_happening(report: ProjectReport, obs: Observations) -> str:
	# Prefer recent commits synthesis (covers active and just-paused projects).
	if obs.recent_changes:
		# `recent_changes` already reads as a sentence ("Recent commits focused on …").
		return obs.recent_changes
	# Past-week prompt activity but no commits.
	w7 = obs.window_7d
	if w7.prompts > 0 and w7.commits == 0:
		return f"{w7.prompts} prompt(s) this week but no commits yet."
	# Older activity: quote the last concrete action.
	if obs.last_action and obs.last_action_at:
		when = _fmt_last_active(obs.last_action_at)
		return f"No commits in the last week. Last activity: {obs.last_action} ({when})."
	if obs.focus:
		return f"Latest prompt: {first_sentence(obs.focus, limit=_RECENT_LIMIT)}"
	return "_No activity observed in the last week._"


def _where_it_stands(report: ProjectReport, obs: Observations) -> str:
	o = obs.outstanding
	parts: list[str] = []
	if "plan-drift" in obs.flags:
		ref = o.plan_doc_ref or "the plan doc"
		parts.append(f"`{ref}` says complete, but commits have continued to land since.")
	if o.git_uncommitted_count > 0:
		parts.append(f"Working tree has {o.git_uncommitted_count} uncommitted file(s).")
	if o.git_ahead > 0:
		dest = o.git_upstream or "upstream"
		parts.append(f"{o.git_ahead} local commit(s) still need to reach `{dest}`.")
	if o.git_behind > 0:
		dest = o.git_upstream or "upstream"
		parts.append(f"{o.git_behind} commit(s) waiting on `{dest}` to be pulled.")
	if o.orphaned_thread_age_hours is not None:
		parts.append(f"Last substantive prompt was {o.orphaned_thread_age_hours}h ago with no follow-up commit.")
	if not report.is_git_repo and report.signals:
		parts.append("Folder has activity history but no git boundary yet.")
	if parts:
		return " ".join(parts)
	# Clean states
	if obs.progress is Progress.SHIPPED:
		return "Clean checkpoint — plan declares complete and the working tree is clean."
	if obs.progress is Progress.DORMANT:
		return f"Dormant. {obs.progress_summary}"
	if obs.progress is Progress.IDLE:
		return f"Idle. Nothing outstanding; last touched {_fmt_last_active(report.last_active)}."
	if obs.progress is Progress.STUB:
		return "Documentation only — nothing committed and no prompts logged."
	if obs.progress is Progress.EMPTY:
		return "Empty folder — no signals at all."
	if obs.progress is Progress.TRACKING:
		return "Only upstream-style commits land here; no prompts of your own."
	return "Nothing outstanding right now."


def _whats_planned_next(report: ProjectReport, obs: Observations) -> str:
	o = obs.outstanding
	# Drift takes precedence — the plan needs reconciliation, not pursuit.
	if "plan-drift" in obs.flags:
		ref = o.plan_doc_ref or "the plan doc"
		return f"Reconcile `{ref}` before proceeding — the documented completion state and the actual code history have diverged."
	# Workstream is forward-looking when it came from a plan doc or an open prompt.
	# When it falls back to `recent_changes`, it duplicates the previous section,
	# so we drop it and let the caller fall through to a 'no plan' framing.
	workstream = obs.workstream
	if workstream and workstream != obs.recent_changes:
		ref = o.plan_doc_ref or _best_plan_ref(report)
		body = workstream.rstrip()
		if not body.endswith((".", "!", "?", "\u2026")):
			body = body + "."
		if ref and ref not in body:
			return f"Per `{ref}`: {body}"
		return body
	if o.plan_next:
		ref = o.plan_doc_ref
		if ref:
			return f"Per `{ref}`: next item is _{o.plan_next}_."
		return f"Next plan item: _{o.plan_next}_."
	if obs.progress is Progress.SHIPPED:
		return "No active plan — project is shipped and clean."
	if obs.progress in (Progress.DORMANT, Progress.IDLE):
		return "No active plan — archive or revisit when a use case comes up."
	if obs.progress is Progress.EMPTY:
		return "No plan — folder is empty."
	return "_No plan doc found — start one if you want to track work here._"


# ───── inspect footer (single-line locator) ──────────────────────────────────

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


def _inspect_footer(report: ProjectReport, obs: Observations) -> str:
	"""Single-line list of source pointers for further inspection."""
	parts: list[str] = []
	plan_ref = _best_plan_ref(report) or obs.outstanding.plan_doc_ref
	if plan_ref:
		parts.append(f"plan `{plan_ref}`")
	commit = next(iter(report.recent(n=1, kinds=["commit"])), None)
	if commit is not None:
		parts.append(f"last commit `{commit.timestamp.date()}`")
	prompt = _latest_substantive_prompt(report)
	if prompt is not None:
		parts.append(f"last prompt `{prompt.timestamp.date()}` (`{prompt.source}`)")
	identity_ref = _purpose_ref(report)
	if identity_ref and identity_ref != plan_ref:
		parts.append(f"identity `{identity_ref}`")
	if obs.outstanding.git_uncommitted_count > 0:
		parts.append(f"working tree ({obs.outstanding.git_uncommitted_count} files)")
	return " · ".join(parts)


def _print_section(console: Console, title: str, body: str) -> None:
	console.print(f"[bold cyan]{title}[/bold cyan]")
	console.print(f"  {body}")
	console.print()


# ───── small formatting helpers ──────────────────────────────────────────────

def _truncate(text: str, *, limit: int) -> str:
	text = " ".join(text.split())
	if len(text) <= limit:
		return text
	return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _oneline(text: str) -> str:
	return " ".join(text.split())


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
	obs = r.observations
	if obs is None:
		return "dim"
	if obs.progress is Progress.DRIFTING:
		return "bright_red"
	if obs.progress is Progress.PAUSED and obs.outstanding.git_uncommitted_count > 0:
		return "yellow"
	if obs.progress in _BAND_ACTIVE and not obs.outstanding.is_empty:
		return "yellow"
	return _PROGRESS_BASE_STYLE.get(obs.progress.value, "white")


def _fmt_state(r: ProjectReport, *, plain: bool = False) -> str:
	obs = r.observations
	if obs is None:
		return _DASH
	label = progress_label(obs.progress)
	when = _fmt_last_active(r.last_active)
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
	if o.git_uncommitted_count > 0:
		return f"[yellow]{label}[/yellow]"
	if o.git_behind > 0:
		return f"[red]{label}[/red]"
	if o.git_ahead > 0:
		return f"[cyan]{label}[/cyan]"
	if o.plan_open_count > 0 or o.plan_open_phases > 0:
		return f"[blue]{label}[/blue]"
	return label
