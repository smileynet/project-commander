"""Tests for the `dod` subcommand: parsing, pattern matching, evaluation,
result aggregation, JSON shape, and end-to-end run() exit codes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_commander.dod import (
	_PATTERNS,
	evaluate,
	evaluate_items,
	find_dod_file,
	match_pattern,
	parse_dod_file,
	render_json,
	render_markdown,
)
from project_commander.models import PlanDocSummary, ProjectReport, Signal


# ───── helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
	return datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _commit(when: datetime, summary: str = "x") -> Signal:
	return Signal(source="git", kind="commit", timestamp=when,
	              summary=summary, ref="abc")


def _prompt(when: datetime, summary: str) -> Signal:
	return Signal(source="claude", kind="prompt", timestamp=when, summary=summary)


def _report(tmp_path: Path, **kw) -> ProjectReport:
	"""Build a ProjectReport with observations populated, project rooted at tmp_path."""
	from project_commander.observations import build
	defaults = dict(
		path=tmp_path, name="x", is_git_repo=True, git_branch="main",
		git_dirty=False,
	)
	defaults.update(kw)
	r = ProjectReport(**defaults)
	r.observations = build(r, now=_now())
	return r


# ───── find_dod_file ─────────────────────────────────────────────────────────

def test_find_dod_file_default(tmp_path: Path):
	(tmp_path / "DOD.md").write_text("- [ ] something\n")
	found = find_dod_file(tmp_path)
	assert found is not None and found.name == "DOD.md"


def test_find_dod_file_case_insensitive(tmp_path: Path):
	(tmp_path / "dod.md").write_text("- [ ] something\n")
	assert find_dod_file(tmp_path) is not None


def test_find_dod_file_alias(tmp_path: Path):
	(tmp_path / "DEFINITION_OF_DONE.md").write_text("- [ ] foo\n")
	assert find_dod_file(tmp_path) is not None


def test_find_dod_file_missing(tmp_path: Path):
	assert find_dod_file(tmp_path) is None


def test_find_dod_file_override(tmp_path: Path):
	custom = tmp_path / "criteria.md"
	custom.write_text("- [ ] x\n")
	assert find_dod_file(tmp_path, override=custom) == custom
	# Override pointing at a non-file → None (does not fall back)
	assert find_dod_file(tmp_path, override=tmp_path / "missing.md") is None


# ───── parse_dod_file ────────────────────────────────────────────────────────

def test_parse_dod_file_extracts_checkboxes(tmp_path: Path):
	p = tmp_path / "DOD.md"
	p.write_text(
		"# Definition of Done\n"
		"\n"
		"Some prose.\n"
		"\n"
		"## Mechanical\n"
		"- [ ] Working tree clean\n"
		"- [x] README updated\n"
		"* [ ] Pushed to origin\n"
		"\n"
		"## Manual\n"
		"+ [ ] Demoed to Sam\n"
		"\n"
		"Not a checkbox: just a line.\n"
	)
	items = parse_dod_file(p)
	assert items == [
		("Working tree clean", False),
		("README updated", True),
		("Pushed to origin", False),
		("Demoed to Sam", False),
	]


def test_parse_dod_file_strips_markdown_chrome(tmp_path: Path):
	p = tmp_path / "DOD.md"
	p.write_text(
		"- [ ] **Working tree** is clean\n"
		"- [ ] *no* dirty files\n"
		"- [ ] `verify passes`\n"
	)
	items = parse_dod_file(p)
	assert [t for t, _ in items] == [
		"Working tree is clean",
		"no dirty files",
		"verify passes",
	]


def test_parse_dod_file_missing_returns_empty(tmp_path: Path):
	assert parse_dod_file(tmp_path / "nope.md") == []


# ───── pattern registry ──────────────────────────────────────────────────────

def test_every_pattern_is_unique_named():
	names = [p.name for p in _PATTERNS]
	assert len(names) == len(set(names)), f"duplicate auto-check names: {names}"


def test_match_pattern_recognizes_canonical_phrasings():
	# (criterion text, expected auto-check name)
	cases = [
		("Working tree clean",                          "working_tree_clean"),
		("working tree is clean",                       "working_tree_clean"),
		("no uncommitted changes",                      "working_tree_clean"),
		("no dirty files",                              "working_tree_clean"),
		("Pushed to origin",                            "branch_in_sync"),
		("in sync with upstream",                       "branch_in_sync"),
		("no unpushed commits",                         "branch_in_sync"),
		("branch at parity",                            "branch_in_sync"),
		("On main branch",                              "branch_is_main"),
		("branch is master",                            "branch_is_main"),
		("No orphan threads",                           "no_orphan_thread"),
		("no dangling prompts",                         "no_orphan_thread"),
		("no plan-drift",                               "no_plan_drift"),
		("plan doc matches reality",                    "no_plan_drift"),
		("Plan complete",                               "plan_complete"),
		("all phases complete",                         "plan_complete"),
		("every checkbox checked",                      "plan_complete"),
		("substantive prompts",                         "prompts_substantive"),
		("Verify passes",                               "verify_all"),
		("all verify checks passing",                   "verify_all"),
	]
	for text, expected in cases:
		hit = match_pattern(text)
		assert hit is not None, f"no pattern matched: {text!r}"
		pat, _ = hit
		assert pat.name == expected, (
			f"{text!r} matched {pat.name}, expected {expected}"
		)


def test_match_pattern_returns_none_for_unknown_criteria():
	assert match_pattern("Feature X documented in README") is None
	assert match_pattern("Demoed to product owner") is None
	assert match_pattern("Latency below 100ms") is None


# ───── file_exists pattern ───────────────────────────────────────────────────

def test_file_exists_recognizes_subject_form():
	hit = match_pattern("docs/PLAN.md exists")
	assert hit is not None
	pat, m = hit
	assert pat.name == "file_exists"
	from project_commander.dod import _extract_file_path
	assert _extract_file_path(m) == "docs/PLAN.md"


def test_file_exists_recognizes_predicate_form():
	hit = match_pattern("acceptance doc published at docs/features/foo.md")
	assert hit is not None
	pat, m = hit
	assert pat.name == "file_exists"
	from project_commander.dod import _extract_file_path
	assert _extract_file_path(m) == "docs/features/foo.md"


def test_file_exists_ignores_non_path_phrases():
	# Should NOT match: no path-shaped token (no dot-extension, no slash).
	assert match_pattern("the documentation exists somewhere") is None
	assert match_pattern("everything is present and correct") is None


def test_file_exists_check_pass(tmp_path: Path):
	(tmp_path / "PLAN.md").write_text("hi")
	r = _report(tmp_path)
	out = evaluate_items(r, [("PLAN.md exists", False)])
	assert out[0].status == "PASS"
	assert out[0].auto_check == "file_exists"
	assert "found at PLAN.md" in out[0].detail


def test_file_exists_check_fail(tmp_path: Path):
	r = _report(tmp_path)
	out = evaluate_items(r, [("MISSING.md exists", False)])
	assert out[0].status == "FAIL"
	assert "not found" in out[0].detail


def test_file_exists_nested_path(tmp_path: Path):
	(tmp_path / "docs").mkdir()
	(tmp_path / "docs" / "FEATURE.md").write_text("hi")
	r = _report(tmp_path)
	out = evaluate_items(r, [("acceptance doc published at docs/FEATURE.md", False)])
	assert out[0].status == "PASS"
	assert "docs/FEATURE.md" in out[0].detail


def test_file_exists_directory_path_passes(tmp_path: Path):
	# Directories also count — useful for "internal/web/templates exists" checks.
	(tmp_path / "internal" / "web").mkdir(parents=True)
	r = _report(tmp_path)
	out = evaluate_items(r, [("internal/web exists", False)])
	assert out[0].status == "PASS"
	assert "directory" in out[0].detail


def test_file_exists_strips_dotdot_prefix_via_regex(tmp_path: Path):
	# `..` is not a word-boundary anchor; the regex extracts only the path tail.
	# A criterion mentioning `../etc/passwd exists` is matched as `etc/passwd
	# exists`, which fails because no such file exists inside the project.
	r = _report(tmp_path)
	out = evaluate_items(r, [("../etc/passwd exists", False)])
	assert out[0].status == "FAIL"
	assert "etc/passwd" in out[0].detail
	# The path-escape guard itself is exercised in
	# test_file_exists_rejects_explicit_escape below — that path goes through
	# _check_file_exists directly, bypassing the regex stripping.


def test_file_exists_rejects_explicit_escape(tmp_path: Path):
	# When something does manage to construct a path that escapes the root
	# (resolved symlink, edge-case extractor), the runtime guard SKIPs it.
	from project_commander.dod import _check_file_exists
	r = _report(tmp_path)
	cr = _check_file_exists(r, path="../etc/passwd")
	assert cr.status == "SKIP"
	assert "escapes project root" in cr.detail


# ───── narrator wiring ───────────────────────────────────────────────────────

class _StubNarrator:
	"""Returns a canned DoDOutput. Used to verify the wiring without HTTP."""

	def __init__(self, observation: str):
		from project_commander.narrative import DoDOutput
		self._out = DoDOutput(observation=observation)
		self.calls = 0

	def narrate_dod(self, inputs):
		self.calls += 1
		# Sanity: narrator gets criteria with their evaluated statuses.
		assert isinstance(inputs.criteria, tuple)
		assert all(len(c) == 5 for c in inputs.criteria)
		return self._out

	def narrate(self, inputs): return None
	def narrate_weekly(self, inputs): return None


def test_narrator_observation_attached_when_provided(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n- [x] Reviewed\n")
	narr = _StubNarrator("Tree is clean and the manual review is in. Nothing left to ship.")
	result = evaluate(r, narrator=narr)
	assert result.observation.startswith("Tree is clean")
	assert narr.calls == 1


def test_narrator_default_is_no_observation(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")
	result = evaluate(r)  # no narrator arg
	assert result.observation == ""


def test_narrator_failure_falls_back_silently(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")

	class _Boom:
		def narrate_dod(self, inputs):
			raise RuntimeError("provider exploded")
		def narrate(self, inputs): return None
		def narrate_weekly(self, inputs): return None

	result = evaluate(r, narrator=_Boom())
	assert result.observation == ""  # swallowed
	assert result.complete >= 1       # mechanical core unaffected


def test_narrator_too_short_observation_rejected(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")
	narr = _StubNarrator("hi")  # below is_usable() threshold
	result = evaluate(r, narrator=narr)
	assert result.observation == ""


def test_narrator_observation_in_json_output(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")
	narr = _StubNarrator("Working tree is clean — the only mechanical check passes.")
	result = evaluate(r, narrator=narr)
	doc = json.loads(render_json(result))
	assert doc["observation"].startswith("Working tree is clean")


def test_narrator_observation_in_markdown_output(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")
	narr = _StubNarrator("Tree is clean; nothing else defined yet beyond the one mechanical check.")
	result = evaluate(r, narrator=narr)
	out = render_markdown(result)
	assert "_Observation:_" in out
	assert "Tree is clean" in out


# ───── auto-check evaluation ─────────────────────────────────────────────────

def test_user_checked_criterion_is_done(tmp_path: Path):
	r = _report(tmp_path)
	out = evaluate_items(r, [("anything goes here", True)])
	assert out[0].status == "DONE"
	assert out[0].user_checked is True


def test_unmatched_criterion_is_manual(tmp_path: Path):
	r = _report(tmp_path)
	out = evaluate_items(r, [("Feature X documented in README", False)])
	assert out[0].status == "MANUAL"
	assert out[0].auto_check == ""


def test_working_tree_clean_pass(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	out = evaluate_items(r, [("Working tree clean", False)])
	assert out[0].status == "PASS"
	assert out[0].auto_check == "working_tree_clean"


def test_working_tree_clean_fail(tmp_path: Path):
	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py", " M b.py"])
	out = evaluate_items(r, [("Working tree clean", False)])
	assert out[0].status == "FAIL"
	assert "2 uncommitted" in out[0].detail


def test_branch_in_sync_pass(tmp_path: Path):
	r = _report(tmp_path, git_upstream="origin/main", git_ahead=0, git_behind=0)
	out = evaluate_items(r, [("Pushed to origin", False)])
	assert out[0].status == "PASS"


def test_branch_in_sync_skip_without_upstream(tmp_path: Path):
	r = _report(tmp_path, git_upstream=None)
	out = evaluate_items(r, [("Pushed to origin", False)])
	assert out[0].status == "SKIP"


def test_branch_in_sync_fail_when_ahead(tmp_path: Path):
	r = _report(tmp_path, git_upstream="origin/main", git_ahead=2)
	out = evaluate_items(r, [("Pushed to origin", False)])
	assert out[0].status == "FAIL"
	assert "ahead" in out[0].detail


def test_branch_is_main_pass(tmp_path: Path):
	r = _report(tmp_path, git_branch="main")
	out = evaluate_items(r, [("On main branch", False)])
	assert out[0].status == "PASS"


def test_branch_is_main_fail_on_feature_branch(tmp_path: Path):
	r = _report(tmp_path, git_branch="feature/foo")
	out = evaluate_items(r, [("On main branch", False)])
	assert out[0].status == "FAIL"


def test_no_orphan_thread_fail(tmp_path: Path):
	now = _now()
	r = _report(
		tmp_path,
		signals=[
			_commit(now - timedelta(days=2)),
			_prompt(now - timedelta(hours=12), "audit the system"),
		],
	)
	out = evaluate_items(r, [("No orphan threads", False)])
	assert out[0].status == "FAIL"


def test_plan_complete_pass(tmp_path: Path):
	r = _report(
		tmp_path,
		plan_summaries={"PLAN.md": PlanDocSummary(
			path="PLAN.md", total_items=3, open_items=0,
			total_phases=2, complete_phases=2)},
	)
	out = evaluate_items(r, [("All phases complete", False)])
	assert out[0].status == "PASS"


def test_plan_complete_fail(tmp_path: Path):
	r = _report(
		tmp_path,
		plan_summaries={"PLAN.md": PlanDocSummary(
			path="PLAN.md", total_items=3, open_items=2,
			total_phases=2, complete_phases=0)},
	)
	out = evaluate_items(r, [("Plan complete", False)])
	assert out[0].status == "FAIL"
	assert "2 unchecked" in out[0].detail


def test_plan_complete_skip_when_no_plan_doc(tmp_path: Path):
	r = _report(tmp_path)
	out = evaluate_items(r, [("Plan complete", False)])
	assert out[0].status == "SKIP"


def test_verify_all_pass(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False, git_upstream="origin/main",
	            git_ahead=0, git_behind=0)
	out = evaluate_items(r, [("Verify passes", False)])
	assert out[0].status == "PASS"


def test_verify_all_fail(tmp_path: Path):
	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py"])
	out = evaluate_items(r, [("Verify passes", False)])
	assert out[0].status == "FAIL"
	assert "working_tree_clean" in out[0].detail


# ───── DoDResult aggregation ─────────────────────────────────────────────────

def test_dod_result_progress_math(tmp_path: Path):
	# Layout the criteria so we exercise every status:
	# 1 PASS + 1 PASS + 1 DONE + 1 FAIL + 1 SKIP + 1 MANUAL.
	# (Dirty tree forces working_tree_clean→FAIL; no upstream forces
	# branch_in_sync→SKIP. verify_all would also fail given the dirty
	# tree, so we deliberately don't include it here.)
	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py"],
	            git_upstream=None, git_branch="main")
	(tmp_path / "DOD.md").write_text(
		"- [ ] Working tree clean\n"           # FAIL
		"- [ ] On main branch\n"               # PASS
		"- [x] Demoed to Sam\n"                # DONE
		"- [ ] No orphan threads\n"            # PASS (no signals → no orphan)
		"- [ ] Pushed to origin\n"             # SKIP (no upstream)
		"- [ ] Latency under 100ms\n"          # MANUAL
	)
	result = evaluate(r)
	statuses = [c.status for c in result.criteria]
	assert statuses == ["FAIL", "PASS", "DONE", "PASS", "SKIP", "MANUAL"]
	assert result.complete == 3          # PASS + PASS + DONE
	assert result.outstanding == 2       # FAIL + MANUAL
	assert result.skipped == 1
	assert result.total_relevant == 5    # 6 - 1 SKIP
	assert result.percent == 60
	assert result.is_complete is False


def test_dod_result_is_complete_when_no_outstanding(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text(
		"- [ ] Working tree clean\n"
		"- [ ] On main branch\n"
		"- [x] Manually verified\n"
	)
	result = evaluate(r)
	assert result.complete == 3
	assert result.outstanding == 0
	assert result.percent == 100
	assert result.is_complete is True


def test_dod_result_no_file(tmp_path: Path):
	r = _report(tmp_path)
	result = evaluate(r)
	assert result.file_exists is False
	assert result.criteria == ()
	assert "DOD.md" in result.next_action
	assert result.is_complete is False
	assert result.percent == 0


def test_dod_result_file_with_no_items(tmp_path: Path):
	r = _report(tmp_path)
	(tmp_path / "DOD.md").write_text("# Definition of Done\n\nProse only.\n")
	result = evaluate(r)
	assert result.file_exists is True
	assert result.criteria == ()
	assert "no `- [ ]` items" in result.next_action


def test_dod_result_next_action_prefers_fail_over_manual(tmp_path: Path):
	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py"])
	(tmp_path / "DOD.md").write_text(
		"- [ ] Latency under 100ms\n"
		"- [ ] Working tree clean\n"
	)
	result = evaluate(r)
	assert "Working tree clean" in result.next_action
	assert "Address" in result.next_action


def test_dod_result_next_action_falls_back_to_manual(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text(
		"- [ ] Working tree clean\n"
		"- [ ] Demoed to product owner\n"
	)
	result = evaluate(r)
	assert "Demoed to product owner" in result.next_action
	assert "Confirm" in result.next_action


# ───── rendering ─────────────────────────────────────────────────────────────

def test_render_json_single_project(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n- [x] Reviewed\n")
	result = evaluate(r)
	doc = json.loads(render_json(result))
	assert doc["project"] == "x"
	assert doc["file_exists"] is True
	assert doc["complete"] == 2
	assert doc["outstanding"] == 0
	assert doc["is_complete"] is True
	assert len(doc["criteria"]) == 2
	first = doc["criteria"][0]
	assert first["status"] == "PASS"
	assert first["auto_check"] == "working_tree_clean"
	assert first["user_checked"] is False


def test_render_json_array_for_multiple(tmp_path: Path):
	r1 = _report(tmp_path / "a", git_dirty=False)
	r2 = _report(tmp_path / "b", git_dirty=False)
	(tmp_path / "a").mkdir(exist_ok=True)
	(tmp_path / "b").mkdir(exist_ok=True)
	# Need the directories to exist for any file lookups; results without
	# DOD files are still part of the output.
	results = [evaluate(r1), evaluate(r2)]
	doc = json.loads(render_json(results))
	assert isinstance(doc, list)
	assert len(doc) == 2


def test_render_markdown_table_shape(tmp_path: Path):
	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py"])
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")
	out = render_markdown(evaluate(r))
	assert "## x" in out
	assert "Definition of Done" in out
	assert "| `FAIL` |" in out
	assert "**Status:** OPEN" in out
	assert "**Next:**" in out


def test_render_markdown_done_no_next(tmp_path: Path):
	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [x] reviewed\n")
	out = render_markdown(evaluate(r))
	assert "**Status:** DONE" in out
	# When nothing is outstanding, there is no Next: line.
	assert "**Next:**" not in out


# ───── end-to-end run() ──────────────────────────────────────────────────────

def test_run_exits_nonzero_when_outstanding(tmp_path: Path, monkeypatch):
	"""run() returns 1 when any project's DOD has outstanding criteria."""
	from project_commander import dod, cli

	r = _report(tmp_path, git_dirty=True, git_uncommitted=[" M a.py"])
	(tmp_path / "DOD.md").write_text("- [ ] Working tree clean\n")

	def fake_build_reports(args, *, git_recent_commits=50):
		return [r]

	monkeypatch.setattr(cli, "build_reports", fake_build_reports)

	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="cmd")
	dod.add_subparser(sub)
	args = parser.parse_args(["dod", "--format", "json"])
	rc = dod.run(args)
	assert rc == 1


def test_run_exits_zero_when_complete(tmp_path: Path, monkeypatch):
	from project_commander import dod, cli

	r = _report(tmp_path, git_dirty=False)
	(tmp_path / "DOD.md").write_text("- [x] reviewed\n- [ ] Working tree clean\n")

	def fake_build_reports(args, *, git_recent_commits=50):
		return [r]

	monkeypatch.setattr(cli, "build_reports", fake_build_reports)

	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="cmd")
	dod.add_subparser(sub)
	args = parser.parse_args(["dod", "--format", "json"])
	rc = dod.run(args)
	assert rc == 0


def test_run_exits_zero_when_no_dod_file(tmp_path: Path, monkeypatch):
	"""A project without a DOD.md is informative, not a failure: exit 0."""
	from project_commander import dod, cli

	r = _report(tmp_path)

	def fake_build_reports(args, *, git_recent_commits=50):
		return [r]

	monkeypatch.setattr(cli, "build_reports", fake_build_reports)

	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="cmd")
	dod.add_subparser(sub)
	args = parser.parse_args(["dod", "--format", "json"])
	rc = dod.run(args)
	assert rc == 0


def test_run_file_override_requires_single_project(tmp_path: Path, monkeypatch, capsys):
	from project_commander import dod, cli

	r1 = _report(tmp_path / "a")
	r2 = _report(tmp_path / "b")
	(tmp_path / "a").mkdir(exist_ok=True)
	(tmp_path / "b").mkdir(exist_ok=True)

	def fake_build_reports(args, *, git_recent_commits=50):
		return [r1, r2]

	monkeypatch.setattr(cli, "build_reports", fake_build_reports)

	custom = tmp_path / "shared.md"
	custom.write_text("- [ ] Working tree clean\n")

	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="cmd")
	dod.add_subparser(sub)
	args = parser.parse_args(["dod", "--file", str(custom), "--format", "json"])
	rc = dod.run(args)
	assert rc == 2
	err = capsys.readouterr().err
	assert "single project" in err
