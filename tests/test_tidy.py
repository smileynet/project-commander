"""Tests for the tidy module.

Covers the pure planner (no subprocess) and the push-refusal logic
(integration: builds a real local git repo with hygiene + work commits).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from project_commander.models import ProjectReport, Signal
from project_commander.tidy import (
	HYGIENE_TRAILER,
	Action,
	PlannedAction,
	TidyConfig,
	execute_push,
	plan,
)


def _ts(days_ago: int) -> datetime:
	return datetime.now(tz=timezone.utc) - timedelta(days=days_ago)


def _report(*, path: Path, name: str = "p", is_git: bool = True,
			dirty: bool = False, last_active_days: int | None = None) -> ProjectReport:
	signals = []
	if last_active_days is not None:
		signals.append(Signal(
			source="git", kind="commit", timestamp=_ts(last_active_days),
			summary="x", ref="abc",
		))
	return ProjectReport(
		path=path, name=name, signals=signals,
		is_git_repo=is_git, git_branch="main" if is_git else None, git_dirty=dirty,
	)


def _git(args: list[str], cwd: Path) -> str:
	res = subprocess.run(
		["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
		env={
			"HOME": str(cwd),  # isolate from user's gitconfig
			"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
			"GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
			"PATH": "/usr/bin:/bin:/usr/local/bin",
		},
	)
	return res.stdout


# ---------- planner ----------

def test_plan_init_for_non_git_with_content(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	(p / "README.md").write_text("hi")
	report = _report(path=p, is_git=False)
	cfg = TidyConfig()  # init=True default
	now = datetime.now(tz=timezone.utc)
	actions = plan(report, config=cfg, now=now)
	assert [a.action for a in actions] == [Action.INIT]


def test_plan_no_init_for_empty_folder(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	report = _report(path=p, is_git=False)
	actions = plan(report, config=TidyConfig(), now=datetime.now(tz=timezone.utc))
	assert actions == []


def test_plan_no_init_for_hidden_only(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	(p / ".DS_Store").write_text("")
	actions = plan(_report(path=p, is_git=False), config=TidyConfig(),
				   now=datetime.now(tz=timezone.utc))
	assert actions == []


def test_plan_commit_stale_threshold(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	# Dirty + idle for 8 days -> trigger
	r = _report(path=p, dirty=True, last_active_days=8)
	cfg = TidyConfig(stale_age_days=7)
	acts = plan(r, config=cfg, now=datetime.now(tz=timezone.utc))
	assert any(a.action == Action.COMMIT_STALE for a in acts)
	assert next(a for a in acts if a.action == Action.COMMIT_STALE).detail == "8"


def test_plan_no_commit_when_fresh(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	r = _report(path=p, dirty=True, last_active_days=2)
	cfg = TidyConfig(stale_age_days=7)
	acts = plan(r, config=cfg, now=datetime.now(tz=timezone.utc))
	assert not any(a.action == Action.COMMIT_STALE for a in acts)


def test_plan_no_commit_when_clean(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	r = _report(path=p, dirty=False, last_active_days=30)
	acts = plan(r, config=TidyConfig(), now=datetime.now(tz=timezone.utc))
	assert not any(a.action == Action.COMMIT_STALE for a in acts)


def test_plan_disabled_flags_emit_nothing(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	(p / "f").write_text("x")
	r = _report(path=p, is_git=False)
	# init disabled => no plan even though there's content
	acts = plan(r, config=TidyConfig(init=False), now=datetime.now(tz=timezone.utc))
	assert acts == []


def test_plan_sync_and_push_only_when_repo(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	r = _report(path=p, is_git=False)
	cfg = TidyConfig(sync=True, push=True, init=False, commit_stale=False)
	# is_git=False, so neither fetch nor push gets queued
	assert plan(r, config=cfg, now=datetime.now(tz=timezone.utc)) == []


def test_plan_sync_queued_for_repo(tmp_path):
	p = tmp_path / "proj"
	p.mkdir()
	r = _report(path=p)
	cfg = TidyConfig(init=False, commit_stale=False, sync=True)
	acts = plan(r, config=cfg, now=datetime.now(tz=timezone.utc))
	assert [a.action for a in acts] == [Action.FETCH]


# ---------- push refusal ----------

def _init_repo_with(commits: list[tuple[str, bool]], tmp_path: Path) -> Path:
	"""Create a local repo with the given commits.

	Each commit is (subject, is_hygiene). The first repo created becomes the
	'remote' (a bare clone). The working repo is `tmp_path/work` and tracks
	an upstream.
	"""
	upstream = tmp_path / "upstream.git"
	work = tmp_path / "work"
	# Create the bare upstream
	subprocess.run(["git", "init", "--bare", "--quiet", str(upstream)], check=True)
	# Clone
	subprocess.run(["git", "clone", "--quiet", str(upstream), str(work)], check=True,
				   env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"})
	# Identity
	subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
	subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
	# initial commit so we can push the branch
	(work / "f").write_text("x")
	subprocess.run(["git", "add", "."], cwd=work, check=True)
	subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=work, check=True)
	# rename branch to main and push
	subprocess.run(["git", "branch", "-M", "main"], cwd=work, check=True)
	subprocess.run(["git", "push", "--quiet", "-u", "origin", "main"], cwd=work, check=True)
	# Now add the requested commits
	for subject, is_hygiene in commits:
		(work / "f").write_text((work / "f").read_text() + "\n" + subject)
		subprocess.run(["git", "add", "."], cwd=work, check=True)
		msg = subject + ("\n\n" + HYGIENE_TRAILER + "\n" if is_hygiene else "")
		subprocess.run(["git", "commit", "--quiet", "-m", msg], cwd=work, check=True)
	return work


def test_push_refused_when_hygiene_in_unpushed(tmp_path):
	work = _init_repo_with([("hygiene fix", True), ("real work", False)], tmp_path)
	planned = PlannedAction(project=work, name="work", action=Action.PUSH, reason="test")
	r = execute_push(planned, dry_run=False)
	assert r.ok is True  # not a failure — refusal is the success path
	assert "refused" in r.output
	assert "hygiene" in r.output


def test_push_proceeds_when_no_hygiene(tmp_path):
	work = _init_repo_with([("real work A", False), ("real work B", False)], tmp_path)
	planned = PlannedAction(project=work, name="work", action=Action.PUSH, reason="test")
	r = execute_push(planned, dry_run=True)
	assert r.ok is True
	assert "would push" in r.output
	assert "2 commit" in r.output


def test_push_skipped_when_uptodate(tmp_path):
	work = _init_repo_with([], tmp_path)
	planned = PlannedAction(project=work, name="work", action=Action.PUSH, reason="test")
	r = execute_push(planned, dry_run=False)
	assert r.ok is True
	assert "up-to-date" in r.output


@pytest.mark.parametrize("git_arg", [["--version"]])
def test_git_present(git_arg):
	# sanity: the tests above need git; fail loudly if it's not installed
	subprocess.run(["git", *git_arg], check=True, capture_output=True)



# ---------- prune ----------

def test_prune_archives_dormant_clean_project(tmp_path):
	p = tmp_path / "old-experiment"
	p.mkdir()
	report = _report(path=p, is_git=True, dirty=False, last_active_days=120)
	cfg = TidyConfig(prune=True, prune_age_days=90,
	                 init=False, commit_stale=False)
	now = datetime.now(tz=timezone.utc)
	actions = plan(report, config=cfg, now=now)
	archive = [a for a in actions if a.action == Action.ARCHIVE]
	assert len(archive) == 1
	assert "dormant 120d" in archive[0].reason
	# Default destination is <project.parent>/archive/<name>
	assert archive[0].detail.endswith("/archive/old-experiment")


def test_prune_holds_when_working_tree_dirty(tmp_path):
	p = tmp_path / "messy"
	p.mkdir()
	report = _report(path=p, is_git=True, dirty=True, last_active_days=200)
	cfg = TidyConfig(prune=True, init=False, commit_stale=False)
	now = datetime.now(tz=timezone.utc)
	actions = plan(report, config=cfg, now=now)
	assert [a.action for a in actions] == [Action.HOLD]
	assert "dirty" in actions[0].reason


def test_prune_holds_when_branch_ahead(tmp_path):
	p = tmp_path / "unpushed"
	p.mkdir()
	sig = Signal(source="git", kind="commit",
	             timestamp=_ts(200), summary="x", ref="abc")
	report = ProjectReport(
		path=p, name="unpushed", is_git_repo=True, git_branch="main",
		git_ahead=2, signals=[sig],
	)
	cfg = TidyConfig(prune=True, init=False, commit_stale=False)
	actions = plan(report, config=cfg, now=datetime.now(tz=timezone.utc))
	assert [a.action for a in actions] == [Action.HOLD]
	assert "unpushed" in actions[0].reason


def test_prune_skips_already_archived(tmp_path):
	root = tmp_path / "archive" / "old"
	root.mkdir(parents=True)
	report = _report(path=root, name="old", is_git=True, last_active_days=200)
	cfg = TidyConfig(prune=True, init=False, commit_stale=False)
	actions = plan(report, config=cfg, now=datetime.now(tz=timezone.utc))
	# Path already contains 'archive' \u2014 do not double-archive
	assert actions == []


def test_prune_skips_when_under_age_threshold(tmp_path):
	p = tmp_path / "recent"
	p.mkdir()
	report = _report(path=p, is_git=True, last_active_days=30)
	cfg = TidyConfig(prune=True, prune_age_days=90, init=False, commit_stale=False)
	actions = plan(report, config=cfg, now=datetime.now(tz=timezone.utc))
	assert actions == []


def test_prune_uses_explicit_archive_dir(tmp_path):
	p = tmp_path / "cold"
	p.mkdir()
	custom = tmp_path / "vault"
	report = _report(path=p, is_git=True, last_active_days=200)
	cfg = TidyConfig(prune=True, prune_age_days=90,
	                 archive_dir=custom, init=False, commit_stale=False)
	actions = plan(report, config=cfg, now=datetime.now(tz=timezone.utc))
	assert actions[0].detail == str(custom / "cold")


def test_prune_dry_run_does_not_move(tmp_path):
	from project_commander.tidy import execute_archive
	p = tmp_path / "toarchive"
	p.mkdir()
	(p / "data.txt").write_text("hello")
	dest = tmp_path / "archive" / "toarchive"
	planned = PlannedAction(project=p, name="toarchive", action=Action.ARCHIVE,
	                        reason="test", detail=str(dest))
	result = execute_archive(planned, dry_run=True)
	assert result.ok
	assert p.exists()  # not moved
	assert not dest.exists()


def test_prune_executor_moves_project(tmp_path):
	from project_commander.tidy import execute_archive
	p = tmp_path / "toarchive"
	p.mkdir()
	(p / "data.txt").write_text("hello")
	dest = tmp_path / "archive" / "toarchive"
	planned = PlannedAction(project=p, name="toarchive", action=Action.ARCHIVE,
	                        reason="test", detail=str(dest))
	result = execute_archive(planned, dry_run=False)
	assert result.ok
	assert not p.exists()
	assert (dest / "data.txt").read_text() == "hello"


def test_prune_executor_refuses_existing_destination(tmp_path):
	from project_commander.tidy import execute_archive
	p = tmp_path / "toarchive"
	p.mkdir()
	dest = tmp_path / "archive" / "toarchive"
	dest.mkdir(parents=True)  # destination already exists
	planned = PlannedAction(project=p, name="toarchive", action=Action.ARCHIVE,
	                        reason="test", detail=str(dest))
	result = execute_archive(planned, dry_run=False)
	assert not result.ok
	assert "already exists" in result.error
	assert p.exists()  # original not moved