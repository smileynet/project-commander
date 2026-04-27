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


class Narrator(Protocol):
	"""LLM-or-equivalent that turns NarrativeInputs into prose, or None."""

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None: ...


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

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		body = {
			"model": self.model,
			"max_tokens": 1024,
			"system": _SYSTEM_PROMPT,
			"messages": [{"role": "user", "content": build_user_message(inputs)}],
		}
		headers = {
			"x-api-key": self.api_key,
			"anthropic-version": "2023-06-01",
		}
		payload = _post_json(self.endpoint, headers, body, self.timeout)
		if not payload:
			return None
		try:
			text = payload["content"][0]["text"]
		except (KeyError, IndexError, TypeError):
			_warn("anthropic response shape unexpected")
			return None
		return parse_response(text)


@dataclass
class OpenAINarrator:
	api_key: str
	model: str = "gpt-4o-mini"
	timeout: float = _NETWORK_TIMEOUT_SEC
	endpoint: str = "https://api.openai.com/v1/chat/completions"

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		body = {
			"model": self.model,
			"messages": [
				{"role": "system", "content": _SYSTEM_PROMPT},
				{"role": "user", "content": build_user_message(inputs)},
			],
			"response_format": {"type": "json_object"},
			"max_tokens": 1024,
		}
		headers = {"Authorization": f"Bearer {self.api_key}"}
		payload = _post_json(self.endpoint, headers, body, self.timeout)
		if not payload:
			return None
		try:
			text = payload["choices"][0]["message"]["content"]
		except (KeyError, IndexError, TypeError):
			_warn("openai response shape unexpected")
			return None
		return parse_response(text)


@dataclass
class OllamaNarrator:
	model: str = "llama3.1"
	host: str = "http://localhost:11434"
	timeout: float = _OLLAMA_TIMEOUT_SEC

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
		body = {
			"model": self.model,
			"messages": [
				{"role": "system", "content": _SYSTEM_PROMPT},
				{"role": "user", "content": build_user_message(inputs)},
			],
			"stream": False,
			"format": "json",
			"options": {"num_predict": 1024},
		}
		url = f"{self.host.rstrip('/')}/api/chat"
		payload = _post_json(url, {}, body, self.timeout)
		if not payload:
			return None
		try:
			text = payload["message"]["content"]
		except (KeyError, TypeError):
			_warn("ollama response shape unexpected")
			return None
		return parse_response(text)


@dataclass
class DisabledNarrator:
	"""Always returns None. Caller falls back to deterministic synthesis."""

	def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None:
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
