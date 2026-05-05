"""Optional LLM narrator for the briefing card.

Three of the four detail-card sections benefit from synthesized prose:
- "What is it?"
- "What's been happening?"
- "What's planned next"

The fourth ("Where it stands") stays deterministic --- it is mechanical
state. The status header and the Inspect: footer also stay deterministic
so the user has an audit anchor next to the LLM prose.

Provider abstraction (each fail-soft if unavailable):
- AnthropicNarrator  (ANTHROPIC_API_KEY)
- OpenAINarrator     (OPENAI_API_KEY)
- OllamaNarrator     (OLLAMA_HOST, default http://localhost:11434)
- DisabledNarrator   (always returns None)

Failure modes that return None and let the caller fall back:
- No provider configured
- HTTP error / timeout / non-2xx
- Malformed JSON in response
- Empty / trivially short fields

Caching: prompt text + model id -> SHA-256 -> JSON file under
$XDG_CACHE_HOME/project-commander/narrative/. Signal change ->
prompt change -> key change -> cache miss. No TTL needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .models import ProjectReport, Signal
from .observations import Observations, is_procedural


# ─── tunables ────────────────────────────────────────────────────────────────

_IDENTITY_CHAR_CAP = 600
_PLAN_CHAR_CAP = 1000
_COMMIT_LIMIT = 30
_PROMPT_LIMIT = 15
_NETWORK_TIMEOUT_SEC = 30.0
_OLLAMA_TIMEOUT_SEC = 120.0  # local models can be slow on cold start

_IDENTITY_FILES = (
	"README.md", "README.rst", "README.txt", "README",
	"AGENTS.md", "AGENT.md", "CLAUDE.md", "GEMINI.md",
)

_PLAN_FILES = (
	"PLAN.md", "ROADMAP.md", "NEXT_STEPS.md", "IMPROVEMENTS.md", "TODO.md",
)


# ─── data shapes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NarrativeInputs:
	"""Structured signal package handed to the LLM."""

	project_name: str
	branch: str | None
	dirty: bool
	uncommitted_count: int
	ahead: int
	behind: int
	identity_excerpts: tuple[tuple[str, str], ...]   # (filename, text)
	plan_excerpt: tuple[str, str] | None             # (filename, text)
	recent_commits: tuple[tuple[datetime, str], ...]
	recent_prompts: tuple[tuple[datetime, str, str], ...]   # (ts, source, summary)
	last_active: datetime | None


@dataclass(frozen=True)
class NarrativeOutput:
	"""LLM-synthesized briefing fields. Replaces the body of three sections."""

	what_it_is: str
	whats_been_happening: str
	whats_planned: str

	def is_usable(self) -> bool:
		# Reject empty or trivially short results --- fall back to deterministic.
		return all(
			len(getattr(self, f).strip()) >= 8
			for f in ("what_it_is", "whats_been_happening", "whats_planned")
		)


@dataclass(frozen=True)
class WeeklyInputs:
	"""Aggregated cross-project triage data for a weekly review LLM call."""

	since_date: str             # ISO date, e.g. '2026-04-19'
	now_date: str               # ISO date, today
	since_days: int             # window length
	active_projects: int
	total_commits: int
	total_prompts: int
	attention: tuple[tuple[str, str, str], ...] = ()   # (name, action, outcome)
	new_this_week: tuple[tuple[str, str, str], ...] = ()
	moved_this_week: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class WeeklyOutput:
	"""LLM-synthesized week-in-review paragraph."""

	week_in_review: str

	def is_usable(self) -> bool:
		return len(self.week_in_review.strip()) >= 16


@dataclass(frozen=True)
class DoDInputs:
	"""Per-project DoD state package handed to the LLM observer."""

	project_name: str
	branch: str | None
	dirty: bool
	uncommitted_count: int
	ahead: int
	behind: int
	complete: int
	outstanding: int
	skipped: int
	total_relevant: int
	percent: int
	is_complete: bool
	next_action: str
	# (text, status, detail, auto_check, user_checked) per criterion, source order
	criteria: tuple[tuple[str, str, str, str, bool], ...]
	# Last few commit subjects so the observer can ground "what's landed" claims
	recent_commits: tuple[tuple[datetime, str], ...]


@dataclass(frozen=True)
class DoDOutput:
	"""LLM-synthesized observation comparing current state to the DoD."""

	observation: str

	def is_usable(self) -> bool:
		# 24 chars is enough room for a single concrete sentence; anything
		# shorter is a degenerate response we should not surface.
		return len(self.observation.strip()) >= 24


class Narrator(Protocol):
	"""LLM-or-equivalent that turns NarrativeInputs into prose, or None."""

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None: ...
	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None: ...
	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None: ...


# ─── input collection ────────────────────────────────────────────────────────

def collect_inputs(report: ProjectReport, observations: Observations | None) -> NarrativeInputs:
	"""Gather the structured signal package from a report + observations."""
	return NarrativeInputs(
		project_name=report.name,
		branch=report.git_branch,
		dirty=report.git_dirty,
		uncommitted_count=len(report.git_uncommitted),
		ahead=report.git_ahead,
		behind=report.git_behind,
		identity_excerpts=_collect_identity(report.path),
		plan_excerpt=_collect_plan(report.path),
		recent_commits=_collect_recent_commits(report.signals),
		recent_prompts=_collect_recent_prompts(report.signals),
		last_active=report.last_active,
	)


def _collect_identity(project: Path) -> tuple[tuple[str, str], ...]:
	out: list[tuple[str, str]] = []
	for name in _IDENTITY_FILES:
		p = project / name
		if not p.is_file():
			continue
		text = _safe_read_text(p, _IDENTITY_CHAR_CAP)
		if text:
			out.append((name, text))
		if len(out) >= 2:
			break
	return tuple(out)


def _collect_plan(project: Path) -> tuple[str, str] | None:
	for name in _PLAN_FILES:
		p = project / name
		if not p.is_file():
			continue
		text = _safe_read_text(p, _PLAN_CHAR_CAP)
		if text:
			return (name, text)
	return None


def _safe_read_text(path: Path, char_cap: int) -> str:
	try:
		raw = path.read_text(encoding="utf-8", errors="replace")
	except OSError:
		return ""
	# Drop trailing whitespace per line; preserve newlines for prose feel.
	collapsed = "\n".join(line.rstrip() for line in raw.splitlines())
	return collapsed[:char_cap].strip()


def _collect_recent_commits(signals: Iterable[Signal]) -> tuple[tuple[datetime, str], ...]:
	commits = sorted(
		(s for s in signals if s.source == "git" and s.kind == "commit"),
		key=lambda s: s.timestamp,
		reverse=True,
	)[:_COMMIT_LIMIT]
	return tuple((s.timestamp, s.summary) for s in commits)


def _collect_recent_prompts(signals: Iterable[Signal]) -> tuple[tuple[datetime, str, str], ...]:
	prompts = sorted(
		(s for s in signals if s.kind == "prompt" and not is_procedural(s.summary)),
		key=lambda s: s.timestamp,
		reverse=True,
	)[:_PROMPT_LIMIT]
	return tuple((s.timestamp, str(s.source), s.summary) for s in prompts)


# ─── prompt assembly ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
	"You analyze a software project's recent activity and produce three short "
	"status fields for a developer who is returning to the project after some "
	"time away. The developer wants to rebuild their mental model fast.\n"
	"\n"
	"You receive structured signals: identity excerpts (README/AGENTS first "
	"section), an optional plan-doc excerpt, recent git commit subjects with "
	"dates, recent agent-prompt subjects with dates, and current branch state.\n"
	"\n"
	"Synthesize three fields, each 2-4 sentences of plain prose:\n"
	"  what_it_is: What kind of project this is and what it does. Anchor in "
	"identity excerpts. If identity is absent or vague, say that plainly.\n"
	"  whats_been_happening: What work actually landed (commits) versus what "
	"was discussed but did not land (prompts without follow-up commits). "
	"Distinguish those two. Surface contradictions if commit themes diverge "
	"from prompt themes.\n"
	"  whats_planned: What the plan doc and recent prompts suggest comes next. "
	"If the plan doc claims completion but commits keep landing, surface that "
	"contradiction. If there is no plan signal, say so plainly.\n"
	"\n"
	"Rules:\n"
	"  - Plain prose. No bullet lists. No markdown headings. Do not start with 'this project'.\n"
	"  - No marketing language. No filler phrases like 'a robust solution'.\n"
	"  - Each field <= 60 words.\n"
	"  - Distinguish 'shipped' from 'attempted'.\n"
	"  - Never invent. If a field has no evidence, write 'No clear signal' for it.\n"
	"\n"
	"Output strictly as a JSON object with exactly three string keys: "
	'{"what_it_is": "...", "whats_been_happening": "...", "whats_planned": "..."}.'
	" No prose before or after the JSON."
)


def build_user_message(inputs: NarrativeInputs) -> str:
	"""Render the structured inputs as the LLM's user-message body."""
	lines: list[str] = []
	lines.append(f"# Project: {inputs.project_name}")
	state_bits = [f"branch: {inputs.branch or '(no branch)'}"]
	if inputs.dirty:
		state_bits.append(f"dirty ({inputs.uncommitted_count} uncommitted)")
	if inputs.ahead:
		state_bits.append(f"{inputs.ahead} commits ahead of upstream")
	if inputs.behind:
		state_bits.append(f"{inputs.behind} commits behind upstream")
	if inputs.last_active:
		state_bits.append(f"last activity: {inputs.last_active.strftime('%Y-%m-%d %H:%M UTC')}")
	lines.append("State: " + ", ".join(state_bits))
	lines.append("")

	if inputs.identity_excerpts:
		lines.append("## Identity excerpts")
		for name, text in inputs.identity_excerpts:
			lines.append(f"### {name}")
			lines.append(text)
			lines.append("")
	else:
		lines.append("## Identity excerpts")
		lines.append("(no README / AGENTS / CLAUDE / GEMINI doc found at project root)")
		lines.append("")

	if inputs.plan_excerpt is not None:
		name, text = inputs.plan_excerpt
		lines.append(f"## Planning doc --- {name}")
		lines.append(text)
		lines.append("")
	else:
		lines.append("## Planning doc")
		lines.append("(no PLAN/ROADMAP/NEXT_STEPS/TODO/IMPROVEMENTS doc found)")
		lines.append("")

	lines.append(f"## Recent commits (most recent first, max {_COMMIT_LIMIT})")
	if inputs.recent_commits:
		for ts, summary in inputs.recent_commits:
			lines.append(f"- {ts.strftime('%Y-%m-%d')}  {_one_line(summary)}")
	else:
		lines.append("(no commits in the scanned window)")
	lines.append("")

	lines.append(f"## Recent substantive agent prompts (most recent first, max {_PROMPT_LIMIT})")
	if inputs.recent_prompts:
		for ts, source, summary in inputs.recent_prompts:
			lines.append(f"- {ts.strftime('%Y-%m-%d %H:%M')}  {source}: {_one_line(summary)}")
	else:
		lines.append("(no substantive prompts in the scanned window)")
	lines.append("")

	return "\n".join(lines).rstrip() + "\n"


def _one_line(text: str, limit: int = 200) -> str:
	flat = " ".join(text.split())
	if len(flat) > limit:
		flat = flat[:limit - 1] + "\u2026"
	return flat


# ─── parse + cache ───────────────────────────────────────────────────────────

def parse_response(text: str) -> NarrativeOutput | None:
	"""Best-effort parse of an LLM JSON payload into NarrativeOutput."""
	body = _strip_code_fence(text.strip())
	try:
		payload = json.loads(body)
	except json.JSONDecodeError:
		return None
	if not isinstance(payload, dict):
		return None

	def _get(key: str) -> str:
		v = payload.get(key, "")
		return v.strip() if isinstance(v, str) else ""

	out = NarrativeOutput(
		what_it_is=_get("what_it_is"),
		whats_been_happening=_get("whats_been_happening"),
		whats_planned=_get("whats_planned"),
	)
	return out if out.is_usable() else None


def _strip_code_fence(text: str) -> str:
	"""Some models wrap JSON in ```json ... ``` despite instructions."""
	if not text.startswith("```"):
		return text
	first_nl = text.find("\n")
	if first_nl == -1:
		return text
	body = text[first_nl + 1:]
	if body.endswith("```"):
		body = body[:-3]
	return body.strip()


_WEEKLY_SYSTEM_PROMPT = (
	"You summarize a developer's recent activity across many projects (a 'weekly "
	"review'). The user already sees a triage table below your output --- what "
	"needs their attention, what's new, what moved. Your job is to give them the "
	"3-5 sentence narrative that ties the week together: what they actually "
	"shipped, what stalled, and any cross-project themes.\n"
	"\n"
	"You receive: window length, totals (active projects, commits, substantive "
	"prompts), and three triage sections (attention, new, moved) with one line "
	"per project containing a name, an action verb directive, and a brief "
	"outcome description.\n"
	"\n"
	"Rules:\n"
	"  - Plain prose. No lists. No markdown. No 'this week'.\n"
	"  - 3-5 sentences total.\n"
	"  - Lead with what shipped or moved, not what stalled.\n"
	"  - Surface cross-project themes if you see them (e.g. 'three projects "
	"saw test infrastructure work this week').\n"
	"  - Cite projects by name. Never invent project names or outcomes that "
	"are not in the input.\n"
	"  - If the data is thin (few projects, few commits), say so plainly. Do "
	"not pad with filler.\n"
	"  - Mention specific stalled work if it materially blocks the user, but "
	"do not turn the paragraph into a complaint.\n"
	"\n"
	"Output strictly as a JSON object with exactly one string key: "
	'{"week_in_review": "..."}.'
	" No prose before or after the JSON."
)


def build_weekly_message(inputs: WeeklyInputs) -> str:
	"""Render the weekly triage data as the LLM's user-message body."""
	lines: list[str] = []
	lines.append(f"# Weekly review --- last {inputs.since_days} day(s)")
	lines.append(f"Window: {inputs.since_date} to {inputs.now_date}")
	lines.append(
		f"Totals: {inputs.active_projects} active project(s), "
		f"{inputs.total_commits} commit(s), "
		f"{inputs.total_prompts} substantive prompt(s)"
	)
	lines.append("")

	for heading, bucket in (
		("## Needs your attention", inputs.attention),
		("## New this week", inputs.new_this_week),
		("## Moved this week", inputs.moved_this_week),
	):
		lines.append(heading)
		if not bucket:
			lines.append("(empty)")
			lines.append("")
			continue
		for name, action, outcome in bucket:
			parts = [f"- {name}:"]
			if action:
				parts.append(f"action='{_one_line(action, limit=140)}'")
			if outcome:
				parts.append(f"outcome='{_one_line(outcome, limit=200)}'")
			if not action and not outcome:
				parts.append("(no detail captured)")
			lines.append(" ".join(parts))
		lines.append("")

	return "\n".join(lines).rstrip() + "\n"


def parse_weekly_response(text: str) -> WeeklyOutput | None:
	"""Best-effort parse of an LLM JSON payload into WeeklyOutput."""
	body = _strip_code_fence(text.strip())
	try:
		payload = json.loads(body)
	except json.JSONDecodeError:
		return None
	if not isinstance(payload, dict):
		return None
	v = payload.get("week_in_review", "")
	if not isinstance(v, str):
		return None
	out = WeeklyOutput(week_in_review=v.strip())
	return out if out.is_usable() else None


_DOD_SYSTEM_PROMPT = (
	"You observe a project's progress against its written Definition of Done. "
	"You receive: the project's name, its current git state, the criteria list "
	"(each with status PASS/FAIL/DONE/MANUAL/SKIP and a detail string), the "
	"single most-blocking next_action the tool picked, and the most recent "
	"commit subjects.\n"
	"\n"
	"Write one observation, 2-3 plain prose sentences, comparing the current "
	"state to the defined criteria. The observation goes next to the mechanical "
	"status table; do not duplicate that table in prose. Instead:\n"
	"  - Reference specific criteria by their text (e.g. 'session_phases table "
	"exists', 'BDD tests cover all 7 ACs') when describing where work landed.\n"
	"  - Distinguish PASS (mechanically verified), DONE (user-confirmed by "
	"ticking [x]), and MANUAL (awaiting user confirmation) when relevant.\n"
	"  - Anchor 'what just happened' claims in the recent commit subjects when "
	"there is a clear connection.\n"
	"  - If the work is complete, name what shipped concretely. If it is in "
	"flight, name the specific gap. If it is barely started, say so plainly.\n"
	"\n"
	"Rules:\n"
	"  - Plain prose. No bullet lists. No markdown. No headings.\n"
	"  - Total length <= 80 words.\n"
	"  - No marketing language. No filler ('a robust solution', 'comprehensive').\n"
	"  - Distinguish 'shipped' from 'attempted'. Do not invent criteria or "
	"outcomes that are not in the inputs.\n"
	"  - If the inputs are too thin for a defensible observation, write a "
	"single sentence saying so.\n"
	"\n"
	"Output strictly as a JSON object with one string key: "
	'{"observation": "..."}.'
	" No prose before or after the JSON."
)


def build_dod_message(inputs: DoDInputs) -> str:
	"""Render the DoD state as the LLM's user-message body."""
	lines: list[str] = []
	lines.append(f"# Project: {inputs.project_name}")
	state_bits = [f"branch: {inputs.branch or '(no branch)'}"]
	if inputs.dirty:
		state_bits.append(f"dirty ({inputs.uncommitted_count} uncommitted)")
	if inputs.ahead:
		state_bits.append(f"{inputs.ahead} commits ahead of upstream")
	if inputs.behind:
		state_bits.append(f"{inputs.behind} commits behind upstream")
	lines.append("State: " + ", ".join(state_bits))
	lines.append("")

	lines.append("## Definition of Done progress")
	lines.append(
		f"complete: {inputs.complete}/{inputs.total_relevant} "
		f"({inputs.percent}%)  outstanding: {inputs.outstanding}  "
		f"skipped: {inputs.skipped}  is_complete: {inputs.is_complete}"
	)
	lines.append(f"next_action: {inputs.next_action}")
	lines.append("")

	lines.append("## Criteria")
	for text, status, detail, auto_check, user_checked in inputs.criteria:
		marker = "[x]" if user_checked else "[ ]"
		auto = f"  auto={auto_check}" if auto_check else ""
		det = f"  detail={detail}" if detail else ""
		lines.append(f"- {marker} {status:<6} {text}{auto}{det}")
	lines.append("")

	lines.append("## Recent commits (most recent first)")
	if inputs.recent_commits:
		for ts, summary in inputs.recent_commits:
			lines.append(f"- {ts.strftime('%Y-%m-%d')}  {_one_line(summary)}")
	else:
		lines.append("(no commits yet)")
	lines.append("")

	return "\n".join(lines).rstrip() + "\n"


def parse_dod_response(text: str) -> DoDOutput | None:
	"""Best-effort parse of an LLM JSON payload into DoDOutput."""
	body = _strip_code_fence(text.strip())
	try:
		payload = json.loads(body)
	except json.JSONDecodeError:
		return None
	if not isinstance(payload, dict):
		return None
	v = payload.get("observation", "")
	if not isinstance(v, str):
		return None
	out = DoDOutput(observation=v.strip())
	return out if out.is_usable() else None


def cache_root() -> Path:
	base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
	root = Path(base) / "project-commander" / "narrative"
	root.mkdir(parents=True, exist_ok=True)
	return root


def cache_key(prompt: str, model: str) -> str:
	h = hashlib.sha256()
	h.update(model.encode("utf-8"))
	h.update(b"\n--\n")
	h.update(prompt.encode("utf-8"))
	return h.hexdigest()


@dataclass
class CachedNarrator:
	"""Wraps any Narrator with a content-addressed JSON cache."""

	inner: Narrator
	model: str
	root: Path | None = None

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		root = self.root or cache_root()
		prompt = build_user_message(inputs)
		key = cache_key(prompt, self.model)
		path = root / f"{key}.json"
		if path.is_file():
			try:
				data = json.loads(path.read_text(encoding="utf-8"))
				cached = NarrativeOutput(
					what_it_is=data["what_it_is"],
					whats_been_happening=data["whats_been_happening"],
					whats_planned=data["whats_planned"],
				)
				if cached.is_usable():
					return cached
			except (OSError, KeyError, json.JSONDecodeError, TypeError):
				pass
		out = self.inner.narrate(inputs)
		if out is not None and out.is_usable():
			try:
				path.write_text(json.dumps({
					"what_it_is": out.what_it_is,
					"whats_been_happening": out.whats_been_happening,
					"whats_planned": out.whats_planned,
					"model": self.model,
					"generated_at": datetime.now(tz=timezone.utc).isoformat(),
				}, indent=2), encoding="utf-8")
			except OSError:
				pass
		return out

	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None:
		root = self.root or cache_root()
		prompt = build_weekly_message(inputs)
		key = cache_key(prompt, self.model + "::weekly")
		path = root / f"{key}.json"
		if path.is_file():
			try:
				data = json.loads(path.read_text(encoding="utf-8"))
				cached = WeeklyOutput(week_in_review=data["week_in_review"])
				if cached.is_usable():
					return cached
			except (OSError, KeyError, json.JSONDecodeError, TypeError):
				pass
		out = self.inner.narrate_weekly(inputs)
		if out is not None and out.is_usable():
			try:
				path.write_text(json.dumps({
					"week_in_review": out.week_in_review,
					"model": self.model,
					"kind": "weekly",
					"generated_at": datetime.now(tz=timezone.utc).isoformat(),
				}, indent=2), encoding="utf-8")
			except OSError:
				pass
		return out

	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None:
		root = self.root or cache_root()
		prompt = build_dod_message(inputs)
		key = cache_key(prompt, self.model + "::dod")
		path = root / f"{key}.json"
		if path.is_file():
			try:
				data = json.loads(path.read_text(encoding="utf-8"))
				cached = DoDOutput(observation=data["observation"])
				if cached.is_usable():
					return cached
			except (OSError, KeyError, json.JSONDecodeError, TypeError):
				pass
		out = self.inner.narrate_dod(inputs)
		if out is not None and out.is_usable():
			try:
				path.write_text(json.dumps({
					"observation": out.observation,
					"model": self.model,
					"kind": "dod",
					"generated_at": datetime.now(tz=timezone.utc).isoformat(),
				}, indent=2), encoding="utf-8")
			except OSError:
				pass
		return out


# ─── HTTP transport ──────────────────────────────────────────────────────────

def _post_json(
	url: str,
	headers: dict[str, str],
	body: dict,
	timeout: float,
) -> dict | None:
	"""Fail-soft HTTP POST. Returns parsed JSON or None on any error."""
	data = json.dumps(body).encode("utf-8")
	merged = {**headers, "content-type": "application/json"}
	req = urllib.request.Request(url, data=data, headers=merged)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			return json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		try:
			detail = exc.read().decode("utf-8", errors="replace")[:200]
		except Exception:
			detail = ""
		_warn(f"narrator http {exc.code}: {detail}")
		return None
	except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
		_warn(f"narrator transport failed: {exc}")
		return None


def _warn(msg: str) -> None:
	if os.environ.get("PROJECT_COMMANDER_LLM_VERBOSE"):
		print(f"[project-commander] {msg}", file=sys.stderr)


# ─── providers ───────────────────────────────────────────────────────────────

@dataclass
class AnthropicNarrator:
	api_key: str
	model: str = "claude-3-5-haiku-latest"
	timeout: float = _NETWORK_TIMEOUT_SEC
	endpoint: str = "https://api.anthropic.com/v1/messages"

	def _chat(self, system: str, user: str, *, max_tokens: int = 1024) -> str | None:
		body = {
			"model": self.model,
			"max_tokens": max_tokens,
			"system": system,
			"messages": [{"role": "user", "content": user}],
		}
		headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
		payload = _post_json(self.endpoint, headers, body, self.timeout)
		if not payload:
			return None
		try:
			return payload["content"][0]["text"]
		except (KeyError, IndexError, TypeError):
			_warn("anthropic response shape unexpected")
			return None

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		text = self._chat(_SYSTEM_PROMPT, build_user_message(inputs))
		return parse_response(text) if text is not None else None

	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None:
		text = self._chat(_WEEKLY_SYSTEM_PROMPT, build_weekly_message(inputs), max_tokens=512)
		return parse_weekly_response(text) if text is not None else None

	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None:
		text = self._chat(_DOD_SYSTEM_PROMPT, build_dod_message(inputs), max_tokens=512)
		return parse_dod_response(text) if text is not None else None


@dataclass
class OpenAINarrator:
	api_key: str
	model: str = "gpt-4o-mini"
	timeout: float = _NETWORK_TIMEOUT_SEC
	endpoint: str = "https://api.openai.com/v1/chat/completions"

	def _chat(self, system: str, user: str, *, max_tokens: int = 1024) -> str | None:
		body = {
			"model": self.model,
			"messages": [
				{"role": "system", "content": system},
				{"role": "user", "content": user},
			],
			"response_format": {"type": "json_object"},
			"max_tokens": max_tokens,
		}
		headers = {"Authorization": f"Bearer {self.api_key}"}
		payload = _post_json(self.endpoint, headers, body, self.timeout)
		if not payload:
			return None
		try:
			return payload["choices"][0]["message"]["content"]
		except (KeyError, IndexError, TypeError):
			_warn("openai response shape unexpected")
			return None

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		text = self._chat(_SYSTEM_PROMPT, build_user_message(inputs))
		return parse_response(text) if text is not None else None

	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None:
		text = self._chat(_WEEKLY_SYSTEM_PROMPT, build_weekly_message(inputs), max_tokens=512)
		return parse_weekly_response(text) if text is not None else None

	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None:
		text = self._chat(_DOD_SYSTEM_PROMPT, build_dod_message(inputs), max_tokens=512)
		return parse_dod_response(text) if text is not None else None


@dataclass
class OllamaNarrator:
	model: str = "llama3.1"
	host: str = "http://localhost:11434"
	timeout: float = _OLLAMA_TIMEOUT_SEC

	def _chat(self, system: str, user: str, *, max_tokens: int = 1024) -> str | None:
		body = {
			"model": self.model,
			"messages": [
				{"role": "system", "content": system},
				{"role": "user", "content": user},
			],
			"stream": False,
			"format": "json",
			"options": {"num_predict": max_tokens},
		}
		url = f"{self.host.rstrip('/')}/api/chat"
		payload = _post_json(url, {}, body, self.timeout)
		if not payload:
			return None
		try:
			return payload["message"]["content"]
		except (KeyError, TypeError):
			_warn("ollama response shape unexpected")
			return None

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		text = self._chat(_SYSTEM_PROMPT, build_user_message(inputs))
		return parse_response(text) if text is not None else None

	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None:
		text = self._chat(_WEEKLY_SYSTEM_PROMPT, build_weekly_message(inputs), max_tokens=512)
		return parse_weekly_response(text) if text is not None else None

	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None:
		text = self._chat(_DOD_SYSTEM_PROMPT, build_dod_message(inputs), max_tokens=512)
		return parse_dod_response(text) if text is not None else None


@dataclass
class DisabledNarrator:
	"""Always returns None. Caller falls back to deterministic synthesis."""

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		return None

	def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None:
		return None

	def narrate_dod(self, inputs: DoDInputs) -> DoDOutput | None:
		return None


# ─── factory ─────────────────────────────────────────────────────────────────

def make_narrator(
	*,
	provider: str = "auto",
	model: str | None = None,
	cache: bool = True,
	env: dict[str, str] | None = None,
) -> Narrator:
	"""Construct a Narrator from environment + provider preference.

	provider:
	  - "auto"      -> first usable of anthropic / openai / ollama / disabled
	  - "anthropic" -> require ANTHROPIC_API_KEY
	  - "openai"    -> require OPENAI_API_KEY
	  - "ollama"    -> use OLLAMA_HOST or http://localhost:11434
	  - "none"      -> DisabledNarrator
	"""
	env = env if env is not None else dict(os.environ)
	chosen = provider
	if chosen == "auto":
		if env.get("ANTHROPIC_API_KEY"):
			chosen = "anthropic"
		elif env.get("OPENAI_API_KEY"):
			chosen = "openai"
		elif env.get("OLLAMA_HOST") or _ollama_reachable(env.get("OLLAMA_HOST", "http://localhost:11434")):
			chosen = "ollama"
		else:
			chosen = "none"

	model_env = env.get("PROJECT_COMMANDER_LLM_MODEL")
	narrator: Narrator
	used_model: str
	if chosen == "anthropic":
		key = env.get("ANTHROPIC_API_KEY", "")
		if not key:
			return DisabledNarrator()
		used_model = model or model_env or "claude-3-5-haiku-latest"
		narrator = AnthropicNarrator(api_key=key, model=used_model)
	elif chosen == "openai":
		key = env.get("OPENAI_API_KEY", "")
		if not key:
			return DisabledNarrator()
		used_model = model or model_env or "gpt-4o-mini"
		narrator = OpenAINarrator(api_key=key, model=used_model)
	elif chosen == "ollama":
		host = env.get("OLLAMA_HOST", "http://localhost:11434")
		used_model = model or model_env or "llama3.1"
		narrator = OllamaNarrator(host=host, model=used_model)
	else:
		return DisabledNarrator()

	if cache:
		narrator = CachedNarrator(inner=narrator, model=used_model)
	return narrator


def _ollama_reachable(host: str) -> bool:
	try:
		req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
		with urllib.request.urlopen(req, timeout=1.0) as resp:
			return resp.status == 200
	except Exception:
		return False
