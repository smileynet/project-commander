"""Tests for the catchup module: cursor persistence, since-parsing, classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from project_commander.catchup import (
	build_sections,
	classify,
	clear_cursor,
	cursor_path,
	parse_since,
	read_cursor,
	state_dir,
	write_cursor,
)
from project_commander.models import ProjectReport, Signal


# ───── since-parsing ─────────────────────────────────────────────────────────

def test_parse_since_supports_common_units():
	assert parse_since("6h") == timedelta(hours=6)
	assert parse_since("2d") == timedelta(days=2)
	assert parse_since("30m") == timedelta(minutes=30)
	assert parse_since("1w") == timedelta(weeks=1)
	assert parse_since(" 3 H ") == timedelta(hours=3)


def test_parse_since_rejects_garbage():
	with pytest.raises(ValueError):
		parse_since("yesterday")
	with pytest.raises(ValueError):
		parse_since("6")
	with pytest.raises(ValueError):
		parse_since("h6")


# ───── cursor persistence ────────────────────────────────────────────────────

def test_state_dir_honors_xdg_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
	assert state_dir(tmp_path / "fake-home") == tmp_path / "xdg"


def test_state_dir_falls_back_to_local_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	assert state_dir(tmp_path) == tmp_path / ".local" / "state"


def test_cursor_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	ts = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
	write_cursor(tmp_path, ts)
	assert cursor_path(tmp_path).exists()
	got = read_cursor(tmp_path)
	assert got == ts


def test_cursor_normalizes_naive_to_utc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	# Manually write a naive timestamp; read_cursor must normalize to UTC.
	cursor_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
	cursor_path(tmp_path).write_text("2026-04-26T12:00:00\n", encoding="utf-8")
	got = read_cursor(tmp_path)
	assert got is not None
	assert got.tzinfo is timezone.utc


def test_cursor_returns_none_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	assert read_cursor(tmp_path) is None


def test_cursor_returns_none_when_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	cursor_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
	cursor_path(tmp_path).write_text("not a date\n", encoding="utf-8")
	assert read_cursor(tmp_path) is None


def test_clear_cursor_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv("XDG_STATE_HOME", raising=False)
	clear_cursor(tmp_path)  # no file yet — must not raise
	write_cursor(tmp_path, datetime(2026, 4, 26, tzinfo=timezone.utc))
	clear_cursor(tmp_path)
	assert not cursor_path(tmp_path).exists()


# ───── classification ────────────────────────────────────────────────────────

def _now():
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str, source: str = "claude") -> Signal:
	return Signal(source=source, kind="prompt", timestamp=when, summary=summary)


def test_classify_emits_agent_row_for_prompts_since():
	now = _now()
	since = now - timedelta(hours=8)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_prompt(now - timedelta(hours=2), "review state and proceed")],
	)
	rows = classify(r, since=since, now=now)
	assert len(rows) == 1
	assert rows[0].bucket == "agent"
	assert rows[0].prompts == 1
	assert "review state" in rows[0].headline


def test_classify_skips_prompts_before_cursor():
	now = _now()
	since = now - timedelta(hours=8)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_prompt(now - timedelta(hours=24), "old prompt")],
	)
	rows = classify(r, since=since, now=now)
	assert rows == []


def test_classify_emits_upstream_row_when_behind_with_recent_commits():
	now = _now()
	since = now - timedelta(hours=24)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_behind=21, git_upstream="origin/main",
		signals=[_commit(now - timedelta(hours=2), "upstream change")],
	)
	rows = classify(r, since=since, now=now)
	assert len(rows) == 1
	assert rows[0].bucket == "upstream"
	assert "21" in rows[0].headline
	assert "origin/main" in rows[0].headline


def test_classify_skips_upstream_when_not_behind():
	now = _now()
	since = now - timedelta(hours=24)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_behind=0,
		signals=[_commit(now - timedelta(hours=2), "your commit")],
	)
	rows = classify(r, since=since, now=now)
	assert len(rows) == 1
	assert rows[0].bucket == "own"


def test_classify_does_not_double_count_own_when_behind():
	"""A project that is behind should not also appear under 'own' \u2014 the
	upstream framing already explains the commit volume."""
	now = _now()
	since = now - timedelta(hours=24)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_behind=5, git_upstream="origin/main",
		signals=[_commit(now - timedelta(hours=2), "remote commit")],
	)
	rows = classify(r, since=since, now=now)
	buckets = {row.bucket for row in rows}
	assert "upstream" in buckets
	assert "own" not in buckets


def test_classify_emits_both_agent_and_own_when_user_drove_the_session():
	now = _now()
	since = now - timedelta(hours=24)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_behind=0,
		signals=[
			_prompt(now - timedelta(hours=4), "implement feature foo"),
			_commit(now - timedelta(hours=2), "feat: foo"),
		],
	)
	rows = classify(r, since=since, now=now)
	buckets = {row.bucket for row in rows}
	assert buckets == {"agent", "own"}


def test_build_sections_orders_by_recency():
	now = _now()
	since = now - timedelta(days=7)
	old = ProjectReport(
		path=Path("/tmp/old"), name="old", is_git_repo=True,
		signals=[_prompt(now - timedelta(days=3), "old")],
	)
	fresh = ProjectReport(
		path=Path("/tmp/fresh"), name="fresh", is_git_repo=True,
		signals=[_prompt(now - timedelta(hours=1), "fresh")],
	)
	buckets = build_sections([old, fresh], since=since, now=now)
	assert [r.name for r in buckets["agent"]] == ["fresh", "old"]


def test_classify_treats_procedural_only_prompts_as_low_signal():
	now = _now()
	since = now - timedelta(hours=24)
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_prompt(now - timedelta(hours=2), "yes")],
	)
	rows = classify(r, since=since, now=now)
	# Procedural prompts still produce an agent row (we want to know an
	# approval session happened) but the row's headline must say so.
	assert len(rows) == 1
	assert rows[0].bucket == "agent"
	assert rows[0].prompts == 0  # subst_prompts only
	assert rows[0].procedural == 1
	assert "approval" in rows[0].headline
