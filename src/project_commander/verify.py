"""Per-project closure verification with structured PASS/FAIL output.

The detail report tells a human reader why a project is in its current state.
`verify` is the same idea recast for automation: named checks with explicit
status, an exit code that scripts can branch on, and a JSON form an agent
can pipe into its own decision logic.

Five checks today, all derived from existing observations data:

- working_tree_clean    no uncommitted changes
- branch_in_sync        no ahead/behind state vs upstream
- no_orphan_thread      latest substantive prompt has a follow-up commit
- no_plan_drift         plan-doc completion claim still matches the code
- prompts_substantive   recent prompts include real direction, not just approvals

The full fleet form (`verify` with no project) reports each project's verdict
on one line and exits non-zero if any project fails. Useful as a pre-shutdown
or pre-handoff gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Iterable

from rich.console import Console

from .models import ProjectReport


@dataclass(frozen=True)
class CheckResult:
	name: str
	status: str  # "PASS" | "FAIL" | "SKIP"
	detail: str = ""


@dataclass(frozen=True)
class ProjectVerdict:
	project: str
	verdict: str  # "PASS" | "FAIL"
	checks: tuple[CheckResult, ...]
	next_action: str = ""


# ───── individual checks ─────────────────────────────────────────────────────

def _check_working_tree(report: ProjectReport) -> CheckResult:
	if not report.is_git_repo:
		return CheckResult("working_tree_clean", "SKIP", "not a git repo")
	if report.git_dirty:
		count = len(report.git_uncommitted)
		return CheckResult("working_tree_clean", "FAIL", f"{count} uncommitted file(s)")
	return CheckResult("working_tree_clean", "PASS")


def _check_branch_sync(report: ProjectReport) -> CheckResult:
	if not report.is_git_repo:
		return CheckResult("branch_in_sync", "SKIP", "not a git repo")
	if not report.git_upstream:
		return CheckResult("branch_in_sync", "SKIP", "no upstream configured")
	parts = []
	if report.git_ahead:
		parts.append(f"{report.git_ahead} ahead")
	if report.git_behind:
		parts.append(f"{report.git_behind} behind")
	if parts:
		return CheckResult("branch_in_sync", "FAIL",
		                   f"{report.git_upstream}: {', '.join(parts)}")
	return CheckResult("branch_in_sync", "PASS",
	                   f"at parity with {report.git_upstream}")


def _check_orphan_thread(report: ProjectReport) -> CheckResult:
	obs = report.observations
	if obs is None:
		return CheckResult("no_orphan_thread", "SKIP", "no observations")
	hours = obs.outstanding.orphaned_thread_age_hours
	if hours is None:
		return CheckResult("no_orphan_thread", "PASS")
	return CheckResult("no_orphan_thread", "FAIL",
	                   f"{hours}h since last substantive prompt with no follow-up commit")


def _check_plan_drift(report: ProjectReport) -> CheckResult:
	obs = report.observations
	if obs is None:
		return CheckResult("no_plan_drift", "SKIP", "no observations")
	if "plan-drift" in obs.flags:
		ref = obs.outstanding.plan_doc_ref or "plan doc"
		return CheckResult("no_plan_drift", "FAIL",
		                   f"{ref} declares complete but commits continue to land")
	return CheckResult("no_plan_drift", "PASS")


def _check_substantive_prompts(report: ProjectReport) -> CheckResult:
	obs = report.observations
	if obs is None:
		return CheckResult("prompts_substantive", "SKIP", "no observations")
	if "procedural-prompts" in obs.flags:
		return CheckResult("prompts_substantive", "FAIL",
		                   "every recent prompt was a one-word approval")
	return CheckResult("prompts_substantive", "PASS")


_CHECKS = (
	_check_working_tree,
	_check_branch_sync,
	_check_orphan_thread,
	_check_plan_drift,
	_check_substantive_prompts,
)


def verify_one(report: ProjectReport) -> ProjectVerdict:
	"""Run every check on a single project and return the combined verdict."""
	results = tuple(check(report) for check in _CHECKS)
	verdict = "FAIL" if any(r.status == "FAIL" for r in results) else "PASS"
	next_action = ""
	if verdict == "FAIL":
		obs = report.observations
		next_action = obs.next_action if obs is not None else ""
	return ProjectVerdict(
		project=report.name, verdict=verdict, checks=results,
		next_action=next_action,
	)


def verify_all(reports: Iterable[ProjectReport]) -> list[ProjectVerdict]:
	return [verify_one(r) for r in reports]


# ───── rendering ─────────────────────────────────────────────────────────────

_STATUS_COLOR = {"PASS": "green", "FAIL": "red", "SKIP": "dim"}


def render_verdict_terminal(v: ProjectVerdict, console: Console) -> None:
	verdict_color = "red" if v.verdict == "FAIL" else "green"
	failed = [c for c in v.checks if c.status == "FAIL"]
	if failed:
		count = f"({len(failed)} issue(s))"
	else:
		count = ""
	console.print()
	console.print(f"[bold cyan]{v.project}[/bold cyan]  [bold {verdict_color}]VERIFY {v.verdict}[/bold {verdict_color}]  [dim]{count}[/dim]")
	console.print()
	for check in v.checks:
		color = _STATUS_COLOR[check.status]
		detail = f"  [dim]{check.detail}[/dim]" if check.detail else ""
		console.print(f"  [{color}][{check.status:>4}][/{color}]  {check.name}{detail}")
	if v.verdict == "FAIL" and v.next_action:
		console.print()
		console.print(f"  [bold]Suggested resolution:[/bold] {v.next_action}")


def render_fleet_terminal(verdicts: list[ProjectVerdict], console: Console) -> None:
	failed_count = sum(1 for v in verdicts if v.verdict == "FAIL")
	for v in verdicts:
		fail_details = [c for c in v.checks if c.status == "FAIL"]
		if v.verdict == "PASS":
			console.print(f"  [green]PASS[/green]  [cyan]{v.project}[/cyan]")
		else:
			summary = ", ".join(c.detail or c.name for c in fail_details)
			console.print(f"  [red]FAIL[/red]  [cyan]{v.project}[/cyan]  [dim]{summary}[/dim]")
	console.print()
	if failed_count:
		console.print(f"[bold red]{failed_count} of {len(verdicts)} projects failed verification.[/bold red]")
	else:
		console.print(f"[bold green]All {len(verdicts)} projects passed.[/bold green]")


def render_json(verdicts: list[ProjectVerdict] | ProjectVerdict) -> str:
	"""Serialize one or many verdicts. Single verdict → object; list → array."""
	def to_dict(v: ProjectVerdict) -> dict:
		return {
			"project": v.project,
			"verdict": v.verdict,
			"checks": [
				{"name": c.name, "status": c.status, "detail": c.detail}
				for c in v.checks
			],
			"next_action": v.next_action,
		}
	if isinstance(verdicts, ProjectVerdict):
		return json.dumps(to_dict(verdicts), indent=2)
	return json.dumps([to_dict(v) for v in verdicts], indent=2)


# ───── subcommand wiring ─────────────────────────────────────────────────────

def add_subparser(subparsers) -> argparse.ArgumentParser:
	parser = subparsers.add_parser(
		"verify",
		help="Run named PASS/FAIL closure checks; non-zero exit on FAIL.",
		description="Per-project (or fleet-wide) closure verification suitable for "
		            "agent chaining, pre-handoff gates, and shutdown audits.",
	)
	from .cli import _add_common_scan_args
	_add_common_scan_args(parser)
	parser.add_argument("--format", choices=["terminal", "json"], default="terminal")
	parser.set_defaults(func=run)
	return parser


def run(args: argparse.Namespace) -> int:
	from .cli import build_reports
	console = Console(no_color=args.no_color, soft_wrap=False)

	reports = build_reports(args)
	if reports is None:
		return 1

	verdicts = verify_all(reports)
	any_failed = any(v.verdict == "FAIL" for v in verdicts)

	if args.format == "json":
		# Single project → single object; multiple → array
		if len(verdicts) == 1:
			sys.stdout.write(render_json(verdicts[0]) + "\n")
		else:
			sys.stdout.write(render_json(verdicts) + "\n")
	else:
		if len(verdicts) == 1:
			render_verdict_terminal(verdicts[0], console)
		else:
			render_fleet_terminal(verdicts, console)

	return 1 if any_failed else 0
