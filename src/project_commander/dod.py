"""User-defined Definition of Done — diff perceived state against a checklist.

`verify` ships five hardcoded closure checks that apply to every project the
same way. `dod` is the user-defined complement: each project drops a
`DOD.md` at its root listing what "done" means *for it*, and this command
shows progress against that definition so the user (or an agent) can drive
toward completion.

Each `- [ ] criterion` is parsed and matched against a small registry of
auto-check patterns. Recognized criteria are evaluated against the
ProjectReport state. Unrecognized criteria stay MANUAL — the user marks
them `[x]` when they hand-confirm, and the tool counts that as DONE.

States and how they roll up:

	PASS    auto-check matched and passed
	FAIL    auto-check matched and failed
	DONE    user marked `[x]` in the source file
	MANUAL  no auto-check pattern; awaiting user confirmation
	SKIP    auto-check matched but is not applicable (e.g. branch_in_sync
	        with no upstream configured)

	complete   = PASS + DONE
	outstanding = FAIL + MANUAL
	progress   = complete / (complete + outstanding)   (SKIP excluded)

The exit code is 0 when every relevant criterion is complete, 1 otherwise —
mirroring `verify` so the same chaining patterns work.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from rich.console import Console
from rich.markup import escape as _rich_escape

from .models import ProjectReport
from .verify import (
	CheckResult,
	_check_branch_sync,
	_check_orphan_thread,
	_check_plan_drift,
	_check_substantive_prompts,
	_check_working_tree,
	verify_one,
)


# ───── data shapes ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Criterion:
	"""One `- [ ]` line from DOD.md, after evaluation."""

	text: str            # the criterion text (markdown chrome stripped)
	user_checked: bool   # True if the source line was `- [x]`
	status: str          # "PASS" | "FAIL" | "DONE" | "MANUAL" | "SKIP"
	detail: str = ""     # one-line explanation of the status
	auto_check: str = "" # name of the matched auto-check (or "")


@dataclass(frozen=True)
class DoDResult:
	"""Per-project evaluation of the Definition of Done."""

	project: str
	file_path: str            # rel-path of DOD.md inside the project, or "" if missing
	file_exists: bool
	criteria: tuple[Criterion, ...]
	next_action: str = ""

	@property
	def total_relevant(self) -> int:
		return sum(1 for c in self.criteria if c.status != "SKIP")

	@property
	def complete(self) -> int:
		return sum(1 for c in self.criteria if c.status in ("PASS", "DONE"))

	@property
	def outstanding(self) -> int:
		return sum(1 for c in self.criteria if c.status in ("FAIL", "MANUAL"))

	@property
	def skipped(self) -> int:
		return sum(1 for c in self.criteria if c.status == "SKIP")

	@property
	def percent(self) -> int:
		if self.total_relevant == 0:
			return 0
		return round(100 * self.complete / self.total_relevant)

	@property
	def is_complete(self) -> bool:
		return self.file_exists and self.outstanding == 0 and self.total_relevant > 0


# ───── extra auto-checks (verify primitives cover most) ──────────────────────

def _check_branch_is_main(report: ProjectReport) -> CheckResult:
	if not report.is_git_repo:
		return CheckResult("branch_is_main", "SKIP", "not a git repo")
	if not report.git_branch:
		return CheckResult("branch_is_main", "SKIP", "no branch detected")
	if report.git_branch in ("main", "master", "trunk"):
		return CheckResult("branch_is_main", "PASS", f"on {report.git_branch}")
	return CheckResult("branch_is_main", "FAIL",
	                   f"on {report.git_branch}; expected main/master/trunk")


def _check_plan_complete(report: ProjectReport) -> CheckResult:
	if not report.plan_summaries:
		return CheckResult("plan_complete", "SKIP", "no structured plan doc found")
	open_items = sum(p.open_items for p in report.plan_summaries.values())
	open_phases = sum(p.total_phases - p.complete_phases
	                  for p in report.plan_summaries.values())
	if open_items == 0 and open_phases == 0:
		return CheckResult("plan_complete", "PASS",
		                   "all checkboxes ticked, all phases complete")
	parts = []
	if open_items:
		parts.append(f"{open_items} unchecked item(s)")
	if open_phases:
		parts.append(f"{open_phases} open phase(s)")
	return CheckResult("plan_complete", "FAIL", ", ".join(parts))


def _check_verify_all(report: ProjectReport) -> CheckResult:
	v = verify_one(report)
	failed = [c for c in v.checks if c.status == "FAIL"]
	if v.verdict == "PASS":
		passed = sum(1 for c in v.checks if c.status == "PASS")
		return CheckResult("verify_all", "PASS",
		                   f"{passed} of {len(v.checks)} checks passed (rest skipped)")
	names = ", ".join(c.name for c in failed)
	return CheckResult("verify_all", "FAIL",
	                   f"{len(failed)} verify check(s) failed: {names}")


# ───── pattern registry ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Pattern:
	name: str
	regex: re.Pattern
	check: Callable[[ProjectReport], CheckResult]


# Order matters: the first matching pattern wins. More specific phrases first.
_PATTERNS: tuple[_Pattern, ...] = (
	# All verify checks pass (must come before the individual ones; otherwise
	# the substring "verify" inside a more specific phrase could be missed).
	_Pattern(
		name="verify_all",
		regex=re.compile(
			r"\bverify\s+(?:passes|clean|all\s+green|green)\b"
			r"|\ball\s+verify\s+checks?\s+pass(?:ing|es)?\b",
			re.IGNORECASE,
		),
		check=_check_verify_all,
	),
	# Working tree clean / no uncommitted
	_Pattern(
		name="working_tree_clean",
		regex=re.compile(
			r"\b(?:working\s+tree|tree)(?:\s+is)?\s+clean\b"
			r"|\bno\s+(?:un(?:committed|staged|tracked))(?:\s+(?:files?|changes?))?\b"
			r"|\bno\s+dirty\b",
			re.IGNORECASE,
		),
		check=_check_working_tree,
	),
	# Branch in sync with origin / pushed / no unpushed
	_Pattern(
		name="branch_in_sync",
		regex=re.compile(
			r"\b(?:pushed|in\s+sync)\s+(?:to|with)\s+(?:origin|upstream)\b"
			r"|\bno\s+unpushed\b"
			r"|\bbranch\s+(?:in\s+sync|at\s+parity)\b"
			r"|\bup\s*to\s*date\s+with\s+(?:origin|upstream)\b",
			re.IGNORECASE,
		),
		check=_check_branch_sync,
	),
	# Branch is main/master/trunk
	_Pattern(
		name="branch_is_main",
		regex=re.compile(
			r"\bon\s+(?:main|master|trunk)(?:\s+branch)?\b"
			r"|\bbranch\s+(?:is\s+)?(?:main|master|trunk)\b",
			re.IGNORECASE,
		),
		check=_check_branch_is_main,
	),
	# No orphan thread
	_Pattern(
		name="no_orphan_thread",
		regex=re.compile(
			r"\bno\s+orphan(?:ed)?\s+(?:thread|prompt)s?\b"
			r"|\bno\s+(?:dangling|loose)\s+(?:thread|prompt)s?\b"
			r"|\bevery\s+prompt\s+(?:has|got)\s+a\s+(?:follow[- ]?up\s+)?commit\b",
			re.IGNORECASE,
		),
		check=_check_orphan_thread,
	),
	# No plan drift
	_Pattern(
		name="no_plan_drift",
		regex=re.compile(
			r"\bno\s+plan[\s-]?drift\b"
			r"|\bplan\s+(?:doc(?:ument)?\s+)?(?:matches|consistent\s+with)\s+(?:reality|code)\b",
			re.IGNORECASE,
		),
		check=_check_plan_drift,
	),
	# Plan / all phases / all checkboxes complete (catch-all for "plan done")
	_Pattern(
		name="plan_complete",
		regex=re.compile(
			r"\bplan\s+(?:is\s+)?(?:complete|done|finished)\b"
			r"|\ball\s+(?:plan\s+)?(?:phases?|items?|checkboxes?)\s+"
			r"(?:complete|completed|done|checked|ticked)\b"
			r"|\bevery\s+(?:plan\s+)?(?:phase|item|checkbox|checkboxes)\s+"
			r"(?:complete|completed|done|checked|ticked)\b",
			re.IGNORECASE,
		),
		check=_check_plan_complete,
	),
	# Substantive prompts (not just procedural approvals)
	_Pattern(
		name="prompts_substantive",
		regex=re.compile(
			r"\bsubstantive\s+prompts?\b"
			r"|\bno\s+procedural[- ]only\s+prompts?\b"
			r"|\bdirected\s+by\s+real\s+prompts?\b",
			re.IGNORECASE,
		),
		check=_check_substantive_prompts,
	),
)


def match_pattern(text: str) -> _Pattern | None:
	"""Return the first auto-check pattern whose regex matches `text`."""
	for pat in _PATTERNS:
		if pat.regex.search(text):
			return pat
	return None


# ───── parsing ───────────────────────────────────────────────────────────────

# The set of filenames we recognize as a Definition-of-Done file. Matching is
# case-insensitive, so DOD.md / dod.md / Dod.md all work.
_DOD_FILENAMES: tuple[str, ...] = (
	"DOD.md",
	"DEFINITION_OF_DONE.md",
	"DEFINITION-OF-DONE.md",
)

_CHECKBOX_RE = re.compile(
	r"^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$",
)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def find_dod_file(project: Path, *, override: Path | None = None) -> Path | None:
	"""Locate the project's Definition-of-Done file, if any.

	Honors `override` first (explicit `--file`). Otherwise scans the project
	root case-insensitively for one of `_DOD_FILENAMES`.
	"""
	if override is not None:
		return override if override.is_file() else None
	if not project.is_dir():
		return None
	targets = {n.lower() for n in _DOD_FILENAMES}
	try:
		for child in project.iterdir():
			if child.is_file() and child.name.lower() in targets:
				return child
	except OSError:
		return None
	return None


def _strip_md_chrome(text: str) -> str:
	text = _BOLD_RE.sub(r"\1", text)
	text = _ITALIC_RE.sub(r"\1", text)
	text = _INLINE_CODE_RE.sub(r"\1", text)
	return text.strip()


def parse_dod_file(path: Path) -> list[tuple[str, bool]]:
	"""Read a DOD file and return `(criterion_text, user_checked)` tuples.

	Lines that are not checkbox items are ignored — the rest of the file
	can be free-form prose, headings, comments, whatever the author likes.
	"""
	try:
		text = path.read_text(errors="replace")
	except OSError:
		return []
	items: list[tuple[str, bool]] = []
	for line in text.splitlines():
		m = _CHECKBOX_RE.match(line)
		if not m:
			continue
		raw = m.group("text")
		mark = m.group("mark").lower()
		items.append((_strip_md_chrome(raw), mark == "x"))
	return items


# ───── evaluation ────────────────────────────────────────────────────────────

def evaluate_items(report: ProjectReport,
                   items: Iterable[tuple[str, bool]]) -> list[Criterion]:
	"""Apply auto-checks to each parsed item.

	A `[x]` line is taken at face value (DONE) — the user has hand-confirmed
	it, and we don't undermine that even if our auto-check would say
	otherwise. Auditors who want strict checking can keep items unchecked
	and rely on `PASS`.
	"""
	out: list[Criterion] = []
	for text, user_checked in items:
		if user_checked:
			out.append(Criterion(
				text=text, user_checked=True,
				status="DONE", detail="user-confirmed",
			))
			continue
		pat = match_pattern(text)
		if pat is None:
			out.append(Criterion(
				text=text, user_checked=False,
				status="MANUAL",
				detail="no auto-check matched; mark `[x]` when you confirm by hand",
			))
			continue
		cr = pat.check(report)
		out.append(Criterion(
			text=text, user_checked=False,
			status=cr.status, detail=cr.detail, auto_check=pat.name,
		))
	return out


def _next_action(criteria: Iterable[Criterion]) -> str:
	"""Pick a single, concrete next step from the criteria list."""
	fails = [c for c in criteria if c.status == "FAIL"]
	manuals = [c for c in criteria if c.status == "MANUAL"]
	if fails:
		first = fails[0]
		hint = f": {first.detail}" if first.detail else ""
		return f"Address `{first.text}`{hint}"
	if manuals:
		first = manuals[0]
		return f"Confirm by hand and tick `{first.text}` in DOD.md"
	return "Definition of Done met."


def evaluate(report: ProjectReport, *,
             override_file: Path | None = None) -> DoDResult:
	"""Build a complete `DoDResult` for one project."""
	dod_path = find_dod_file(report.path, override=override_file)
	if dod_path is None:
		return DoDResult(
			project=report.name, file_path="", file_exists=False,
			criteria=(),
			next_action=("Write a DOD.md at the project root with `- [ ] criterion` "
			             "items to start tracking definition of done."),
		)
	items = parse_dod_file(dod_path)
	criteria = tuple(evaluate_items(report, items))
	try:
		rel = dod_path.relative_to(report.path)
		rel_str = str(rel)
	except ValueError:
		rel_str = dod_path.name
	if not items:
		next_action = (f"`{rel_str}` has no `- [ ]` items; add criteria for the "
		               "tool to track.")
	else:
		next_action = _next_action(criteria)
	return DoDResult(
		project=report.name, file_path=rel_str, file_exists=True,
		criteria=criteria, next_action=next_action,
	)


def evaluate_all(reports: Iterable[ProjectReport]) -> list[DoDResult]:
	return [evaluate(r) for r in reports]


# ───── rendering ─────────────────────────────────────────────────────────────

_STATUS_COLOR = {
	"PASS":   "green",
	"FAIL":   "red",
	"DONE":   "green",
	"MANUAL": "yellow",
	"SKIP":   "dim",
}


def _bar(complete: int, total: int, width: int = 24) -> str:
	if total <= 0:
		return "[" + " " * width + "]"
	filled = int(round(width * complete / total))
	filled = max(0, min(width, filled))
	return "[" + "\u2588" * filled + "\u2591" * (width - filled) + "]"


def render_terminal(result: DoDResult, console: Console) -> None:
	"""Single-project terminal view with progress bar and criteria table."""
	console.print()
	if not result.file_exists:
		console.print(f"[bold cyan]{result.project}[/bold cyan]  "
		              f"[bold yellow]NO DOD[/bold yellow]")
		console.print()
		console.print(f"  [dim]{_rich_escape(result.next_action)}[/dim]")
		return

	bar = _bar(result.complete, result.total_relevant)
	pct = result.percent
	header_color = "green" if result.is_complete else (
		"yellow" if any(c.status == "MANUAL" for c in result.criteria)
		           and not any(c.status == "FAIL" for c in result.criteria)
		else "red" if any(c.status == "FAIL" for c in result.criteria)
		else "cyan"
	)
	verdict = "DONE" if result.is_complete else "OPEN"
	skipped = f"  [dim](skipped {result.skipped})[/dim]" if result.skipped else ""

	console.print(f"[bold cyan]{result.project}[/bold cyan]  "
	              f"[bold {header_color}]DoD {verdict}[/bold {header_color}]  "
	              f"[dim]{result.file_path}[/dim]")
	console.print()
	console.print(f"  {bar}  [bold]{result.complete}/{result.total_relevant}[/bold]"
	              f"  [dim]({pct}%)[/dim]{skipped}")
	console.print()
	for c in result.criteria:
		color = _STATUS_COLOR[c.status]
		text = _rich_escape(c.text)
		detail = f"  [dim]{_rich_escape(c.detail)}[/dim]" if c.detail else ""
		console.print(f"  [{color}][{c.status:>6}][/{color}]  {text}{detail}")
	if not result.is_complete:
		console.print()
		console.print(f"  [bold]Next:[/bold] {_rich_escape(result.next_action)}")


def render_fleet_terminal(results: list[DoDResult], console: Console) -> None:
	"""Fleet view: one summary line per project that has a DOD.md.

	Projects without a DOD.md are listed compactly at the end so the user
	knows the universe of projects considered, without burying the rows
	that actually have criteria.
	"""
	with_dod = [r for r in results if r.file_exists]
	without_dod = [r for r in results if not r.file_exists]

	if not with_dod:
		console.print()
		console.print(f"[dim]None of {len(results)} project(s) have a DOD.md.[/dim]")
		console.print("[dim]Drop a `- [ ] criterion` checklist at the project "
		              "root to start tracking.[/dim]")
		return

	console.print()
	any_open = False
	for r in sorted(with_dod, key=lambda r: (r.is_complete, -r.percent, r.project)):
		bar = _bar(r.complete, r.total_relevant, width=12)
		if r.is_complete:
			tag = "[green]DONE[/green]"
		else:
			any_open = True
			tag = "[yellow]OPEN[/yellow]" if r.outstanding == r.total_relevant - r.complete \
			      and not any(c.status == "FAIL" for c in r.criteria) \
			      else "[red]OPEN[/red]"
		console.print(f"  {tag}  {bar}  "
		              f"[bold]{r.complete}/{r.total_relevant}[/bold]  "
		              f"[cyan]{_rich_escape(r.project)}[/cyan]  "
		              f"[dim]{_rich_escape(r.next_action)}[/dim]")
	console.print()
	complete_n = sum(1 for r in with_dod if r.is_complete)
	console.print(f"[dim]{complete_n} of {len(with_dod)} project(s) with DOD.md "
	              f"are complete.[/dim]")
	if without_dod:
		names = ", ".join(r.project for r in without_dod[:10])
		more = "" if len(without_dod) <= 10 else f" (+{len(without_dod) - 10} more)"
		console.print(f"[dim]{len(without_dod)} project(s) have no DOD.md: "
		              f"{names}{more}[/dim]")
	if any_open:
		# Trailing newline for shell-friendliness
		console.print()


def render_json(results: list[DoDResult] | DoDResult) -> str:
	def to_dict(r: DoDResult) -> dict:
		return {
			"project": r.project,
			"file_path": r.file_path,
			"file_exists": r.file_exists,
			"complete": r.complete,
			"outstanding": r.outstanding,
			"skipped": r.skipped,
			"total_relevant": r.total_relevant,
			"percent": r.percent,
			"is_complete": r.is_complete,
			"next_action": r.next_action,
			"criteria": [
				{
					"text": c.text,
					"user_checked": c.user_checked,
					"status": c.status,
					"detail": c.detail,
					"auto_check": c.auto_check,
				}
				for c in r.criteria
			],
		}
	if isinstance(results, DoDResult):
		return json.dumps(to_dict(results), indent=2)
	return json.dumps([to_dict(r) for r in results], indent=2)


def render_markdown(results: list[DoDResult] | DoDResult) -> str:
	if isinstance(results, DoDResult):
		results = [results]
	lines: list[str] = []
	for r in results:
		lines.append(f"## {r.project} \u2014 Definition of Done")
		lines.append("")
		if not r.file_exists:
			lines.append(f"_No DOD.md found._ {r.next_action}")
			lines.append("")
			continue
		verdict = "DONE" if r.is_complete else "OPEN"
		lines.append(f"**Status:** {verdict} \u00b7 {r.complete}/{r.total_relevant} "
		             f"({r.percent}%) \u00b7 source: `{r.file_path}`")
		lines.append("")
		lines.append("| Status | Criterion | Detail |")
		lines.append("|---|---|---|")
		for c in r.criteria:
			detail = c.detail.replace("|", "\\|")
			text = c.text.replace("|", "\\|")
			lines.append(f"| `{c.status}` | {text} | {detail} |")
		lines.append("")
		if not r.is_complete:
			lines.append(f"**Next:** {r.next_action}")
			lines.append("")
	return "\n".join(lines).rstrip() + "\n"


# ───── subcommand wiring ─────────────────────────────────────────────────────

def add_subparser(subparsers) -> argparse.ArgumentParser:
	parser = subparsers.add_parser(
		"dod",
		help="Diff each project's perceived state against its DOD.md.",
		description="Read a per-project DOD.md checklist (Definition of "
		            "Done), auto-evaluate the criteria the tool recognizes, "
		            "and report progress with structured PASS/FAIL/DONE/"
		            "MANUAL/SKIP status. Exits non-zero if any relevant "
		            "criterion is outstanding.",
	)
	from .cli import _add_common_scan_args
	_add_common_scan_args(parser)
	parser.add_argument(
		"--file", type=Path, default=None,
		help="path to a DOD file (overrides DOD.md auto-discovery); "
		     "only meaningful with --project NAME selecting a single project",
	)
	parser.add_argument("--format", choices=["terminal", "json", "markdown"],
	                    default="terminal")
	parser.set_defaults(func=run)
	return parser


def run(args: argparse.Namespace) -> int:
	from .cli import build_reports
	console = Console(no_color=args.no_color, soft_wrap=False)

	reports = build_reports(args)
	if reports is None:
		return 1

	override = args.file
	if override is not None and len(reports) != 1:
		print("--file requires --project NAME selecting a single project.",
		      file=sys.stderr)
		return 2

	results = [evaluate(r, override_file=override) for r in reports]

	if args.format == "json":
		if len(results) == 1:
			sys.stdout.write(render_json(results[0]) + "\n")
		else:
			sys.stdout.write(render_json(results) + "\n")
	elif args.format == "markdown":
		sys.stdout.write(render_markdown(results))
	else:
		if len(results) == 1:
			render_terminal(results[0], console)
		else:
			render_fleet_terminal(results, console)

	# Exit non-zero when any relevant DOD has outstanding work, mirroring verify.
	any_outstanding = any(r.file_exists and not r.is_complete and r.total_relevant > 0
	                      for r in results)
	return 1 if any_outstanding else 0
