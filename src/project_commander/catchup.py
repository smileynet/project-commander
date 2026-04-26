"""Catch-up digest: 'what changed since I last looked?'

Distinct from `report --since N` because the cadence is anchored to the user
rather than to the calendar week. The tool persists a cursor — the timestamp
of the last `catchup` invocation — and shows deltas relative to it. Each run
advances the cursor.

Override the cursor for a single invocation with `--since <Nh|Nd>`. Reset
the cursor entirely with `--reset-cursor`.

Three sections, each answering a different question:

- Agent activity while you were away  (prompts since the cursor)
- Upstream moved without you          (commits authored remotely)
- Your own work since last check      (commits you authored locally)

If the same project has signals in multiple categories, it appears in each
relevant one — these are not exclusive views, they are different angles on
the same delta.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from rich.console import Console

from .models import ProjectReport
from .observations import is_procedural


# ───── cursor persistence ────────────────────────────────────────────────────

_CURSOR_REL = "project-commander/catchup-cursor.txt"


def state_dir(home: Path) -> Path:
	"""Return the XDG-style state directory for the cursor file."""
	xdg = os.environ.get("XDG_STATE_HOME")
	if xdg:
		return Path(xdg).expanduser()
	return home / ".local" / "state"


def cursor_path(home: Path) -> Path:
	return state_dir(home) / _CURSOR_REL


def read_cursor(home: Path) -> datetime | None:
	"""Read the persisted cursor timestamp; None if unset or unreadable."""
	path = cursor_path(home)
	try:
		text = path.read_text(encoding="utf-8").strip()
	except OSError:
		return None
	try:
		ts = datetime.fromisoformat(text)
	except ValueError:
		return None
	if ts.tzinfo is None:
		ts = ts.replace(tzinfo=timezone.utc)
	return ts.astimezone(timezone.utc)


def write_cursor(home: Path, ts: datetime) -> None:
	path = cursor_path(home)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(ts.astimezone(timezone.utc).isoformat() + "\n", encoding="utf-8")


def clear_cursor(home: Path) -> None:
	path = cursor_path(home)
	try:
		path.unlink()
	except FileNotFoundError:
		pass


# ───── since-window parsing ──────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)


def parse_since(text: str) -> timedelta:
	"""Parse a duration like '6h', '2d', '30m'. Raises ValueError on bad input."""
	m = _DURATION_RE.match(text)
	if not m:
		raise ValueError(f"unrecognized duration: {text!r} (try '6h', '2d', '30m')")
	n = int(m.group(1))
	unit = m.group(2).lower()
	multipliers = {
		"s": timedelta(seconds=1),
		"m": timedelta(minutes=1),
		"h": timedelta(hours=1),
		"d": timedelta(days=1),
		"w": timedelta(weeks=1),
	}
	return n * multipliers[unit]


# ───── delta classification ──────────────────────────────────────────────────

@dataclass(frozen=True)
class CatchupRow:
	"""One project's contribution to one section of the catchup digest."""

	name: str
	bucket: str  # "agent" | "upstream" | "own"
	commits: int
	prompts: int
	procedural: int
	headline: str
	last_active: datetime | None
	dirty: bool
	upstream_behind: int
	upstream_label: str = ""


def _topic(commit_summary: str, limit: int = 80) -> str:
	"""Compact a commit subject for the headline."""
	text = " ".join(commit_summary.split())
	if len(text) <= limit:
		return text
	return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _agent_headline(prompts_since: list, commits_since: list) -> str:
	subst = [p for p in prompts_since if not is_procedural(p.summary)]
	if subst:
		latest = max(subst, key=lambda s: s.timestamp)
		ratio_note = ""
		if commits_since:
			ratio_note = f"; {len(commits_since)} commit(s) followed"
		else:
			ratio_note = "; no commits followed yet"
		return f"latest prompt: {_topic(latest.summary, 90)}{ratio_note}"
	if prompts_since:
		return f"{len(prompts_since)} approval prompt(s); nothing substantive"
	return "(no prompts)"


def _own_headline(commits_since: list) -> str:
	if not commits_since:
		return "(no commits)"
	subjects = [_topic(c.summary, 60) for c in commits_since[:3]]
	tail = f" (+{len(commits_since) - 3} more)" if len(commits_since) > 3 else ""
	return "; ".join(subjects) + tail


def _upstream_headline(report: ProjectReport) -> str:
	dest = report.git_upstream or "upstream"
	return f"{report.git_behind} commit(s) ahead of you on `{dest}`"


def classify(report: ProjectReport, *, since: datetime, now: datetime) -> list[CatchupRow]:
	"""Return zero or more catchup rows for this project."""
	rows: list[CatchupRow] = []
	commits_since = [s for s in report.signals if s.kind == "commit" and s.timestamp >= since]
	prompts_since = [s for s in report.signals if s.kind == "prompt" and s.timestamp >= since]
	subst_prompts = [p for p in prompts_since if not is_procedural(p.summary)]
	procedural_prompts = [p for p in prompts_since if is_procedural(p.summary)]

	# Agent activity: any prompts since the cursor
	if prompts_since:
		rows.append(CatchupRow(
			name=report.name, bucket="agent",
			commits=len(commits_since), prompts=len(subst_prompts),
			procedural=len(procedural_prompts),
			headline=_agent_headline(prompts_since, commits_since),
			last_active=report.last_active, dirty=report.git_dirty,
			upstream_behind=report.git_behind,
			upstream_label=report.git_upstream or "",
		))

	# Upstream moved without you: behind upstream AND has new commits in window
	# (the second clause filters out projects you've been behind on for ages)
	if report.git_behind > 0 and commits_since:
		rows.append(CatchupRow(
			name=report.name, bucket="upstream",
			commits=len(commits_since), prompts=0, procedural=0,
			headline=_upstream_headline(report),
			last_active=report.last_active, dirty=report.git_dirty,
			upstream_behind=report.git_behind,
			upstream_label=report.git_upstream or "",
		))

	# Your own work: commits in window, NOT in upstream-behind state
	# (upstream-behind already covers those; this section is for projects
	# where you authored progress)
	if commits_since and report.git_behind == 0:
		rows.append(CatchupRow(
			name=report.name, bucket="own",
			commits=len(commits_since), prompts=len(subst_prompts), procedural=0,
			headline=_own_headline(commits_since),
			last_active=report.last_active, dirty=report.git_dirty,
			upstream_behind=0,
			upstream_label=report.git_upstream or "",
		))

	return rows


def build_sections(reports: Iterable[ProjectReport], *, since: datetime, now: datetime
                   ) -> dict[str, list[CatchupRow]]:
	buckets: dict[str, list[CatchupRow]] = {"agent": [], "upstream": [], "own": []}
	for report in reports:
		for row in classify(report, since=since, now=now):
			buckets[row.bucket].append(row)
	for entries in buckets.values():
		entries.sort(key=lambda r: -(r.last_active.timestamp() if r.last_active else 0.0))
	return buckets


# ───── rendering ─────────────────────────────────────────────────────────────

_SECTION_TITLES = {
	"agent": "Agent activity while you were away",
	"upstream": "Upstream moved without you",
	"own": "Your own work since last check",
}


def render_terminal(buckets: dict[str, list[CatchupRow]], *, since: datetime, now: datetime,
                    cursor_was_unset: bool, console: Console) -> None:
	delta = now - since
	hours = int(delta.total_seconds() // 3600)
	if hours < 24:
		when = f"{hours}h ago" if hours > 0 else "moments ago"
	else:
		when = f"{delta.days}d ago"
	header = f"Catch up since {since.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ({when})"
	if cursor_was_unset:
		header += "  [first run]"
	console.rule(f"[bold cyan]{header}")
	if not any(buckets.values()):
		console.print()
		console.print("  [dim](nothing changed since last check)[/dim]")
		return
	for bucket, title in _SECTION_TITLES.items():
		entries = buckets[bucket]
		if not entries:
			continue
		console.print()
		console.print(f"[bold]{title}[/bold] [dim]({len(entries)})[/dim]")
		for row in entries:
			color = {"agent": "yellow", "upstream": "cyan", "own": "green"}[bucket]
			console.print(f"  [{color}]{row.name}[/{color}]  [dim]{row.headline}[/dim]")


def render_markdown(buckets: dict[str, list[CatchupRow]], *, since: datetime, now: datetime,
                    cursor_was_unset: bool) -> str:
	delta = now - since
	hours = int(delta.total_seconds() // 3600)
	if hours < 24:
		when = f"{hours}h ago" if hours > 0 else "moments ago"
	else:
		when = f"{delta.days}d ago"
	first = "  _(first run)_" if cursor_was_unset else ""
	lines = [f"# Catch up since {since.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ({when}){first}",
	         ""]
	if not any(buckets.values()):
		lines.append("_Nothing changed since last check._")
		return "\n".join(lines).rstrip() + "\n"
	for bucket, title in _SECTION_TITLES.items():
		entries = buckets[bucket]
		if not entries:
			continue
		lines.append(f"## {title} ({len(entries)})")
		lines.append("")
		for row in entries:
			lines.append(f"- **{row.name}** \u2014 {row.headline}")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── subcommand wiring ─────────────────────────────────────────────────────

def add_subparser(subparsers) -> argparse.ArgumentParser:
	parser = subparsers.add_parser(
		"catchup",
		help="Show what changed since you last ran catchup.",
		description="Catch-up digest anchored to a persisted cursor (~/.local/state/project-commander/).",
	)
	from .cli import _add_common_scan_args
	_add_common_scan_args(parser)
	parser.add_argument("--since", type=str, default=None,
	                    help="override the cursor with a duration like '6h' or '2d'")
	parser.add_argument("--reset-cursor", action="store_true", default=False,
	                    help="delete the cursor file and exit (next run starts fresh)")
	parser.add_argument("--no-advance", action="store_true", default=False,
	                    help="do not advance the cursor on this run (preview mode)")
	parser.add_argument("--format", choices=["terminal", "markdown"], default="terminal")
	parser.set_defaults(func=run)
	return parser


def _resolve_since(args, *, home: Path, now: datetime) -> tuple[datetime, bool]:
	"""Return (since_ts, cursor_was_unset)."""
	if args.since:
		return now - parse_since(args.since), False
	cursor = read_cursor(home)
	if cursor is not None:
		return cursor, False
	# First run: anchor to 24h ago so the user gets something useful
	return now - timedelta(hours=24), True


def run(args: argparse.Namespace) -> int:
	from .cli import build_reports
	console = Console(no_color=args.no_color, soft_wrap=False)

	home = args.home
	if args.reset_cursor:
		clear_cursor(home)
		console.print("[green]cursor cleared[/green]")
		return 0

	now = datetime.now(tz=timezone.utc)
	since, was_unset = _resolve_since(args, home=home, now=now)

	reports = build_reports(args)
	if reports is None:
		return 1

	buckets = build_sections(reports, since=since, now=now)
	if args.format == "markdown":
		import sys
		sys.stdout.write(render_markdown(buckets, since=since, now=now,
		                                 cursor_was_unset=was_unset))
	else:
		render_terminal(buckets, since=since, now=now,
		                cursor_was_unset=was_unset, console=console)

	# Advance cursor unless --no-advance or --since (which is preview-by-intent)
	if not args.no_advance and not args.since:
		write_cursor(home, now)
	return 0
