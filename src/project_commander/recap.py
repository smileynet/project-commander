"""Retrospective: a per-period narrative across the project fleet.

Different intent from `report --since N`: the weekly digest is a triage
artifact (what should I do?). Recap is a memory artifact (what did I do?).

Each project that had activity in the window gets a paragraph synthesized
from its purpose, recent commit themes, and plan-doc references, grouped
into four categories:

- Shipped               plan-doc declares complete + activity in window
- Major arcs            plenty of activity in the window (>= threshold commits)
- Started but paused    first commit in window, no activity in last 30% of window
- Quiet activity        had activity but doesn't fit the above

Out-of-scope: archived projects (the `tidy --prune` command moves those out
of the scanned root, so they naturally fall off the recap).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from rich.console import Console

from .models import ProjectReport
from .observations import is_procedural


_MAJOR_ARC_COMMIT_THRESHOLD = 10  # commits in window to qualify as a "major arc"


@dataclass(frozen=True)
class RecapEntry:
	project: str
	category: str  # "shipped" | "major" | "paused" | "quiet"
	commits: int
	prompts: int
	first_commit_in_window: datetime | None
	last_commit_in_window: datetime | None
	narrative: str


# ───── categorization ────────────────────────────────────────────────────────

def _is_shipped(report: ProjectReport) -> bool:
	obs = report.observations
	if obs is None:
		return False
	# Shipped progress state OR plan-drift (plan said shipped, but evolved
	# afterwards) — both reflect a "this was declared done" moment in the period.
	return obs.progress.value == "shipped"


def categorize(report: ProjectReport, *, since: datetime, now: datetime) -> str | None:
	"""Return a category key, or None if the project had no activity in the window."""
	commits = [s for s in report.signals if s.kind == "commit" and s.timestamp >= since]
	prompts = [s for s in report.signals if s.kind == "prompt" and s.timestamp >= since
	           and not is_procedural(s.summary)]
	if not commits and not prompts:
		return None
	if _is_shipped(report) and commits:
		return "shipped"
	if len(commits) >= _MAJOR_ARC_COMMIT_THRESHOLD:
		return "major"
	# Started-but-paused: first commit happened in the window, but the most
	# recent activity is in the early part (idle for the back end of the window)
	if commits:
		all_commits = sorted(s.timestamp for s in report.signals if s.kind == "commit")
		first_ever = min(all_commits)
		if first_ever >= since:
			window_span = (now - since).total_seconds()
			last_in_window = max(s.timestamp for s in commits)
			idle_for = (now - last_in_window).total_seconds()
			if idle_for >= 0.3 * window_span:
				return "paused"
	return "quiet"


# ───── narrative synthesis ──────────────────────────────────────────────────

def _commit_topics(commits: list, *, max_topics: int = 3) -> list[str]:
	"""Pull a few representative commit subject fragments without lossy NLP."""
	from .observations import _commit_topic_fragment, _similar_topic, _is_low_signal_commit
	topics: list[str] = []
	for c in sorted(commits, key=lambda s: s.timestamp, reverse=True):
		if _is_low_signal_commit(c.summary):
			continue
		fragment = _commit_topic_fragment(c.summary)
		if not fragment or any(_similar_topic(fragment, t) for t in topics):
			continue
		topics.append(fragment)
		if len(topics) >= max_topics:
			break
	return topics


def _phrase_join(items: list[str]) -> str:
	if not items:
		return ""
	if len(items) == 1:
		return items[0]
	if len(items) == 2:
		return f"{items[0]} and {items[1]}"
	return ", ".join(items[:-1]) + f", and {items[-1]}"


def synthesize(report: ProjectReport, *, since: datetime, now: datetime,
               category: str) -> str:
	"""Produce the per-project paragraph."""
	commits = [s for s in report.signals if s.kind == "commit" and s.timestamp >= since]
	obs = report.observations
	purpose_sentence = ""
	if obs and obs.purpose:
		from .observations import first_sentence
		purpose_sentence = first_sentence(obs.purpose, limit=200).rstrip(".") + "."

	topics = _commit_topics(commits)
	commits_count = len(commits)

	parts: list[str] = []
	if purpose_sentence:
		parts.append(purpose_sentence)

	if category == "shipped":
		body = f"Declared complete this period after {commits_count} commit(s)"
		if topics:
			body += f"; final-stretch work covered {_phrase_join(topics)}"
		parts.append(body + ".")
	elif category == "major":
		body = f"Major arc: {commits_count} commits this period"
		if topics:
			body += f", focused on {_phrase_join(topics)}"
		parts.append(body + ".")
	elif category == "paused":
		when = max(s.timestamp for s in commits)
		idle_days = (now - when).days
		body = f"Started this period and stalled \u2014 {commits_count} commit(s) " \
		       f"with no activity in the last {idle_days} day(s)"
		if topics:
			body += f"; touched on {_phrase_join(topics)}"
		parts.append(body + ".")
	else:  # quiet
		body = f"Quiet activity \u2014 {commits_count} commit(s)"
		if topics:
			body += f", touching {_phrase_join(topics)}"
		parts.append(body + ".")
	return " ".join(parts)


def build_entries(reports: Iterable[ProjectReport], *, since: datetime, now: datetime
                  ) -> list[RecapEntry]:
	entries: list[RecapEntry] = []
	for report in reports:
		category = categorize(report, since=since, now=now)
		if category is None:
			continue
		commits = [s for s in report.signals if s.kind == "commit" and s.timestamp >= since]
		prompts = [s for s in report.signals if s.kind == "prompt" and s.timestamp >= since
		           and not is_procedural(s.summary)]
		first = min((s.timestamp for s in commits), default=None)
		last = max((s.timestamp for s in commits), default=None)
		narrative = synthesize(report, since=since, now=now, category=category)
		entries.append(RecapEntry(
			project=report.name, category=category,
			commits=len(commits), prompts=len(prompts),
			first_commit_in_window=first, last_commit_in_window=last,
			narrative=narrative,
		))
	# Order within each category: most active first
	entries.sort(key=lambda e: (-e.commits, e.project))
	return entries


# ───── rendering ─────────────────────────────────────────────────────────────

_SECTION_TITLES = [
	("shipped", "Shipped"),
	("major", "Major arcs"),
	("paused", "Started but paused"),
	("quiet", "Quiet activity"),
]


def _resolve_window(now: datetime, *, quarter: bool, year: bool, month: bool,
                    since_days: int | None) -> tuple[datetime, str]:
	"""Return (since_ts, label)."""
	if year:
		since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
		return since, f"{now.year} year-in-review"
	if quarter:
		q = (now.month - 1) // 3
		first_month = q * 3 + 1
		since = now.replace(month=first_month, day=1, hour=0, minute=0, second=0, microsecond=0)
		return since, f"Q{q + 1} {now.year} in review"
	if month:
		since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
		return since, f"{now.strftime('%B %Y')} in review"
	if since_days is not None:
		since = now - timedelta(days=since_days)
		return since, f"Last {since_days} day(s) in review"
	# Default: 90 days
	return now - timedelta(days=90), "Last 90 day(s) in review"


def render_terminal(entries: list[RecapEntry], *, label: str, since: datetime,
                    now: datetime, console: Console) -> None:
	console.rule(f"[bold cyan]{label}[/bold cyan]")
	console.print(f"[dim]since {since.date().isoformat()}  ·  "
	              f"{len(entries)} project(s) active[/dim]")
	if not entries:
		console.print()
		console.print("  [dim](nothing to recap)[/dim]")
		return
	bucketed: dict[str, list[RecapEntry]] = {key: [] for key, _ in _SECTION_TITLES}
	for e in entries:
		bucketed[e.category].append(e)
	for key, title in _SECTION_TITLES:
		bucket = bucketed[key]
		if not bucket:
			continue
		console.print()
		console.print(f"[bold]{title}[/bold] [dim]({len(bucket)})[/dim]")
		for e in bucket:
			console.print(f"  [cyan]{e.project}[/cyan]")
			console.print(f"    [dim]{e.narrative}[/dim]")


def render_markdown(entries: list[RecapEntry], *, label: str, since: datetime,
                    now: datetime) -> str:
	lines = [f"# {label}", "",
	         f"_Since {since.date().isoformat()} · {len(entries)} project(s) active_", ""]
	if not entries:
		lines.append("_Nothing to recap._")
		return "\n".join(lines).rstrip() + "\n"
	bucketed: dict[str, list[RecapEntry]] = {key: [] for key, _ in _SECTION_TITLES}
	for e in entries:
		bucketed[e.category].append(e)
	for key, title in _SECTION_TITLES:
		bucket = bucketed[key]
		if not bucket:
			continue
		lines.append(f"## {title} ({len(bucket)})")
		lines.append("")
		for e in bucket:
			lines.append(f"### {e.project}")
			lines.append("")
			lines.append(e.narrative)
			lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── subcommand wiring ─────────────────────────────────────────────────────

def add_subparser(subparsers) -> argparse.ArgumentParser:
	parser = subparsers.add_parser(
		"recap",
		help="Retrospective: per-project narrative for an arbitrary period.",
		description="Categorize each project's activity in the window (shipped, major arc, "
		            "paused, quiet) and synthesize a paragraph for each.",
	)
	from .cli import _add_common_scan_args
	_add_common_scan_args(parser)
	group = parser.add_mutually_exclusive_group()
	group.add_argument("--quarter", action="store_true", help="current calendar quarter")
	group.add_argument("--year", action="store_true", help="current calendar year")
	group.add_argument("--month", action="store_true", help="current calendar month")
	group.add_argument("--since", type=int, default=None, dest="since_days",
	                    help="window in days (default: 90)")
	parser.add_argument("--format", choices=["terminal", "markdown"], default="terminal")
	parser.set_defaults(func=run)
	return parser


def run(args: argparse.Namespace) -> int:
	from .cli import build_reports
	console = Console(no_color=args.no_color, soft_wrap=False)

	# Recap windows can stretch to a full year; pull more git history per project.
	reports = build_reports(args, git_recent_commits=500)
	if reports is None:
		return 1

	now = datetime.now(tz=timezone.utc)
	since, label = _resolve_window(
		now, quarter=args.quarter, year=args.year, month=args.month,
		since_days=args.since_days,
	)
	entries = build_entries(reports, since=since, now=now)

	if args.format == "markdown":
		sys.stdout.write(render_markdown(entries, label=label, since=since, now=now))
	else:
		render_terminal(entries, label=label, since=since, now=now, console=console)
	return 0
