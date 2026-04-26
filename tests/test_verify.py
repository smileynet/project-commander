"""Tests for verify: each check's PASS/FAIL/SKIP semantics, JSON shape, exit code."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_commander.models import PlanDocSummary, ProjectReport, Signal
from project_commander.observations import build
from project_commander.verify import (
	ProjectVerdict,
	render_json,
	verify_all,
	verify_one,
)


def _now():
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str) -> Signal:
	return Signal(source="claude", kind="prompt", timestamp=when, summary=summary)


def _build(report: ProjectReport, *, now=None) -> ProjectVerdict:
	report.observations = build(report, now=now or _now())
	return verify_one(report)


# ───── working_tree_clean ────────────────────────────────────────────────────

def test_clean_tree_passes_working_tree_check():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=False)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "working_tree_clean")
	assert check.status == "PASS"


def test_dirty_tree_fails_working_tree_check():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py", " M b.py"])
	v = _build(r)
	check = next(c for c in v.checks if c.name == "working_tree_clean")
	assert check.status == "FAIL"
	assert "2 uncommitted" in check.detail


def test_no_git_skips_working_tree_check():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=False)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "working_tree_clean")
	assert check.status == "SKIP"


# ───── branch_in_sync ────────────────────────────────────────────────────────

def test_branch_at_parity_passes():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_upstream="origin/main",
	                  git_ahead=0, git_behind=0)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "branch_in_sync")
	assert check.status == "PASS"


def test_ahead_branch_fails_sync():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_upstream="origin/main",
	                  git_ahead=3, git_behind=0)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "branch_in_sync")
	assert check.status == "FAIL"
	assert "3 ahead" in check.detail


def test_no_upstream_skips_sync():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_upstream=None)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "branch_in_sync")
	assert check.status == "SKIP"


# ───── no_orphan_thread ──────────────────────────────────────────────────────

def test_orphan_thread_fails_check():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=2)),
			_prompt(now - timedelta(hours=12), "audit the system"),
		],
	)
	v = _build(r, now=now)
	check = next(c for c in v.checks if c.name == "no_orphan_thread")
	assert check.status == "FAIL"
	assert "12h" in check.detail


def test_no_orphan_thread_when_followed_by_commit():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_prompt(now - timedelta(hours=12), "do the thing"),
			_commit(now - timedelta(hours=6)),
		],
	)
	v = _build(r, now=now)
	check = next(c for c in v.checks if c.name == "no_orphan_thread")
	assert check.status == "PASS"


# ───── no_plan_drift ─────────────────────────────────────────────────────────

def test_plan_drift_fails_check():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			Signal(source="docs", kind="doc", timestamp=now - timedelta(days=1),
			       summary="[PLAN.md] Status: completed", ref="PLAN.md"),
			_commit(now - timedelta(hours=2), "still landing"),
		],
	)
	v = _build(r, now=now)
	check = next(c for c in v.checks if c.name == "no_plan_drift")
	assert check.status == "FAIL"


def test_no_plan_drift_when_plan_open():
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		plan_summaries={"PLAN.md": PlanDocSummary(
			path="PLAN.md", total_items=3, open_items=1, next_item="next thing")},
	)
	v = _build(r)
	check = next(c for c in v.checks if c.name == "no_plan_drift")
	assert check.status == "PASS"


# ───── prompts_substantive ───────────────────────────────────────────────────

def test_procedural_only_prompts_fails_check():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_prompt(now - timedelta(hours=2), "yes"),
			_prompt(now - timedelta(hours=3), "proceed"),
		],
	)
	v = _build(r, now=now)
	check = next(c for c in v.checks if c.name == "prompts_substantive")
	assert check.status == "FAIL"


def test_substantive_prompts_pass_check():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_prompt(now - timedelta(hours=2), "implement the new feature")],
	)
	v = _build(r, now=now)
	check = next(c for c in v.checks if c.name == "prompts_substantive")
	assert check.status == "PASS"


# ───── verdict aggregation ──────────────────────────────────────────────────

def test_verdict_is_pass_when_all_checks_pass():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=False)
	v = _build(r)
	assert v.verdict == "PASS"


def test_verdict_is_fail_when_any_check_fails():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py"])
	v = _build(r)
	assert v.verdict == "FAIL"
	assert v.next_action != ""


def test_skip_alone_does_not_fail_verdict():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=False)
	v = _build(r)
	# Every git-dependent check skips, but no FAIL → overall PASS
	assert v.verdict == "PASS"


# ───── JSON output ───────────────────────────────────────────────────────────

def test_render_json_single_verdict_emits_object():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py"])
	r.observations = build(r, now=_now())
	v = verify_one(r)
	rendered = render_json(v)
	parsed = json.loads(rendered)
	assert parsed["project"] == "x"
	assert parsed["verdict"] == "FAIL"
	assert any(c["name"] == "working_tree_clean" and c["status"] == "FAIL"
	           for c in parsed["checks"])
	assert "next_action" in parsed


def test_render_json_multiple_verdicts_emits_array():
	r1 = ProjectReport(path=Path("/tmp/a"), name="a", is_git_repo=True,
	                   git_branch="main", git_dirty=False)
	r2 = ProjectReport(path=Path("/tmp/b"), name="b", is_git_repo=True,
	                   git_branch="main", git_dirty=True, git_uncommitted=[" M x"])
	r1.observations = build(r1, now=_now())
	r2.observations = build(r2, now=_now())
	verdicts = verify_all([r1, r2])
	rendered = render_json(verdicts)
	parsed = json.loads(rendered)
	assert isinstance(parsed, list)
	assert len(parsed) == 2
	assert {p["project"] for p in parsed} == {"a", "b"}
