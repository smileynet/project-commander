"""Tests for the recap module: categorization + narrative synthesis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_commander.models import ProjectReport, Signal
from project_commander.observations import build
from project_commander.recap import (
	build_entries,
	categorize,
	render_markdown,
	synthesize,
)


def _now():
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str) -> Signal:
	return Signal(source="claude", kind="prompt", timestamp=when, summary=summary)


def _doc(when: datetime, ref: str, body: str) -> Signal:
	return Signal(source="docs", kind="doc", timestamp=when,
	              summary=f"[{ref}] {body}", ref=ref)


def _make(report: ProjectReport, now: datetime) -> ProjectReport:
	report.observations = build(report, now=now)
	return report


# ───── categorization ───────────────────────────────────────────────────────

def test_categorize_returns_none_when_no_activity():
	now = _now()
	since = now - timedelta(days=90)
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True)
	assert categorize(r, since=since, now=now) is None


def test_categorize_marks_shipped_when_progress_state_is_shipped():
	now = _now()
	since = now - timedelta(days=90)
	# Shipped progress requires: w30.commits>=3, w30.prompts==0, no purpose,
	# clean tree, age <= 30d.
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_dirty=False,
		signals=[
			_commit(now - timedelta(days=10), "feat: thing"),
			_commit(now - timedelta(days=12), "feat: another"),
			_commit(now - timedelta(days=14), "feat: third"),
		],
	)
	r = _make(r, now)
	assert r.observations.progress.value == "shipped"
	assert categorize(r, since=since, now=now) == "shipped"


def test_categorize_marks_major_arc_at_threshold():
	now = _now()
	since = now - timedelta(days=90)
	signals = [_commit(now - timedelta(days=i), f"commit {i}") for i in range(1, 11)]
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=signals,
	)
	r = _make(r, now)
	assert categorize(r, since=since, now=now) == "major"


def test_categorize_marks_paused_when_first_commit_in_window_then_quiet():
	now = _now()
	since = now - timedelta(days=90)
	# All commits are in the FIRST third of the window, then nothing after.
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=85), "init"),
			_commit(now - timedelta(days=82), "next"),
			_commit(now - timedelta(days=80), "third"),
		],
	)
	r = _make(r, now)
	assert categorize(r, since=since, now=now) == "paused"


def test_categorize_returns_quiet_for_modest_activity():
	now = _now()
	since = now - timedelta(days=90)
	# Pre-existing project (first commit BEFORE window) with some recent
	# commits but below the major-arc threshold.
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=200), "old commit"),  # before window
			_commit(now - timedelta(days=20), "recent 1"),
			_commit(now - timedelta(days=10), "recent 2"),
		],
	)
	r = _make(r, now)
	assert categorize(r, since=since, now=now) == "quiet"


# ───── narrative synthesis ──────────────────────────────────────────────────

def test_synthesize_includes_purpose_sentence_when_available():
	now = _now()
	since = now - timedelta(days=90)
	r = ProjectReport(
		path=Path("/tmp/x"), name="experiment", is_git_repo=True, git_branch="main",
		signals=[
			_doc(now - timedelta(days=10), "README.md",
			     "A reproducible benchmarking harness for LLM inference."),
			_commit(now - timedelta(days=5), "feat: harness scaffold"),
			_commit(now - timedelta(days=4), "feat: profiling pipeline"),
		],
	)
	r = _make(r, now)
	text = synthesize(r, since=since, now=now, category="quiet")
	assert "benchmarking harness" in text


def test_synthesize_lists_commit_topics_for_major_arc():
	now = _now()
	since = now - timedelta(days=90)
	signals = [
		_commit(now - timedelta(days=i), f"feat: thing {chr(65 + i)}")
		for i in range(10)
	]
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=signals,
	)
	r = _make(r, now)
	text = synthesize(r, since=since, now=now, category="major")
	assert "Major arc" in text
	assert "10 commits" in text


def test_synthesize_paused_mentions_idle_days():
	now = _now()
	since = now - timedelta(days=90)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=85), "init"),
			_commit(now - timedelta(days=80), "next"),
		],
	)
	r = _make(r, now)
	text = synthesize(r, since=since, now=now, category="paused")
	assert "stalled" in text.lower()
	assert "80" in text  # 80 days idle since last commit


# ───── full pipeline ────────────────────────────────────────────────────────

def test_build_entries_skips_inactive_projects():
	now = _now()
	since = now - timedelta(days=90)
	dormant = ProjectReport(path=Path("/tmp/d"), name="dormant", is_git_repo=True)
	active = ProjectReport(
		path=Path("/tmp/a"), name="active", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(days=10), "feat: thing")],
	)
	dormant = _make(dormant, now)
	active = _make(active, now)
	entries = build_entries([dormant, active], since=since, now=now)
	assert [e.project for e in entries] == ["active"]


def test_build_entries_sorts_by_commit_volume():
	now = _now()
	since = now - timedelta(days=90)
	low = ProjectReport(
		path=Path("/tmp/low"), name="low", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(days=5), "one")],
	)
	high = ProjectReport(
		path=Path("/tmp/high"), name="high", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(days=i), f"feat {i}") for i in range(1, 12)],
	)
	low = _make(low, now)
	high = _make(high, now)
	entries = build_entries([low, high], since=since, now=now)
	assert entries[0].project == "high"
	assert entries[1].project == "low"


def test_render_markdown_organizes_by_section():
	now = _now()
	since = now - timedelta(days=90)
	major = ProjectReport(
		path=Path("/tmp/major"), name="bigthing", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(days=i), f"feat {i}") for i in range(1, 12)],
	)
	quiet = ProjectReport(
		path=Path("/tmp/quiet"), name="littlething", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=200), "old"),  # makes window-start NOT first commit
			_commit(now - timedelta(days=20), "small fix"),
		],
	)
	major = _make(major, now)
	quiet = _make(quiet, now)
	entries = build_entries([major, quiet], since=since, now=now)
	out = render_markdown(entries, label="Test recap", since=since, now=now)
	assert "# Test recap" in out
	assert "## Major arcs (1)" in out
	assert "## Quiet activity (1)" in out
	# Each project gets its own H3
	assert "### bigthing" in out
	assert "### littlething" in out
