"""Per-project agent audit: prompt \u2192 commit causality, ratios, flags.

Different question from `verify` and `report`:
- `report` answers "what is this project?"
- `verify` answers "is it in a clean state?"
- `audit` answers "did agent prompts actually convert into landed code?"

For each substantive prompt in the window, we look forward 24h for any
commit. If found, the prompt is "executed". If not, "orphan". The
prompt-to-commit ratio gives a coarse handle on conversation volume vs
real progress; outliers (very high ratio) suggest the user is iterating
without committing.

Procedural prompts (yes/proceed/continue/...) are counted separately and
never count as "substantive" \u2014 they are approval traffic, not direction.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rich.console import Console

from .models import ProjectReport, Signal
from .observations import is_procedural


# Window in which a commit "follows" a prompt. Conservative: agents and humans
# usually commit within a day of the directive being given.
_FOLLOWUP_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class PromptOutcome:
	prompt: Signal
	followup_commits: int  # commits in the 24h window after this prompt
	executed: bool         # at least one follow-up commit landed


@dataclass(frozen=True)
class AuditReport:
	project: str
	since: datetime
	now: datetime
	commits: int
	prompts_total: int
	prompts_substantive: int
	prompts_procedural: int
	prompt_to_commit_ratio: float | None
	outcomes: tuple[PromptOutcome, ...]
	flags: tuple[str, ...] = field(default_factory=tuple)

	@property
	def orphan_count(self) -> int:
		return sum(1 for o in self.outcomes if not o.executed)

	@property
	def executed_count(self) -> int:
		return sum(1 for o in self.outcomes if o.executed)


# ───── computation ───────────────────────────────────────────────────────────

def audit(report: ProjectReport, *, since_days: int, now: datetime | None = None) -> AuditReport:
	now = now or datetime.now(tz=timezone.utc)
	since = now - timedelta(days=since_days)
	commits = sorted(
		(s for s in report.signals if s.kind == "commit" and s.timestamp >= since),
		key=lambda s: s.timestamp,
	)
	prompts = sorted(
		(s for s in report.signals if s.kind == "prompt" and s.timestamp >= since),
		key=lambda s: s.timestamp,
	)
	substantive = [p for p in prompts if not is_procedural(p.summary)]
	procedural = [p for p in prompts if is_procedural(p.summary)]

	# Sort all commits across the whole project (not just in-window): a prompt
	# at the end of the window may be followed by a commit a few hours later.
	all_commit_ts = sorted(s.timestamp for s in report.signals if s.kind == "commit")

	outcomes: list[PromptOutcome] = []
	for p in substantive:
		end = p.timestamp + _FOLLOWUP_WINDOW
		count = sum(1 for ts in all_commit_ts if p.timestamp < ts <= end)
		outcomes.append(PromptOutcome(prompt=p, followup_commits=count, executed=count > 0))

	ratio: float | None = None
	if commits:
		ratio = len(prompts) / len(commits)

	flags: list[str] = []
	if report.observations is not None:
		obs_flags = report.observations.flags
		for f in ("prompt-injection-detected", "plan-drift", "procedural-prompts"):
			if f in obs_flags:
				flags.append(f)
	if outcomes and all(not o.executed for o in outcomes):
		flags.append("all-orphans")
	if ratio is not None and ratio >= 5.0:
		flags.append("high-prompt-volume")

	return AuditReport(
		project=report.name, since=since, now=now,
		commits=len(commits), prompts_total=len(prompts),
		prompts_substantive=len(substantive), prompts_procedural=len(procedural),
		prompt_to_commit_ratio=ratio,
		outcomes=tuple(outcomes), flags=tuple(flags),
	)


# ───── rendering ─────────────────────────────────────────────────────────────

def _summary_line(text: str, *, limit: int = 90) -> str:
	text = " ".join(text.split())
	if len(text) <= limit:
		return text
	return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def render_terminal(audit_report: AuditReport, console: Console) -> None:
	a = audit_report
	since_age_days = (a.now - a.since).days
	console.rule(f"[bold cyan]{a.project}[/bold cyan]  [dim](audit, last {since_age_days}d)[/dim]")
	console.print()
	ratio_str = f"{a.prompt_to_commit_ratio:.1f}" if a.prompt_to_commit_ratio is not None else "n/a"
	console.print(f"  {a.prompts_total} prompt(s)  ·  {a.commits} commit(s)  ·  ratio {ratio_str}")
	console.print(f"  [dim]substantive: {a.prompts_substantive}  ·  procedural: {a.prompts_procedural}[/dim]")
	console.print()
	if a.outcomes:
		console.print("[bold]Substantive prompts[/bold]")
		for o in a.outcomes:
			when = o.prompt.timestamp.strftime("%Y-%m-%d")
			source = o.prompt.source
			arrow = "→ executed" if o.executed else "→ orphan"
			color = "green" if o.executed else "yellow"
			console.print(
				f"  [dim]{when}[/dim] [cyan]{source}[/cyan]  "
				f"\"{_summary_line(o.prompt.summary)}\""
			)
			console.print(f"    [{color}]{arrow}[/{color}]  "
			              f"[dim]{o.followup_commits} commit(s) within 24h[/dim]")
	if a.flags:
		console.print()
		console.print(f"[yellow]flags:[/yellow] {', '.join(a.flags)}")


def render_markdown(audit_report: AuditReport) -> str:
	a = audit_report
	since_age_days = (a.now - a.since).days
	ratio_str = f"{a.prompt_to_commit_ratio:.1f}" if a.prompt_to_commit_ratio is not None else "n/a"
	lines = [f"# {a.project} — audit", "",
	         f"_Last {since_age_days} day(s) · since {a.since.date().isoformat()}_", ""]
	lines.append(f"- **Prompts:** {a.prompts_total} ({a.prompts_substantive} substantive, "
	             f"{a.prompts_procedural} procedural)")
	lines.append(f"- **Commits:** {a.commits}")
	lines.append(f"- **Prompt → commit ratio:** {ratio_str}")
	lines.append("")
	if a.outcomes:
		lines.append("## Substantive prompts")
		lines.append("")
		for o in a.outcomes:
			when = o.prompt.timestamp.strftime("%Y-%m-%d")
			arrow = "executed" if o.executed else "**orphan**"
			lines.append(
				f"- `{when}` `{o.prompt.source}` — \"{_summary_line(o.prompt.summary)}\"  "
				f"_({arrow}, {o.followup_commits} commit(s) within 24h)_"
			)
		lines.append("")
	if a.flags:
		lines.append(f"**Flags:** {', '.join(a.flags)}")
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── subcommand wiring ─────────────────────────────────────────────────────

def add_subparser(subparsers) -> argparse.ArgumentParser:
	parser = subparsers.add_parser(
		"audit",
		help="Audit agent activity on a project: prompt \u2192 commit causality.",
		description="Show prompt-to-commit causality and traffic ratios for one or more projects.",
	)
	from .cli import _add_common_scan_args
	_add_common_scan_args(parser)
	parser.add_argument("--since", type=int, default=7,
	                    help="window in days (default: 7)")
	parser.add_argument("--format", choices=["terminal", "markdown"], default="terminal")
	parser.set_defaults(func=run)
	return parser


def run(args: argparse.Namespace) -> int:
	from .cli import build_reports
	console = Console(no_color=args.no_color, soft_wrap=False)

	# Audit windows up to ~30 days; pull a comfortable cushion of commit history.
	reports = build_reports(args, git_recent_commits=200)
	if reports is None:
		return 1

	now = datetime.now(tz=timezone.utc)
	audits = [audit(r, since_days=args.since, now=now) for r in reports]

	if args.format == "markdown":
		out_parts = [render_markdown(a) for a in audits]
		sys.stdout.write("\n---\n\n".join(out_parts))
	else:
		for a in audits:
			render_terminal(a, console)
			console.print()
	return 0
