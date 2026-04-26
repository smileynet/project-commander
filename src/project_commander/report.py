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
	"""Period-in-review: 'what moved / what stalled / what was flagged / what's new'."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
	from datetime import timedelta
	cutoff = cutoff - timedelta(days=since_days)
	header = f"Last {since_days} day(s) \u2014 since {cutoff.date().isoformat()}"
	console.print()
	console.print(Rule(f"[bold]{header}[/bold]", style="cyan"))
	moved = _section_moved_forward(rows, cutoff=cutoff)
	parked = _section_parked_dirty(rows)
	flagged = _section_flagged(rows)
	new = _section_new_this_period(rows, cutoff=cutoff)
	any_section = False
	if moved:
		_print_review_section(console, "Moved forward", moved, columns=("name", "stats", "focus"))
		any_section = True
	if parked:
		_print_review_section(console, "Parked dirty (decide)", parked, columns=("name", "dirt", "idle"))
		any_section = True
	if flagged:
		_print_review_section(console, "Flagged", flagged, columns=("name", "flag", "detail"))
		any_section = True
	if new:
		_print_review_section(console, "New this period", new, columns=("name", "stats", "focus"))
		any_section = True
	if not any_section:
		console.print()
		console.print("  [dim](nothing moved, nothing parked, nothing flagged)[/dim]")


def render_review_markdown(reports: Iterable[ProjectReport], *, since_days: int) -> str:
	"""Markdown week-in-review digest tuned for cross-project triage."""
	rows = list(reports)
	now = datetime.now(tz=timezone.utc)
	cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
	from datetime import timedelta
	cutoff = cutoff - timedelta(days=since_days)
	sections = [
		("Moved forward", _section_moved_forward(rows, cutoff=cutoff), "moved"),
		("Parked dirty (decide)", _section_parked_dirty(rows), "parked"),
		("Flagged", _section_flagged(rows), "flagged"),
		("New this period", _section_new_this_period(rows, cutoff=cutoff), "new"),
	]
	lines = [f"# Last {since_days} day(s)", "", f"_Since {cutoff.date().isoformat()}_", ""]
	if not any(items for _, items, _ in sections):
		lines.append("_Nothing moved, nothing parked, nothing flagged._")
		return "\n".join(lines).rstrip() + "\n"
	lookup = {r.name: r for r in rows}
	for title, items, kind in sections:
		if not items:
			continue
		lines.append(f"## {title} ({len(items)})")
		lines.append("")
		for row in items[:_REVIEW_SECTION_CAP]:
			lines.extend(_review_markdown_entry(kind, row, lookup.get(row[0])))
		if len(items) > _REVIEW_SECTION_CAP:
			lines.append(f"- _… +{len(items) - _REVIEW_SECTION_CAP} more_")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


def render_detail(report: ProjectReport, console: Console) -> None:
	obs = report.observations
	console.rule(f"[bold cyan]{report.name}")
	console.print(f"[dim]{report.path}[/dim]")
	console.print(f"State: {_fmt_state(report)}")
	console.print(f"Git: {_fmt_git(report)}    Sources: {_fmt_sources(report)}")
	console.print()
	if obs is None:
		console.print(f"[bold]Intent:[/bold] {report.intent or '(none)'}")
		return

	console.print(f"[bold]Progress:[/bold] {_progress_text(obs)} — {obs.progress_summary}")
	workstream = _truncate(_oneline(obs.workstream), limit=_FOCUS_LIMIT) if obs.workstream else ""
	purpose = first_sentence(obs.purpose, limit=_PURPOSE_LIMIT) if obs.purpose else ""
	if workstream:
		console.print(f"[bold]Workstream:[/bold] {workstream}")
	if obs.attention:
		console.print(f"[bold]Attention:[/bold] {_truncate(_oneline(obs.attention), limit=220)}")
	if obs.focus:
		console.print(f"[bold]Current thread:[/bold] {_truncate(_oneline(obs.focus), limit=_FOCUS_LIMIT)}")
	else:
		console.print("[bold]Current thread:[/bold] [dim](no recent prompts)[/dim]")
	if purpose and _oneline(purpose) != _oneline(workstream):
		console.print(f"[bold]Purpose:[/bold] {purpose}")
	if obs.last_action:
		when = _fmt_last_active(obs.last_action_at)
		console.print(f"[bold]Last action:[/bold] {_oneline(obs.last_action)} [dim]({when}, {obs.last_action_source})[/dim]")
	if obs.flags:
		console.print("[bold]Flags:[/bold] " + " ".join(f"[yellow]{f}[/yellow]" for f in obs.flags))
	w7, w30 = obs.window_7d, obs.window_30d
	console.print(
		f"[dim]Activity: 7d → {w7.commits}c / {w7.prompts}p across {w7.distinct_days} day(s); "
		f"30d → {w30.commits}c / {w30.prompts}p[/dim]"	)
	if obs.evidence:
		console.print("[dim]Evidence: " + "; ".join(obs.evidence) + "[/dim]")
	console.print()

	_terminal_outstanding_section(console, report, obs.outstanding)
	if obs.next_action:
		console.print(f"[bold green]Next:[/bold green] {obs.next_action}")
		console.print()

	_terminal_section(console, "Recent commits", report.recent(n=10, kinds=["commit"]))
	_terminal_prompts_section(console, report)
	_terminal_section(console, "Sessions", report.recent(n=6, kinds=["session"]))
	_terminal_plan_docs_section(console, report)


def render_detail_markdown(report: ProjectReport) -> str:
	"""Self-contained markdown writeup for a single project, lead with handoff block."""
	obs = report.observations
	lines: list[str] = []
	lines.append(f"# {report.name}")
	lines.append("")
	lines.append(f"`{report.path}`")
	lines.append("")

	# Pick up where you left off — synthesized handoff at the very top.
	if obs is not None:
		lines.append("> **Pick up where you left off.**  ")
		lines.append(f"> **State:** {progress_label(obs.progress)} · {_fmt_last_active(report.last_active)}.  ")
		if obs.workstream:
			lines.append(f"> **Workstream:** {_md_safe(_truncate(_oneline(obs.workstream), limit=180))}  ")
		if obs.attention:
			lines.append(f"> **Attention:** {_md_safe(_truncate(_oneline(obs.attention), limit=200))}  ")
		out_summary = _outstanding_one_line(obs.outstanding)
		if out_summary:
			lines.append(f"> **Outstanding:** {out_summary}.  ")
		else:
			lines.append("> **Outstanding:** clean (no uncommitted work, no unpushed commits).  ")
		if obs.next_action:
			lines.append(f"> **Next:** {_md_safe(obs.next_action)}")
		lines.append("")

	lines.append(f"- **Last active:** {_fmt_last_active(report.last_active)}")
	if obs is not None:
		lines.append(f"- **Progress:** {progress_label(obs.progress)} — {obs.progress_summary}")
	lines.append(f"- **Git:** {_fmt_git(report, plain=True) or _DASH}")
	lines.append(f"- **Sources:** {_fmt_sources(report)}")
	if obs is not None and obs.flags:
		lines.append("- **Flags:** " + ", ".join(f"`{f}`" for f in obs.flags))
	lines.append("")

	if obs is not None:
		workstream = _truncate(_oneline(obs.workstream), limit=220) if obs.workstream else ""
		purpose = first_sentence(obs.purpose, limit=_PURPOSE_LIMIT) if obs.purpose else ""
		if workstream:
			lines.append("## Workstream")
			lines.append("")
			lines.append(_md_safe(workstream))
			lines.append("")
		if obs.focus:
			lines.append("## Current thread")
			lines.append("")
			lines.append(_md_safe(_truncate(_oneline(obs.focus), limit=_FOCUS_LIMIT)))
			lines.append("")
		if purpose and _oneline(purpose) != _oneline(workstream):
			lines.append("## Purpose")
			lines.append("")
			lines.append(_md_safe(purpose))
			lines.append("")
		if obs.attention:
			lines.append("## Attention now")
			lines.append("")
			lines.append(_md_safe(_truncate(_oneline(obs.attention), limit=220)))
			lines.append("")
		if obs.last_action:
			when = _fmt_last_active(obs.last_action_at)
			lines.append(
				f"**Last action:** {_oneline(obs.last_action)}  "
				f"<sub>{when} · `{obs.last_action_source}`</sub>"
			)
			lines.append("")

		# Outstanding block — explicit even when clean
		_md_outstanding_block(lines, report, obs.outstanding)

		w7, w30, w90 = obs.window_7d, obs.window_30d, obs.window_90d
		lines.append("## Activity")
		lines.append("")
		lines.append("| Window | Commits | Prompts | Sessions | Active days |")
		lines.append("| --- | ---: | ---: | ---: | ---: |")
		for label, w in [("7d", w7), ("30d", w30), ("90d", w90)]:
			lines.append(f"| {label} | {w.commits} | {w.prompts} | {w.sessions} | {w.distinct_days} |")
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
		commits_set = sorted([s.timestamp for s in report.signals if s.kind == "commit"], reverse=True)
		for p in subst[:5]:
			body = _truncate(_oneline(p.summary), limit=200)
			marker = " ~" if not _has_followup_commit(p, commits_set) else ""
			lines.append(f"- `{p.timestamp.date()}`{marker} {_md_safe(body)} <sub>(`{p.source}`)</sub>")
		lines.append("")
		if any(not _has_followup_commit(p, commits_set) for p in subst[:5]):
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

	sessions = report.recent(n=6, kinds=["session"])
	if sessions:
		lines.append("## Sessions")
		lines.append("")
		for s in sessions:
			body = _truncate(_oneline(s.summary), limit=120)
			lines.append(f"- `{s.timestamp.date()}` {_md_safe(body)} <sub>(`{s.source}`)</sub>")
		lines.append("")

	if report.plan_summaries or report.recent(n=8, kinds=["doc"]):
		lines.append("## Plan docs")
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

def _section_moved_forward(rows: Sequence[ProjectReport], *, cutoff: datetime) -> list[tuple]:
	"""Projects with at least one commit OR substantive prompt in the window."""
	out: list[tuple] = []
	for r in rows:
		obs = r.observations
		if obs is None:
			continue
		commits = sum(1 for s in r.signals if s.kind == "commit" and s.timestamp >= cutoff)
		prompts = sum(1 for s in r.signals if s.kind == "prompt" and s.timestamp >= cutoff
		              and not is_procedural(s.summary))
		if commits == 0 and prompts == 0:
			continue
		focus = obs.recent_changes or obs.workstream or obs.focus or obs.last_action or obs.purpose
		out.append((r.name, f"{commits}c {prompts}p", _truncate(_oneline(focus or ''), limit=80)))
	out.sort(key=lambda t: -(int(t[1].split('c')[0]) + int(t[1].split('c')[1].split('p')[0])))
	return out


def _section_parked_dirty(rows: Sequence[ProjectReport]) -> list[tuple]:
	"""Projects with uncommitted work or pre-git content sitting idle."""
	now = datetime.now(tz=timezone.utc)
	out: list[tuple] = []
	for r in rows:
		obs = r.observations
		if obs is None:
			continue
		count = obs.outstanding.git_uncommitted_count
		if count > 0:
			idle_days = (now - r.last_active).days if r.last_active else 0
			# Only surface as "parked" when idle ≥ 2 days; today's dirty trees are normal.
			if idle_days < 2:
				continue
			out.append((r.name, f"dirty {count}f", f"idle {idle_days}d"))
		elif not r.is_git_repo and r.signals:
			# folder has signals but no git
			idle_days = (now - r.last_active).days if r.last_active else 0
			out.append((r.name, "no-git", f"idle {idle_days}d"))
	out.sort(key=lambda t: -int(t[2].split()[1].rstrip("d")))
	return out


def _section_flagged(rows: Sequence[ProjectReport]) -> list[tuple]:
	"""Projects with attention-worthy flags."""
	priority = ("plan-drift", "prompt-injection-detected", "upstream-only", "tool-cluster")
	out: list[tuple] = []
	for r in rows:
		obs = r.observations
		if obs is None:
			continue
		hit = next((f for f in priority if f in obs.flags), None)
		if not hit:
			continue
		detail = ""
		if hit == "plan-drift":
			# pull the count from the evidence line, e.g. "plan-drift: doc says complete, 5 commits since"
			for ev in obs.evidence:
				if ev.startswith("plan-drift:"):
					detail = ev[len("plan-drift: "):]
					break
		elif hit == "tool-cluster":
			detail = f"{len(r.sources_active)} tools"
		out.append((r.name, hit, detail))
	return out


def _section_new_this_period(rows: Sequence[ProjectReport], *, cutoff: datetime) -> list[tuple]:
	"""Projects whose first observed commit is within the window."""
	out: list[tuple] = []
	for r in rows:
		obs = r.observations
		if obs is None:
			continue
		commits = [s for s in r.signals if s.kind == "commit"]
		if not commits:
			continue
		first = min(commits, key=lambda s: s.timestamp)
		if first.timestamp < cutoff:
			continue
		when = first.timestamp.strftime("%a")
		focus = obs.recent_changes or obs.workstream or obs.focus or obs.last_action or obs.purpose
		out.append((r.name, f"first {when}", _truncate(_oneline(focus or ''), limit=80)))
	return out


_REVIEW_SECTION_CAP = 12


def _print_review_section(console: Console, title: str, rows: list[tuple], *, columns: tuple[str, ...]) -> None:
	count = len(rows)
	console.print()
	console.print(f"[bold]{title}[/bold] [dim]({count})[/dim]")
	shown = rows[:_REVIEW_SECTION_CAP]
	for row in shown:
		name = row[0]
		col2 = row[1] if len(row) > 1 else ""
		col3 = row[2] if len(row) > 2 else ""
		console.print(f"  [cyan]{name:<30}[/cyan] [yellow]{col2:<14}[/yellow] [dim]{col3}[/dim]")
	if count > _REVIEW_SECTION_CAP:
		console.print(f"  [dim]\u2026 +{count - _REVIEW_SECTION_CAP} more[/dim]")


def _review_arc(report: ProjectReport | None) -> str:
	if report is None or report.observations is None:
		return ""
	obs = report.observations
	for text in (obs.recent_changes, obs.workstream, obs.focus, obs.last_action, obs.purpose):
		if text:
			return _truncate(_oneline(text), limit=200)
	return ""


def _review_attention(report: ProjectReport | None) -> str:
	if report is None or report.observations is None or not report.observations.attention:
		return ""
	return _truncate(_oneline(report.observations.attention), limit=220)


def _review_markdown_entry(kind: str, row: tuple, report: ProjectReport | None) -> list[str]:
	name = _md_safe(str(row[0]))
	lines: list[str] = []
	if kind in ("moved", "new") and len(row) >= 2:
		lines.append(f"- **{name}** — {row[1]}")
	elif kind == "parked" and len(row) >= 3:
		lines.append(f"- **{name}** — {row[1]}, {row[2]}")
	elif kind == "flagged" and len(row) >= 2:
		detail = f": {_md_safe(str(row[2]))}" if len(row) >= 3 and row[2] else ""
		lines.append(f"- **{name}** — {row[1]}{detail}")
	else:
		lines.append(f"- **{name}**")
	arc = _review_arc(report)
	if arc:
		lines.append(f"  - Arc: {_md_safe(arc)}")
	attention = _review_attention(report)
	if attention:
		lines.append(f"  - Attention: {_md_safe(attention)}")
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
	if report.observations and report.observations.next_action:
		lines.append(f"**Next:** {_md_safe(report.observations.next_action)}")
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
