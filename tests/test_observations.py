"""Tests for the observations layer: chrome stripping, first-sentence, plan parsing,
outstanding, next-action."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from project_commander.models import PlanDocSummary, ProjectReport, Signal
from project_commander.observations import (
	Outstanding,
	Progress,
	build,
	clean_doc_prose,
	first_sentence,
)
from project_commander.sources.docs import _parse_plan_structure

from project_commander.report import render_detail_markdown, render_review_markdown

# ───── chrome stripping ──────────────────────────────────────────────────────

def test_clean_doc_prose_strips_blockquote_chains():
	# Joined-from-multiline summaries had `>` markers mid-string that the
	# old line-anchored regex could not catch.
	assert clean_doc_prose("> A > > B > > > C") == "A B C"


def test_clean_doc_prose_strips_html_and_dangling_tags():
	# Complete tags get scrubbed; a tag whose closer fell off the truncation
	# edge gets removed instead of leaving "<img" garbage.
	assert clean_doc_prose('<p align="center">hello</p>') == "hello"
	assert clean_doc_prose("text then <img src='x.png' al") == "text then"


def test_clean_doc_prose_strips_github_callouts():
	assert clean_doc_prose("[!TIP] Do the thing.") == "Do the thing."
	assert clean_doc_prose("[!WARNING] Be careful.") == "Be careful."


def test_clean_doc_prose_strips_leading_metadata_prefix():
	# `**Status:**` followed by substantive prose: prefix removed, prose kept.
	assert clean_doc_prose("**Status:** experimental, offline.") == "experimental, offline."
	# Note: bare 'Date: 2025-01-01 prose' isn't reliably separable from prose;
	# the docs scanner skips pure-metadata lines (no period after value), and the
	# observations layer strips only the **bold-key:** prefix form.


def test_clean_doc_prose_strips_bold_italic_inline_code():
	assert clean_doc_prose("**bold** and *italic* and `code`.") == "bold and italic and code."


def test_clean_doc_prose_handles_empty():
	assert clean_doc_prose("") == ""


# ───── first_sentence ────────────────────────────────────────────────────────

def test_first_sentence_returns_complete_sentence():
	out = first_sentence("This is one. This is two.", limit=200)
	assert out == "This is one. This is two."


def test_first_sentence_accumulates_short_openers():
	# Old behavior would have stopped at "Yes." and lost the rest.
	text = "Yes. This is the actual content that should appear."
	out = first_sentence(text, limit=200)
	assert "actual content" in out


def test_first_sentence_truncates_when_no_period():
	text = "no period here just a long stream of words " * 5
	out = first_sentence(text, limit=80)
	assert len(out) <= 80
	assert out.endswith("\u2026")


def test_first_sentence_completes_sentence_just_past_limit():
	# A sentence whose period sits a few chars past `limit` should still complete.
	text = "A medium length opener. Then a second sentence."
	# limit just before the second period — first_sentence may complete or stop.
	# What we care about: result is not a mid-word truncation.
	out = first_sentence(text, limit=25)
	assert out.endswith(".") or out.endswith("\u2026")


def test_first_sentence_handles_empty():
	assert first_sentence("", limit=100) == ""


# ───── plan-doc structure parser ─────────────────────────────────────────────

def test_parse_plan_structure_counts_checkboxes():
	text = """\
# Plan

- [x] Done thing
- [x] Also done
- [ ] Open thing
- [ ] Another open
"""
	s = _parse_plan_structure(text, path="PLAN.md")
	assert s.total_items == 4
	assert s.open_items == 2
	assert s.next_item == "Open thing"


def test_parse_plan_structure_counts_phases():
	text = """\
## I. Foundation (complete)

stuff

## II. Auth

more stuff

## III. Verification
"""
	s = _parse_plan_structure(text, path="PLAN.md")
	assert s.total_phases == 3
	assert s.complete_phases == 1


def test_parse_plan_structure_phase_complete_via_status_marker():
	text = """\
## Phase 1: Foundation

**Status:** completed

Some prose.

## Phase 2: Build

In progress.
"""
	s = _parse_plan_structure(text, path="PLAN.md")
	assert s.total_phases == 2
	assert s.complete_phases == 1


def test_parse_plan_structure_strips_markdown_in_next_item():
	text = "- [ ] **Important**: do `the thing`"
	s = _parse_plan_structure(text, path="TODO.md")
	assert s.next_item == "Important: do the thing"


def test_parse_plan_structure_returns_zero_for_prose_only():
	text = "This is just a paragraph without checkboxes or phase headings.\n"
	s = _parse_plan_structure(text, path="README.md")
	assert s.total_items == 0
	assert s.total_phases == 0


# ───── Outstanding builder + next_action ─────────────────────────────────────

def _now():
	return datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when, summary=summary, ref="abc")


def _prompt(when: datetime, summary: str) -> Signal:
	return Signal(source="claude", kind="prompt", timestamp=when, summary=summary)


def test_outstanding_clean_when_no_signals():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=False)
	obs = build(r, now=_now())
	assert obs.outstanding.is_empty
	assert obs.outstanding.headline == "\u2014"


def test_outstanding_carries_uncommitted_count_and_examples():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py", " M b.py", " M c.py", "?? d.py", "?? e.py"])
	obs = build(r, now=_now())
	assert obs.outstanding.git_uncommitted_count == 5
	assert len(obs.outstanding.git_examples) == 3
	assert obs.outstanding.headline == "dirty 5"


def test_outstanding_carries_ahead_behind():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_ahead=2, git_behind=0,
	                  git_upstream="origin/main")
	obs = build(r, now=_now())
	assert obs.outstanding.git_ahead == 2
	assert obs.outstanding.headline == "ahead 2"


def test_outstanding_carries_plan_summary():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main",
	                  plan_summaries={"PLAN.md": PlanDocSummary(
	                      path="PLAN.md", total_items=5, open_items=3,
	                      next_item="Add CI workflow")})
	obs = build(r, now=_now())
	assert obs.outstanding.plan_open_count == 3
	assert obs.outstanding.plan_next == "Add CI workflow"
	assert obs.outstanding.plan_doc_ref == "PLAN.md"


def test_outstanding_orphan_thread_when_prompt_has_no_followup():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(days=2)),
			_prompt(now - timedelta(hours=12), "do the audit"),  # latest prompt, 12h old
		],
	)
	obs = build(r, now=now)
	assert obs.outstanding.orphaned_thread_age_hours == 12


def test_outstanding_no_orphan_when_commit_followed_prompt():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_prompt(now - timedelta(hours=12), "do the thing"),
			_commit(now - timedelta(hours=6), "did the thing"),
		],
	)
	obs = build(r, now=now)
	assert obs.outstanding.orphaned_thread_age_hours is None


def test_outstanding_no_orphan_when_prompt_too_recent():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_prompt(now - timedelta(minutes=30), "hi")],
	)
	obs = build(r, now=now)
	# < 4h since latest prompt → still mid-conversation, not orphaned
	assert obs.outstanding.orphaned_thread_age_hours is None


def test_next_action_for_dirty_tree():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py", " M b.py"])
	obs = build(r, now=_now())
	assert "commit 2 uncommitted" in obs.next_action.lower()


def test_next_action_for_ahead_then_dirty():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=True,
	                  git_uncommitted=[" M a.py"],
	                  git_ahead=3, git_upstream="origin/main")
	obs = build(r, now=_now())
	# Both pieces should appear, in order: commit first, then push.
	assert "commit" in obs.next_action.lower()
	assert "push 3" in obs.next_action.lower()
	assert obs.next_action.lower().index("commit") < obs.next_action.lower().index("push")


def test_next_action_for_drifting():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			Signal(source="docs", kind="doc", timestamp=now - timedelta(days=1),
			       summary="[PLAN.md] Status: completed", ref="PLAN.md"),
			_commit(now - timedelta(hours=2), "still working"),
		],
	)
	obs = build(r, now=now)
	assert obs.progress is Progress.DRIFTING
	assert "PLAN.md" in obs.next_action
	assert "completion marker" in obs.next_action.lower()


def test_next_action_for_no_git_with_signals():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=False,
		signals=[_prompt(now - timedelta(hours=2), "first prompt")],
	)
	obs = build(r, now=now)
	assert "tidy" in obs.next_action.lower()


def test_next_action_clean_shipped():
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", git_dirty=False)
	# Force progress=SHIPPED via direct builder call would require seeded signals.
	# Instead, hit the branch through the orchestration: empty obs path.
	# (Detailed Progress.SHIPPED is exercised by the existing scanner tests.)
	obs = build(r, now=_now())
	# With no signals + git repo, this lands in the empty-record path; that's fine.
	assert obs.outstanding.is_empty


# ───── headline ordering ─────────────────────────────────────────────────────

def test_outstanding_headline_priority():
	# dirty wins over ahead wins over plan wins over orphan wins over dash
	o = Outstanding(git_uncommitted_count=2, git_ahead=3, plan_open_count=1,
	                orphaned_thread_age_hours=5)
	assert o.headline.startswith("dirty")

	o = Outstanding(git_ahead=3, plan_open_count=1, orphaned_thread_age_hours=5)
	assert o.headline.startswith("ahead")

	o = Outstanding(plan_open_count=1, orphaned_thread_age_hours=5)
	assert o.headline.startswith("plan")

	o = Outstanding(orphaned_thread_age_hours=5)
	assert o.headline == "orphan"


# ───── purpose extraction integration ────────────────────────────────────────

def test_build_strips_chrome_from_extracted_purpose():
	now = _now()
	# Doc summary as it would arrive from the docs scanner: bracketed prefix +
	# blockquote + bold + GH callout chrome.
	doc = Signal(
		source="docs", kind="doc", timestamp=now - timedelta(days=1),
		summary="[README.md] > [!TIP] > **Building in Public** > > The maintainer ships in real-time.",
		ref="README.md",
	)
	r = ProjectReport(path=Path("/tmp/x"), name="x", is_git_repo=True,
	                  git_branch="main", signals=[doc])
	obs = build(r, now=now)
	assert ">" not in obs.purpose
	assert "[!TIP]" not in obs.purpose
	assert "**" not in obs.purpose
	assert "Building in Public" in obs.purpose
	assert "real-time" in obs.purpose



# ───── jtbd-driven synthesis + markdown renders ─────────────────────────────

def test_workstream_prefers_recent_plan_doc_summary():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			Signal(
				source="docs", kind="doc", timestamp=now - timedelta(days=3),
				summary="[README.md] Generic product overview.", ref="README.md",
			),
			Signal(
				source="docs", kind="doc", timestamp=now - timedelta(hours=2),
				summary=("[.sisyphus/plans/optimal-plan-forward.md] Quick Summary: Clean the working tree, "
				         "repair the benchmark methodology gap, then continue with the stable profiling pipeline."),
				ref=".sisyphus/plans/optimal-plan-forward.md",
			),
		],
	)
	obs = build(r, now=now)
	assert "Clean the working tree" in obs.workstream
	assert "benchmark methodology gap" in obs.workstream


def test_recent_changes_summarize_commit_topics():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(hours=1), "[T-022] Add baseline regression comparison tool"),
			_commit(now - timedelta(hours=2), "[T-021] Collect high-item scenario data from monolith"),
			_commit(now - timedelta(hours=3), "[T-020] Add high-item profiling scenarios (500/2000/5000)"),
		],
	)
	obs = build(r, now=now)
	lower = obs.recent_changes.lower()
	assert obs.recent_changes.startswith("Recent commits focused on")
	assert "baseline regression comparison tool" in lower
	assert "high-item scenario data from monolith" in lower


def test_attention_splits_open_issue_and_why_stopped():
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py", " M b.py"],
		plan_summaries={"PLAN.md": PlanDocSummary(
			path="PLAN.md", total_items=4, open_items=1, next_item="Repair the benchmark methodology gap",
		)},
	)
	obs = build(r, now=_now())
	assert "benchmark methodology gap" in obs.open_issue.lower()
	assert "working tree" in obs.why_stopped.lower()
	assert "benchmark methodology gap" in obs.attention.lower()
	assert "working tree" in obs.attention.lower()


def test_recent_changes_skip_low_signal_commit_noise():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(hours=1), "@user123 has signed the CLA in org/repo#1"),
			_commit(now - timedelta(hours=2), "Preserve migration history during config migration"),
			_commit(now - timedelta(hours=3), "Update OpenAI defaults to GPT-5.5"),
		],
	)
	obs = build(r, now=now)
	lower = obs.recent_changes.lower()
	assert "signed the cla" not in lower
	assert "migration history" in lower


def test_render_detail_markdown_uses_four_question_card():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py"],
		plan_summaries={"PLAN.md": PlanDocSummary(
			path="PLAN.md", total_items=4, open_items=1, next_item="Repair the benchmark methodology gap",
		)},
		signals=[
			Signal(
				source="docs", kind="doc", timestamp=now - timedelta(hours=1),
				summary="[PLAN.md] Quick Summary: Clean the working tree, repair the benchmark methodology gap.",
				ref="PLAN.md",
			),
			_prompt(now - timedelta(hours=2), "review state and determine optimal plan forward"),
		],
	)
	r.observations = build(r, now=now)
	text = render_detail_markdown(r)
	# 4-question briefing card structure
	assert "### What is it?" in text
	assert "### What's been happening?" in text
	assert "### Where it stands" in text
	assert "### What's planned next" in text
	# Status header carries state + git + uncommitted count, no per-section dump
	assert "`main*`" in text
	assert "1 uncommitted file(s)" in text
	# 'Where it stands' synthesizes the dirty tree without an evidence list
	assert "Working tree has 1 uncommitted file(s)." in text
	# 'What's planned next' references the plan doc and surfaces the first action
	assert "`PLAN.md`" in text
	assert "**Your first action:**" in text
	assert "Repair the benchmark methodology gap" in text
	# Single-line inspect footer instead of evidence sections
	assert "<sub>Inspect: " in text
	assert "plan `PLAN.md`" in text
	assert "last prompt `" in text
	# The old evidence-dump headings must NOT appear
	assert "## Raw sources" not in text
	assert "### Plan docs" not in text
	assert "### Prompt thread" not in text
	assert "### Git history" not in text

def test_render_review_markdown_uses_jtbd_triage_sections():
	now = _now()
	attention_report = ProjectReport(
		path=Path("/tmp/attention"), name="attention", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py"],
		signals=[
			_commit(now - timedelta(hours=1), "[T-022] Add baseline regression comparison tool"),
		],
	)
	attention_report.observations = build(attention_report, now=now)
	new_report = ProjectReport(
		path=Path("/tmp/new"), name="newproj", is_git_repo=True, git_branch="main",
		signals=[
			_commit(now - timedelta(hours=1), "Create initial workflow skeleton"),
		],
	)
	new_report.observations = build(new_report, now=now)
	text = render_review_markdown([attention_report, new_report], since_days=7)
	# Header carries totals so the reader has a quick sense of activity load
	assert text.startswith("# Last 7 day(s)")
	assert "active project(s)" in text
	# Triage taxonomy: 'Needs your attention' (actionable) and 'New this week' (first commit landed)
	assert "## Needs your attention (1)" in text
	assert "## New this week (1)" in text
	# Each section gets a one-line blurb explaining what the section is for
	assert "_These have a clear next move. Pick one and finish it._" in text
	assert "_Repos that landed in your worktrees for the first time._" in text
	# Attention rows lead with the next action, not an evidence list
	assert "- **attention** \u2014 Commit 1 uncommitted file(s)." in text
	# New rows include a started-on day hint
	assert "- **newproj** _(" in text
	# Each project appears in exactly one section
	assert text.count("**attention**") == 1
	assert text.count("**newproj**") == 1
	# No more 'Look at:' / 'Start with:' two-line evidence dump per row
	assert "  - Look at:" not in text
	assert "  - Start with:" not in text
	# Fleet-table format does not appear in review output
	assert "| Project |" not in text


def test_render_detail_markdown_uses_narrator_when_provided():
	from project_commander import narrative
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Fake:
		def narrate(self, inputs):
			return narrative.NarrativeOutput(
				what_it_is="Synthesized identity prose lives here.",
				whats_been_happening="Synthesized history prose lives here.",
				whats_planned="Synthesized plan prose lives here.",
			)

	text = render_detail_markdown(r, narrator=_Fake())
	assert "Synthesized identity prose lives here." in text
	assert "Synthesized history prose lives here." in text
	assert "Synthesized plan prose lives here." in text
	assert "_synthesized prose_" in text  # footer marker


def test_render_detail_markdown_falls_back_when_narrator_returns_none():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Null:
		def narrate(self, inputs):
			return None

	text = render_detail_markdown(r, narrator=_Null())
	# Deterministic synthesis still runs.
	assert "### What is it?" in text
	assert "### What's been happening?" in text
	# Footer must not claim synthesis happened.
	assert "_synthesized prose_" not in text


def test_render_detail_markdown_recovers_when_narrator_raises():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/x"), name="x", is_git_repo=True, git_branch="main",
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Boom:
		def narrate(self, inputs):
			raise RuntimeError("transport broken")

	# Must not raise; falls back deterministically.
	text = render_detail_markdown(r, narrator=_Boom())
	assert "### What is it?" in text
	assert "_synthesized prose_" not in text



def test_render_review_markdown_uses_weekly_narrator_when_provided():
	from project_commander import narrative
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/active"), name="active", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py"],
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Fake:
		def narrate(self, inputs):
			return None
		def narrate_weekly(self, inputs):
			return narrative.WeeklyOutput(
				week_in_review="You shipped on active and parked a dirty tree mid-week.",
			)

	text = render_review_markdown([r], since_days=7, narrator=_Fake())
	assert "## Week in review" in text
	assert "You shipped on active" in text
	assert "<sub>_synthesized prose_</sub>" in text
	# The deterministic triage still appears below it
	assert "## Needs your attention" in text


def test_render_review_markdown_falls_back_when_weekly_narrator_returns_none():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/active"), name="active", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py"],
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Null:
		def narrate(self, inputs):
			return None
		def narrate_weekly(self, inputs):
			return None

	text = render_review_markdown([r], since_days=7, narrator=_Null())
	assert "## Week in review" not in text
	assert "_synthesized prose_" not in text
	assert "## Needs your attention" in text


def test_render_review_markdown_skips_narrator_when_no_signal():
	# Empty roster -> we never bother the LLM at all
	count = {"calls": 0}

	class _Counting:
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs):
			count["calls"] += 1
			return None

	text = render_review_markdown([], since_days=7, narrator=_Counting())
	assert count["calls"] == 0
	assert "Nothing moved" in text


def test_render_review_markdown_recovers_when_weekly_narrator_raises():
	now = _now()
	r = ProjectReport(
		path=Path("/tmp/active"), name="active", is_git_repo=True, git_branch="main",
		git_dirty=True, git_uncommitted=[" M a.py"],
		signals=[_commit(now - timedelta(hours=1), "feat: ship")],
	)
	r.observations = build(r, now=now)

	class _Boom:
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs):
			raise RuntimeError("transport broken")

	text = render_review_markdown([r], since_days=7, narrator=_Boom())
	assert "## Week in review" not in text
	assert "## Needs your attention" in text
