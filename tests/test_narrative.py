"""Tests for the narrative LLM layer.

No network calls. Each provider's transport is exercised by a FakeNarrator
or by patching `_post_json` to return a synthetic payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_commander import narrative
from project_commander.models import ProjectReport, Signal


def _now() -> datetime:
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str) -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str, source: str = "claude") -> Signal:
	return Signal(source=source, kind="prompt", timestamp=when, summary=summary)


def _make_report(tmp_path: Path, signals: list[Signal] | None = None) -> ProjectReport:
	signals = signals or []
	return ProjectReport(
		path=tmp_path,
		name=tmp_path.name,
		signals=signals,
		git_branch="main",
		git_dirty=False,
		is_git_repo=True,
	)


# ─── input collection ────────────────────────────────────────────────────────

def test_collect_inputs_reads_identity_and_plan_files(tmp_path: Path):
	(tmp_path / "README.md").write_text("# Hot Project\n\nDoes one thing well.\n", encoding="utf-8")
	(tmp_path / "AGENTS.md").write_text("Tabs only. No emojis.\n", encoding="utf-8")
	(tmp_path / "PLAN.md").write_text("- [ ] First\n- [x] Done\n", encoding="utf-8")
	now = _now()
	report = _make_report(tmp_path, [
		_commit(now - timedelta(hours=2), "feat: ship the thing"),
		_prompt(now - timedelta(hours=1), "Investigate the bug"),
	])

	inputs = narrative.collect_inputs(report, observations=None)

	# Identity excerpts come back in declared order, capped at 2 sources.
	assert len(inputs.identity_excerpts) == 2
	assert inputs.identity_excerpts[0][0] == "README.md"
	assert "Hot Project" in inputs.identity_excerpts[0][1]
	assert inputs.identity_excerpts[1][0] == "AGENTS.md"
	assert inputs.plan_excerpt is not None
	assert inputs.plan_excerpt[0] == "PLAN.md"
	assert "First" in inputs.plan_excerpt[1]
	assert inputs.recent_commits[0][1] == "feat: ship the thing"
	assert inputs.recent_prompts[0][2] == "Investigate the bug"


def test_collect_inputs_handles_missing_docs(tmp_path: Path):
	report = _make_report(tmp_path)
	inputs = narrative.collect_inputs(report, observations=None)
	assert inputs.identity_excerpts == ()
	assert inputs.plan_excerpt is None
	assert inputs.recent_commits == ()
	assert inputs.recent_prompts == ()


def test_collect_inputs_drops_procedural_prompts(tmp_path: Path):
	now = _now()
	report = _make_report(tmp_path, [
		_prompt(now - timedelta(hours=1), "yes"),
		_prompt(now - timedelta(hours=2), "Investigate the inventory race"),
	])
	inputs = narrative.collect_inputs(report, observations=None)
	assert len(inputs.recent_prompts) == 1
	assert inputs.recent_prompts[0][2] == "Investigate the inventory race"


def test_collect_inputs_caps_identity_excerpt_length(tmp_path: Path):
	(tmp_path / "README.md").write_text("x" * 10_000, encoding="utf-8")
	report = _make_report(tmp_path)
	inputs = narrative.collect_inputs(report, observations=None)
	# _IDENTITY_CHAR_CAP defines the cap.
	assert len(inputs.identity_excerpts[0][1]) <= narrative._IDENTITY_CHAR_CAP


# ─── prompt + parse ──────────────────────────────────────────────────────────

def test_build_user_message_includes_all_sections(tmp_path: Path):
	(tmp_path / "README.md").write_text("Project does X.\n", encoding="utf-8")
	now = _now()
	report = _make_report(tmp_path, [
		_commit(now - timedelta(hours=2), "feat: shipped X"),
		_prompt(now - timedelta(hours=1), "Plan the next milestone"),
	])
	report.git_dirty = True
	report.git_uncommitted = ["M src/foo.py"]
	report.git_ahead = 2
	msg = narrative.build_user_message(narrative.collect_inputs(report, None))
	assert "# Project:" in msg
	assert "branch: main" in msg
	assert "dirty (1 uncommitted)" in msg
	assert "2 commits ahead of upstream" in msg
	assert "## Identity excerpts" in msg
	assert "Project does X." in msg
	assert "## Recent commits" in msg
	assert "feat: shipped X" in msg
	assert "## Recent substantive agent prompts" in msg
	assert "Plan the next milestone" in msg


def test_parse_response_accepts_clean_json():
	body = (
		'{"what_it_is": "A small CLI tool.",'
		'"whats_been_happening": "Three commits added the verify subcommand.",'
		'"whats_planned": "Next: wire LLM narration into recap."}'
	)
	out = narrative.parse_response(body)
	assert out is not None
	assert out.what_it_is.startswith("A small CLI")
	assert "verify subcommand" in out.whats_been_happening


def test_parse_response_strips_code_fence():
	body = '```json\n{"what_it_is": "abcdefgh","whats_been_happening": "ijklmnop","whats_planned": "qrstuvwx"}\n```'
	out = narrative.parse_response(body)
	assert out is not None and out.what_it_is == "abcdefgh"


def test_parse_response_rejects_malformed_json():
	assert narrative.parse_response("not json at all") is None
	assert narrative.parse_response('{"what_it_is": "ok"}') is None  # missing keys -> trivial output


def test_parse_response_rejects_trivially_short_fields():
	body = '{"what_it_is": "x","whats_been_happening": "y","whats_planned": "z"}'
	assert narrative.parse_response(body) is None


# ─── cache ───────────────────────────────────────────────────────────────────

def test_cache_key_changes_with_prompt_or_model():
	a = narrative.cache_key("prompt body", "model-1")
	b = narrative.cache_key("prompt body", "model-2")
	c = narrative.cache_key("prompt body different", "model-1")
	assert len({a, b, c}) == 3


@dataclass
class _CountingNarrator:
	output: narrative.NarrativeOutput | None
	calls: int = 0

	def narrate(self, inputs: narrative.NarrativeInputs) -> narrative.NarrativeOutput | None:
		self.calls += 1
		return self.output


def _sample_inputs(tmp_path: Path) -> narrative.NarrativeInputs:
	tmp_path.mkdir(parents=True, exist_ok=True)
	(tmp_path / "README.md").write_text("Project description.\n", encoding="utf-8")
	now = _now()
	report = _make_report(tmp_path, [_commit(now, "feat: thing")])
	return narrative.collect_inputs(report, None)


def test_cached_narrator_reuses_disk_cache(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	good = narrative.NarrativeOutput(
		what_it_is="A small CLI tool that ships work.",
		whats_been_happening="Recent commits added the verify subcommand.",
		whats_planned="Next: wire narration into recap.",
	)
	inner = _CountingNarrator(output=good)
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	inputs = _sample_inputs(tmp_path / "proj")
	out1 = cached.narrate(inputs)
	out2 = cached.narrate(inputs)
	assert out1 == out2 == good
	assert inner.calls == 1   # second call served from disk


def test_cached_narrator_does_not_cache_failure(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	inner = _CountingNarrator(output=None)
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	inputs = _sample_inputs(tmp_path / "proj")
	cached.narrate(inputs)
	cached.narrate(inputs)
	assert inner.calls == 2   # both calls hit the inner provider


def test_cached_narrator_invalidates_on_signal_change(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	good = narrative.NarrativeOutput(
		what_it_is="A small CLI tool that ships work.",
		whats_been_happening="Recent commits added the verify subcommand.",
		whats_planned="Next: wire narration into recap.",
	)
	inner = _CountingNarrator(output=good)
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	proj = tmp_path / "proj"
	proj.mkdir()
	(proj / "README.md").write_text("v1\n", encoding="utf-8")
	now = _now()
	r1 = _make_report(proj, [_commit(now, "feat: one")])
	cached.narrate(narrative.collect_inputs(r1, None))
	# add another commit -> different prompt -> different cache key
	r2 = _make_report(proj, [_commit(now, "feat: one"), _commit(now + timedelta(hours=1), "feat: two")])
	cached.narrate(narrative.collect_inputs(r2, None))
	assert inner.calls == 2


# ─── factory + fail-soft ─────────────────────────────────────────────────────

def test_make_narrator_returns_disabled_when_nothing_configured(monkeypatch):
	# Force the ollama probe to report unreachable so auto fallback hits "none".
	monkeypatch.setattr(narrative, "_ollama_reachable", lambda host: False)
	n = narrative.make_narrator(provider="auto", env={})
	# Disabled is wrapped only when cache=True AND inner is a real provider; here it is bare.
	assert isinstance(n, narrative.DisabledNarrator)
	assert n.narrate(_dummy_inputs()) is None


def test_make_narrator_explicit_none(monkeypatch):
	n = narrative.make_narrator(provider="none", env={"ANTHROPIC_API_KEY": "k"})
	assert isinstance(n, narrative.DisabledNarrator)


def test_make_narrator_anthropic(monkeypatch):
	n = narrative.make_narrator(provider="anthropic", env={"ANTHROPIC_API_KEY": "k"})
	# Cache wrapper around the real provider.
	assert isinstance(n, narrative.CachedNarrator)
	assert isinstance(n.inner, narrative.AnthropicNarrator)
	assert n.inner.model == "claude-3-5-haiku-latest"


def test_make_narrator_respects_model_override():
	n = narrative.make_narrator(
		provider="openai",
		model="gpt-4o",
		env={"OPENAI_API_KEY": "k"},
	)
	assert isinstance(n, narrative.CachedNarrator)
	assert isinstance(n.inner, narrative.OpenAINarrator)
	assert n.inner.model == "gpt-4o"


def test_anthropic_narrator_failsoft_on_http_error(monkeypatch):
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: None)
	n = narrative.AnthropicNarrator(api_key="k")
	assert n.narrate(_dummy_inputs()) is None


def test_openai_narrator_handles_unexpected_shape(monkeypatch):
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: {"unexpected": True})
	n = narrative.OpenAINarrator(api_key="k")
	assert n.narrate(_dummy_inputs()) is None


def test_anthropic_narrator_parses_normal_response(monkeypatch):
	good = (
		'{"what_it_is":"A small CLI tool.",'
		'"whats_been_happening":"Three commits landed the verify subcommand.",'
		'"whats_planned":"Next: wire narration into recap."}'
	)
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: {"content": [{"type": "text", "text": good}]})
	n = narrative.AnthropicNarrator(api_key="k")
	out = n.narrate(_dummy_inputs())
	assert out is not None
	assert out.what_it_is.startswith("A small CLI")


def test_openai_narrator_parses_normal_response(monkeypatch):
	good = (
		'{"what_it_is":"A small CLI tool.",'
		'"whats_been_happening":"Three commits landed the verify subcommand.",'
		'"whats_planned":"Next: wire narration into recap."}'
	)
	payload = {"choices": [{"message": {"content": good}}]}
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: payload)
	n = narrative.OpenAINarrator(api_key="k")
	out = n.narrate(_dummy_inputs())
	assert out is not None
	assert "verify subcommand" in out.whats_been_happening


def test_ollama_narrator_parses_normal_response(monkeypatch):
	good = (
		'{"what_it_is":"A small CLI tool.",'
		'"whats_been_happening":"Three commits landed the verify subcommand.",'
		'"whats_planned":"Next: wire narration into recap."}'
	)
	payload = {"message": {"content": good}}
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: payload)
	n = narrative.OllamaNarrator()
	out = n.narrate(_dummy_inputs())
	assert out is not None
	assert "verify subcommand" in out.whats_been_happening


def _dummy_inputs() -> narrative.NarrativeInputs:
	return narrative.NarrativeInputs(
		project_name="p",
		branch="main",
		dirty=False,
		uncommitted_count=0,
		ahead=0,
		behind=0,
		identity_excerpts=(("README.md", "x"),),
		plan_excerpt=None,
		recent_commits=((_now(), "feat: ok"),),
		recent_prompts=(),
		last_active=_now(),
	)



# ─── weekly narrator ─────────────────────────────────────────────────────────

def _dummy_weekly() -> narrative.WeeklyInputs:
	return narrative.WeeklyInputs(
		since_date="2026-04-19",
		now_date="2026-04-26",
		since_days=7,
		active_projects=4,
		total_commits=12,
		total_prompts=6,
		attention=(("alpha", "Commit 3 uncommitted file(s)", "Working tree dirty"),),
		new_this_week=(("beta", "", "Initial scaffold landed"),),
		moved_this_week=(
			("gamma", "", "Recent commits focused on auth refactor"),
			("delta", "", "Quiet activity"),
		),
	)


def test_build_weekly_message_includes_buckets_and_totals():
	msg = narrative.build_weekly_message(_dummy_weekly())
	assert "# Weekly review" in msg
	assert "Window: 2026-04-19 to 2026-04-26" in msg
	assert "4 active project(s)" in msg
	assert "12 commit(s)" in msg
	assert "## Needs your attention" in msg
	assert "## New this week" in msg
	assert "## Moved this week" in msg
	assert "alpha" in msg and "beta" in msg and "gamma" in msg


def test_build_weekly_message_marks_empty_buckets():
	inputs = narrative.WeeklyInputs(
		since_date="2026-04-19", now_date="2026-04-26", since_days=7,
		active_projects=0, total_commits=0, total_prompts=0,
	)
	msg = narrative.build_weekly_message(inputs)
	assert msg.count("(empty)") == 3


def test_parse_weekly_response_accepts_valid_json():
	body = '{"week_in_review": "This week you shipped two features and stalled on auth."}'
	out = narrative.parse_weekly_response(body)
	assert out is not None
	assert out.week_in_review.startswith("This week")


def test_parse_weekly_response_rejects_too_short():
	assert narrative.parse_weekly_response('{"week_in_review": "hi"}') is None


def test_parse_weekly_response_rejects_malformed():
	assert narrative.parse_weekly_response("not json") is None
	assert narrative.parse_weekly_response('{"other": "stuff"}') is None


def test_parse_weekly_response_strips_code_fence():
	body = '```json\n{"week_in_review": "A week of incremental progress on auth."}\n```'
	out = narrative.parse_weekly_response(body)
	assert out is not None
	assert "incremental" in out.week_in_review


def test_anthropic_narrator_weekly_round_trip(monkeypatch):
	good = '{"week_in_review": "You shipped on alpha; gamma kept moving on auth."}'
	monkeypatch.setattr(narrative, "_post_json",
	                    lambda *a, **k: {"content": [{"type": "text", "text": good}]})
	n = narrative.AnthropicNarrator(api_key="k")
	out = n.narrate_weekly(_dummy_weekly())
	assert out is not None
	assert "alpha" in out.week_in_review


def test_openai_narrator_weekly_round_trip(monkeypatch):
	good = '{"week_in_review": "You shipped on alpha; gamma kept moving on auth."}'
	monkeypatch.setattr(narrative, "_post_json",
	                    lambda *a, **k: {"choices": [{"message": {"content": good}}]})
	n = narrative.OpenAINarrator(api_key="k")
	out = n.narrate_weekly(_dummy_weekly())
	assert out is not None
	assert "gamma" in out.week_in_review


def test_ollama_narrator_weekly_round_trip(monkeypatch):
	good = '{"week_in_review": "You shipped on alpha; gamma kept moving on auth."}'
	monkeypatch.setattr(narrative, "_post_json",
	                    lambda *a, **k: {"message": {"content": good}})
	n = narrative.OllamaNarrator()
	out = n.narrate_weekly(_dummy_weekly())
	assert out is not None
	assert "shipped" in out.week_in_review


def test_disabled_narrator_weekly_returns_none():
	assert narrative.DisabledNarrator().narrate_weekly(_dummy_weekly()) is None


def test_anthropic_narrator_weekly_failsoft_on_http_error(monkeypatch):
	monkeypatch.setattr(narrative, "_post_json", lambda *a, **k: None)
	n = narrative.AnthropicNarrator(api_key="k")
	assert n.narrate_weekly(_dummy_weekly()) is None


def test_cached_narrator_weekly_reuses_disk_cache(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	good = narrative.WeeklyOutput(week_in_review="This week saw shipped progress on auth and tests.")

	class _W:
		def __init__(self): self.calls = 0
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs):
			self.calls += 1
			return good

	inner = _W()
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	inputs = _dummy_weekly()
	out1 = cached.narrate_weekly(inputs)
	out2 = cached.narrate_weekly(inputs)
	assert out1 == out2 == good
	assert inner.calls == 1   # cache hit on the second call


def test_cached_narrator_weekly_invalidates_when_inputs_change(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	good = narrative.WeeklyOutput(week_in_review="This week saw shipped progress on auth and tests.")

	class _W:
		def __init__(self): self.calls = 0
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs):
			self.calls += 1
			return good

	inner = _W()
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	cached.narrate_weekly(_dummy_weekly())
	# Different totals -> different prompt -> different cache key
	different = narrative.WeeklyInputs(
		since_date="2026-04-19", now_date="2026-04-26", since_days=7,
		active_projects=99, total_commits=999, total_prompts=99,
	)
	cached.narrate_weekly(different)
	assert inner.calls == 2


def test_cached_narrator_weekly_does_not_cache_failure(tmp_path: Path):
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()

	class _Null:
		def __init__(self): self.calls = 0
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs):
			self.calls += 1
			return None

	inner = _Null()
	cached = narrative.CachedNarrator(inner=inner, model="test-model", root=cache_dir)
	cached.narrate_weekly(_dummy_weekly())
	cached.narrate_weekly(_dummy_weekly())
	assert inner.calls == 2  # both calls hit the inner provider
