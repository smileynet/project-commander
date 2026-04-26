"""Project hygiene actions: init missing repos, checkpoint stale dirty trees,
optionally sync upstreams or push.

Designed to be safe-by-default. The only opinionated defaults are:
- `--init` ON: folders with content but no `.git/` get initialized + committed.
- `--commit-stale` ON: dirty trees idle >= `--stale-age` days get checkpointed.
- `--sync` OFF: never modify state without being asked.
- `--push` OFF: never reach out to the network without being asked.

When `--push` is enabled, this module REFUSES to push any branch that has a
`Project-Commander-Hygiene: true` trailer in its unpushed commit range.
That trailer marks every commit this module produces, so the user's pushed
history stays clean of hygiene churn.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import discovery
from .models import ProjectReport
from .sources.git import GitScanner


HYGIENE_TRAILER = "Project-Commander-Hygiene: true"


class Action(str, Enum):
	INIT = "init"
	COMMIT_STALE = "commit-stale"
	FETCH = "fetch"
	PUSH = "push"


@dataclass
class TidyConfig:
	init: bool = True
	commit_stale: bool = True
	stale_age_days: int = 7
	sync: bool = False
	push: bool = False
	dry_run: bool = False


@dataclass
class PlannedAction:
	project: Path
	name: str
	action: Action
	reason: str
	detail: str = ""


@dataclass
class ExecutedAction:
	planned: PlannedAction
	ok: bool
	output: str = ""
	error: str = ""

	@property
	def display(self) -> str:
		if self.error and not self.ok:
			return f"FAILED: {self.error.strip().splitlines()[0] if self.error else 'unknown error'}"
		return self.output or "ok"


# ---------- planning (pure) ----------

def plan(report: ProjectReport, *, config: TidyConfig, now: datetime) -> list[PlannedAction]:
	"""Decide what actions apply to a single project, without executing.

	Pure function — same inputs always yield the same plan.
	"""
	actions: list[PlannedAction] = []

	if not report.is_git_repo and config.init:
		if _has_visible_content(report.path):
			actions.append(PlannedAction(
				project=report.path, name=report.name,
				action=Action.INIT,
				reason="no git repo; folder has content",
			))

	if report.is_git_repo and report.git_dirty and config.commit_stale:
		if report.last_active is not None:
			days = (now - report.last_active).days
			if days >= config.stale_age_days:
				actions.append(PlannedAction(
					project=report.path, name=report.name,
					action=Action.COMMIT_STALE,
					reason=f"dirty tree; idle {days}d",
					detail=str(days),
				))

	if config.sync and report.is_git_repo:
		actions.append(PlannedAction(
			project=report.path, name=report.name,
			action=Action.FETCH,
			reason="sync requested",
		))

	if config.push and report.is_git_repo:
		actions.append(PlannedAction(
			project=report.path, name=report.name,
			action=Action.PUSH,
			reason="push requested (will skip branches with hygiene commits)",
		))

	return actions


def _has_visible_content(p: Path) -> bool:
	"""True if the directory has any non-hidden entry."""
	try:
		return any(not e.name.startswith(".") for e in p.iterdir())
	except OSError:
		return False


# ---------- execution ----------

def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
	res = subprocess.run(
		["git", *args], cwd=cwd, check=False, capture_output=True, text=True,
	)
	return res.returncode, res.stdout, res.stderr


def _initial_commit_message() -> str:
	return (
		"chore(auto): initial commit\n"
		"\n"
		"Created by project-commander hygiene because the folder had content\n"
		"but no git repository. This is a checkpoint, not curated work.\n"
		"\n"
		f"{HYGIENE_TRAILER}\n"
	)


def _stale_commit_message(days: str, shortstat: str) -> str:
	body = [
		f"chore(auto): hygiene checkpoint after {days}d idle",
		"",
		"Auto-committed by project-commander because the working tree was",
		f"dirty and the project had been quiet for {days} day(s). This is a",
		"checkpoint, not curated work \u2014 review and reorganize as you see fit.",
	]
	if shortstat:
		body.extend(["", shortstat])
	body.extend(["", HYGIENE_TRAILER, ""])
	return "\n".join(body)


def execute_init(p: PlannedAction, *, dry_run: bool) -> ExecutedAction:
	if dry_run:
		return ExecutedAction(planned=p, ok=True, output="(dry-run) would init + initial commit")
	for cmd in (["init", "--quiet"], ["add", "-A"]):
		rc, out, err = _git(cmd, p.project)
		if rc != 0:
			return ExecutedAction(planned=p, ok=False, output=out, error=err)
	rc, out, err = _git(
		["commit", "--no-verify", "-m", _initial_commit_message()],
		p.project,
	)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, output=out, error=err)
	return ExecutedAction(planned=p, ok=True, output="initialized + committed")


def execute_commit_stale(p: PlannedAction, *, dry_run: bool) -> ExecutedAction:
	# shortstat helps the message be informative
	rc, shortstat, _ = _git(["diff", "--shortstat", "HEAD"], p.project)
	shortstat = shortstat.strip() if rc == 0 else ""
	# also include untracked files
	rc, untracked, _ = _git(["ls-files", "--others", "--exclude-standard"], p.project)
	new_count = len([line for line in untracked.splitlines() if line.strip()]) if rc == 0 else 0
	stat_line = shortstat
	if new_count:
		stat_line = (stat_line + f"; {new_count} new file(s)").lstrip("; ")

	if dry_run:
		hint = f" ({stat_line})" if stat_line else ""
		return ExecutedAction(planned=p, ok=True, output=f"(dry-run) would commit{hint}")

	rc, _, err = _git(["add", "-A"], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, error=err)
	# nothing-to-commit guard
	rc, status, _ = _git(["status", "--porcelain"], p.project)
	if rc == 0 and not status.strip():
		return ExecutedAction(planned=p, ok=True, output="nothing to commit (post-stage)")

	msg = _stale_commit_message(p.detail, stat_line)
	rc, out, err = _git(["commit", "--no-verify", "-m", msg], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, output=out, error=err)
	return ExecutedAction(planned=p, ok=True, output=f"committed ({stat_line or 'no shortstat'})")


def execute_fetch(p: PlannedAction, *, dry_run: bool) -> ExecutedAction:
	rc, remotes, _ = _git(["remote"], p.project)
	if rc != 0 or not remotes.strip():
		return ExecutedAction(planned=p, ok=True, output="no remote; skipped")
	if dry_run:
		return ExecutedAction(planned=p, ok=True, output="(dry-run) would fetch all remotes")
	rc, out, err = _git(["fetch", "--all", "--quiet"], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, output=out, error=err)
	return ExecutedAction(planned=p, ok=True, output="fetched")


def execute_push(p: PlannedAction, *, dry_run: bool) -> ExecutedAction:
	rc, branch, _ = _git(["branch", "--show-current"], p.project)
	branch = branch.strip() if rc == 0 else ""
	if not branch:
		return ExecutedAction(planned=p, ok=True, output="detached HEAD; skipped")

	rc, upstream, _ = _git(["rev-parse", "--abbrev-ref", "@{upstream}"], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=True, output="no upstream; skipped")

	rc, log, err = _git(["log", "--format=%H", f"{upstream.strip()}..HEAD"], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, output=log, error=err)
	unpushed = [s for s in log.splitlines() if s.strip()]
	if not unpushed:
		return ExecutedAction(planned=p, ok=True, output="up-to-date with upstream")

	hygiene = 0
	for sha in unpushed:
		rc, body, _ = _git(["log", "-1", "--format=%B", sha], p.project)
		if rc == 0 and HYGIENE_TRAILER in body:
			hygiene += 1
	if hygiene > 0:
		return ExecutedAction(
			planned=p, ok=True,
			output=f"refused: {hygiene}/{len(unpushed)} unpushed are hygiene commits "
				   f"(push manually if intended)",
		)

	if dry_run:
		return ExecutedAction(
			planned=p, ok=True,
			output=f"(dry-run) would push {len(unpushed)} commit(s) to {upstream.strip()}",
		)
	rc, out, err = _git(["push"], p.project)
	if rc != 0:
		return ExecutedAction(planned=p, ok=False, output=out, error=err)
	return ExecutedAction(planned=p, ok=True, output=f"pushed {len(unpushed)} commit(s)")


_EXECUTORS = {
	Action.INIT: execute_init,
	Action.COMMIT_STALE: execute_commit_stale,
	Action.FETCH: execute_fetch,
	Action.PUSH: execute_push,
}


def execute(p: PlannedAction, *, dry_run: bool) -> ExecutedAction:
	return _EXECUTORS[p.action](p, dry_run=dry_run)


# ---------- discovery for tidy ----------

def _build_reports(*, root: Path, only: list[str], exclude: list[str]) -> list[ProjectReport]:
	"""Build minimal ProjectReports for tidy: we only need git state and last_active.

	We do NOT run all source scanners here — tidy needs git state and a rough
	last-active timestamp, and it would be wasteful (and slow) to walk every
	agent's session store just to make a hygiene decision. last_active is
	derived from filesystem mtimes within the project tree.
	"""
	projects = discovery.discover_projects(root)
	projects = discovery.filter_projects(projects, only=only or None, exclude=exclude or None)
	git = GitScanner()
	out: list[ProjectReport] = []
	for p in projects:
		is_repo = git.is_repo(p)
		branch = git.branch(p) if is_repo else None
		dirty = git.is_dirty(p) if is_repo else False
		last_active = _last_mtime(p)
		signals = []  # not needed for tidy decisions
		report = ProjectReport(
			path=p, name=p.name, signals=signals,
			is_git_repo=is_repo, git_branch=branch, git_dirty=dirty,
		)
		# stash a synthetic last_active by patching a single signal
		if last_active is not None:
			from .models import Signal  # local to avoid circular at module top
			report.signals.append(Signal(
				source="fs", kind="filesystem", timestamp=last_active,
				summary="latest mtime", ref="",
			))
		out.append(report)
	return out


def _last_mtime(project: Path) -> datetime | None:
	"""Return the most recent file mtime within the project, ignoring `.git/`
	internals and common large/transient dirs. Used as a proxy for activity.
	"""
	skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__",
				 ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build"}
	latest: float | None = None
	try:
		for entry in project.rglob("*"):
			# skip walking into excluded dirs
			parts = set(entry.relative_to(project).parts)
			if parts & skip_dirs:
				continue
			try:
				mtime = entry.stat().st_mtime
			except OSError:
				continue
			if latest is None or mtime > latest:
				latest = mtime
	except OSError:
		return None
	if latest is None:
		return None
	return datetime.fromtimestamp(latest, tz=timezone.utc)


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		prog="project-commander tidy",
		description="Project hygiene: init missing repos, checkpoint stale work, optionally sync.",
	)
	parser.add_argument("--root", type=Path, default=None,
						help="root to scan (default: ~/code)")
	parser.add_argument("--project", action="append", default=[],
						help="basename glob; repeatable")
	parser.add_argument("--exclude", action="append", default=[],
						help="basename glob to exclude; repeatable")
	parser.add_argument("--no-init", dest="init", action="store_false", default=True,
						help="don't init folders missing a git repo")
	parser.add_argument("--no-commit", dest="commit_stale", action="store_false", default=True,
						help="don't commit stale dirty trees")
	parser.add_argument("--stale-age", type=int, default=7,
						help="minimum idle days for stale-commit (default: 7)")
	parser.add_argument("--sync", action="store_true", default=False,
						help="run `git fetch --all` per repo")
	parser.add_argument("--push", action="store_true", default=False,
						help="push branches when no hygiene commits are in the unpushed range")
	parser.add_argument("--dry-run", action="store_true", default=False,
						help="show planned actions without executing")
	parser.add_argument("--no-color", action="store_true", default=False)
	args = parser.parse_args(argv)

	root = (args.root or Path.home() / "code").expanduser().resolve()
	config = TidyConfig(
		init=args.init,
		commit_stale=args.commit_stale,
		stale_age_days=args.stale_age,
		sync=args.sync,
		push=args.push,
		dry_run=args.dry_run,
	)
	console = Console(no_color=args.no_color, soft_wrap=False)

	reports = _build_reports(root=root, only=args.project, exclude=args.exclude)
	if not reports:
		console.print(f"[yellow]no projects found under {root}[/yellow]")
		return 1

	now = datetime.now(tz=timezone.utc)
	all_planned: list[PlannedAction] = []
	for r in reports:
		all_planned.extend(plan(r, config=config, now=now))

	# Header
	verb = "Would tidy" if config.dry_run else "Tidying"
	console.rule(f"[bold cyan]{verb} {root}")
	bullets = []
	if config.init:
		bullets.append("init")
	if config.commit_stale:
		bullets.append(f"commit-stale (>= {config.stale_age_days}d)")
	if config.sync:
		bullets.append("sync")
	if config.push:
		bullets.append("push")
	console.print(f"[dim]Active: {', '.join(bullets) or '(nothing enabled)'}[/dim]")
	console.print()

	if not all_planned:
		console.print("[green]No hygiene actions needed.[/green]")
		return 0

	# Plan -> execute, render row by row
	results: list[ExecutedAction] = []
	table = Table(show_lines=False, expand=False)
	table.add_column("Action", style="bold")
	table.add_column("Project", style="cyan")
	table.add_column("Reason", style="dim")
	table.add_column("Outcome")
	for p in all_planned:
		exec_result = execute(p, dry_run=config.dry_run)
		results.append(exec_result)
		style = "green" if exec_result.ok else "red"
		table.add_row(
			p.action.value,
			p.name,
			p.reason,
			f"[{style}]{exec_result.display}[/{style}]",
		)
	console.print(table)

	# Summary
	by_action: dict[Action, tuple[int, int]] = {}  # (ok, fail)
	for r in results:
		ok, fail = by_action.get(r.planned.action, (0, 0))
		if r.ok:
			by_action[r.planned.action] = (ok + 1, fail)
		else:
			by_action[r.planned.action] = (ok, fail + 1)
	parts = []
	for a, (ok, fail) in by_action.items():
		if fail:
			parts.append(f"{a.value}: {ok} ok, {fail} failed")
		else:
			parts.append(f"{a.value}: {ok}")
	console.print()
	console.print(f"[bold]Summary[/bold] — {'; '.join(parts) if parts else 'nothing'}")
	if config.dry_run:
		console.print("[dim](dry-run; nothing was executed)[/dim]")

	# Exit code: nonzero if any failures
	any_failed = any(not r.ok for r in results)
	return 1 if any_failed else 0


if __name__ == "__main__":
	raise SystemExit(main())
