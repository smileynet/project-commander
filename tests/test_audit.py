"""Tests for the audit module: prompt\u2192commit causality, ratios, flags."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_commander.audit import audit, render_markdown
from project_commander.models import ProjectReport, Signal


def _now():
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str, source: str = "claude") -> Signal:
	return Signal(source=source, kind="prompt", timestamp=when, summary=summary)


# ───── execution detection ──────────────────────────────────────────────────

def test_audit_marks_prompt_executed_when_commit_within_24h():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(hours=20), "build the thing"),
			_commit(now - timedelta(hours=10), "built the thing"),
		],
	)
	a = audit(r, since_days=7, now=now)
	assert len(a.outcomes) == 1
	assert a.outcomes[0].executed
	assert a.outcomes[0].followup_commits == 1


def test_audit_marks_prompt_orphan_when_no_commit_within_24h():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(hours=48), "do the thing"),
			_commit(now - timedelta(days=4)),  # before the prompt → doesn't count
		],
	)
	a = audit(r, since_days=7, now=now)
	assert len(a.outcomes) == 1
	assert not a.outcomes[0].executed
	assert a.orphan_count == 1


def test_audit_only_counts_commits_inside_24h_window():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(days=3), "first prompt"),
			_commit(now - timedelta(days=3) + timedelta(hours=23), "in window"),
			_commit(now - timedelta(days=3) + timedelta(hours=25), "outside 24h window"),
		],
	)
	a = audit(r, since_days=7, now=now)
	# 25h after the prompt is outside the 24h follow-up window
	assert a.outcomes[0].followup_commits == 1
	assert a.outcomes[0].executed


def test_audit_excludes_procedural_prompts_from_outcomes():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(hours=20), "yes"),
			_prompt(now - timedelta(hours=22), "implement feature foo"),
		],
	)
	a = audit(r, since_days=7, now=now)
	# Procedural goes into prompts_procedural, not outcomes
	assert a.prompts_procedural == 1
	assert a.prompts_substantive == 1
	assert len(a.outcomes) == 1
	assert "feature foo" in a.outcomes[0].prompt.summary


# ───── ratio + flags ────────────────────────────────────────────────────────

def test_audit_ratio_is_prompts_per_commit():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(hours=20), "do the thing"),
			_prompt(now - timedelta(hours=21), "do the other thing"),
			_prompt(now - timedelta(hours=22), "do the third thing"),
			_commit(now - timedelta(hours=15), "did them"),
		],
	)
	a = audit(r, since_days=7, now=now)
	assert a.prompt_to_commit_ratio == 3.0


def test_audit_ratio_is_none_when_no_commits():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[_prompt(now - timedelta(hours=20), "do the thing")],
	)
	a = audit(r, since_days=7, now=now)
	assert a.prompt_to_commit_ratio is None


def test_audit_flags_high_volume_when_ratio_above_5():
	now = _now()
	prompts = [_prompt(now - timedelta(hours=h), f"prompt {h}") for h in range(1, 7)]
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=prompts + [_commit(now - timedelta(minutes=30))],
	)
	a = audit(r, since_days=7, now=now)
	assert "high-prompt-volume" in a.flags


def test_audit_flags_all_orphans_when_no_prompt_landed():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(days=3), "first orphan"),
			_prompt(now - timedelta(days=2), "second orphan"),
		],
	)
	a = audit(r, since_days=7, now=now)
	assert "all-orphans" in a.flags


def test_audit_excludes_signals_outside_window():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(days=30), "old prompt"),
			_commit(now - timedelta(days=30), "old commit"),
			_prompt(now - timedelta(hours=2), "fresh prompt"),
		],
	)
	a = audit(r, since_days=7, now=now)
	assert a.prompts_total == 1
	assert a.commits == 0


# ───── markdown rendering ───────────────────────────────────────────────────

def test_render_markdown_shows_outcomes_and_ratio():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/proj"), name="proj", is_git_repo=True,
		signals=[
			_prompt(now - timedelta(hours=20), "build feature A", source="opencode"),
			_commit(now - timedelta(hours=10), "feat: A"),
			_prompt(now - timedelta(hours=5), "build feature B"),
		],
	)
	a = audit(r, since_days=7, now=now)
	out = render_markdown(a)
	assert "# proj — audit" in out
	assert "Prompt → commit ratio:" in out
	assert "## Substantive prompts" in out
	assert "build feature A" in out
	# A landed → executed; B didn't → orphan
	assert "executed" in out
	assert "**orphan**" in out
