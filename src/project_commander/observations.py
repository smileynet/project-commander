"""Combined observation set built from raw `Signal`s.

The aggregator emits a `ProjectReport` with raw signals from every source.
This module collapses those signals into a per-project `Observations` record
that the renderer can read directly. The point is to interpret signals once,
in one place, with named heuristics — instead of scattering ad-hoc reasoning
through the renderer.

User-facing fields:
- `intent` — what the project is for, plus what it's currently doing
- `progress` — a small enum of lifecycle states with a one-line summary

Structured fields (`windows`, `flags`, `evidence`) back the user-facing text
and make it auditable: every line we display can be traced back to a signal.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

from .models import ProjectReport, Signal


# ─── prose cleanup ----------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Catches a `<` that opens a tag whose closing `>` was lost to truncation.
_DANGLING_TAG_RE = re.compile(r"<[a-zA-Z][^>]*$")
_CODE_FENCE_RE = re.compile(r"```[^`]*```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)")
# Match blockquote markers either at line start (multiline) OR after whitespace mid-string,
# so flattened-to-one-line summaries (e.g. `> A > > B > C`) get every marker stripped, not just the first.
_BLOCKQUOTE_RE = re.compile(r"(?:^|\s)>+\s?", re.MULTILINE)
_HEADING_RE = re.compile(r"^\s*#+\s+", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_GH_CALLOUT_RE = re.compile(r"\[!(?:TIP|NOTE|WARNING|IMPORTANT|CAUTION)\]\s*", re.IGNORECASE)
_FRONTMATTER_PREFIX_RE = re.compile(
    r"^\s*(?:date|status|author|title|tags|category|published|updated)\s*[:\-]\s*[^\n]*\n",
    re.IGNORECASE | re.MULTILINE,
)
# Strip a leading `Key:` / `**Key:**` prefix when the key is metadata-flavored
# and substantive prose follows on the same line.
_LEADING_META_PREFIX_RE = re.compile(
    r"^\s*\*?\*?(?:date|status|author|title|tags|category|published|updated|version)\*?\*?\s*[:\-]\s*",
    re.IGNORECASE,
)
_DOC_TAG_RE = re.compile(r"^\s*\[[^\]]+\]\s*")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])(?:\s+(?=[A-Z\"'(\[])|\s*$)")


def clean_doc_prose(text: str) -> str:
    """Strip markdown / HTML chrome from doc prose so it reads as plain text."""
    if not text:
        return ""
    cleaned = _CODE_FENCE_RE.sub(" ", text)
    # HTML tags first \u2014 their `<...>` syntax conflicts with the blockquote `>` strip below.
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _DANGLING_TAG_RE.sub("", cleaned)
    cleaned = _FRONTMATTER_PREFIX_RE.sub("", cleaned)
    cleaned = _HEADING_RE.sub("", cleaned)
    cleaned = _LIST_MARKER_RE.sub("", cleaned)
    cleaned = _GH_CALLOUT_RE.sub("", cleaned)
    cleaned = _BOLD_RE.sub(r"\1", cleaned)
    cleaned = _ITALIC_RE.sub(r"\1", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    # Now that HTML is gone, any remaining `>+` runs are blockquote chrome.
    cleaned = re.sub(r">+", " ", cleaned)
    cleaned = _LEADING_META_PREFIX_RE.sub("", cleaned)
    return " ".join(cleaned.split())


def first_sentence(text: str, *, limit: int) -> str:
    """Return one or more leading sentences \u2264 limit chars; fall back to word-truncate.

    The point: 'truncated at character N' breaks mid-clause and tells the reader
    nothing useful. A sentence boundary guarantees a thought completes. We greedily
    accumulate sentences while they fit in budget, so a short opener like
    'Yes.' or 'Status: experimental.' doesn't strand the rest of the paragraph.
    """
    text = " ".join(text.split())
    if not text:
        return ""
    # Search a window slightly past `limit` so we can complete a sentence that ends just over.
    window = text[: limit + 80]
    last_good_end = 0
    for m in _SENTENCE_END_RE.finditer(window):
        end = m.start() + 1
        candidate = window[:end].strip()
        if len(candidate) > limit:
            break
        last_good_end = end
        # Stop once the running prefix is substantive enough on its own.
        if len(candidate) >= 60:
            return candidate
    if last_good_end and last_good_end <= limit:
        return window[:last_good_end].strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"



# ─── progress state ──────────────────────────────────────────────────────────

class Progress(str, Enum):
    """Where the project is in its lifecycle, observed from signals."""

    HOT = "hot"            # touched today, prompts+commits both present
    ACTIVE = "active"      # touched within 7 days
    PAUSED = "paused"      # 7–30 days, dirty tree or unresolved prompts
    COOLING = "cooling"    # 7–30 days, clean, low cadence
    IDLE = "idle"          # 30–90 days
    DORMANT = "dormant"    # 90+ days
    SHIPPED = "shipped"    # recent commits, plan says complete, no fresh prompts
    DRIFTING = "drifting"  # plan says complete BUT commits continue past it
    TRACKING = "tracking"  # only upstream-style activity (sync commits, no prompts)
    STUB = "stub"          # docs only, no commits or prompts
    EMPTY = "empty"        # nothing observed


_PROGRESS_LABELS: dict[Progress, str] = {
    Progress.HOT: "Hot",
    Progress.ACTIVE: "Active",
    Progress.PAUSED: "Paused",
    Progress.COOLING: "Cooling",
    Progress.IDLE: "Idle",
    Progress.DORMANT: "Dormant",
    Progress.SHIPPED: "Shipped",
    Progress.DRIFTING: "Drifting",
    Progress.TRACKING: "Tracking",
    Progress.STUB: "Stub",
    Progress.EMPTY: "Empty",
}


def progress_label(p: Progress) -> str:
    return _PROGRESS_LABELS[p]


# ─── activity windows ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActivityWindow:
    """Signal counts inside a sliding window ending now."""

    days: int
    commits: int
    prompts: int
    sessions: int
    distinct_days: int

    @property
    def total(self) -> int:
        return self.commits + self.prompts + self.sessions


# \u2500\u2500\u2500 outstanding work --------------------------------------------------------

@dataclass(frozen=True)
class Outstanding:
    """Forward-looking work the project still has on its plate."""

    git_uncommitted_count: int = 0
    git_ahead: int = 0
    git_behind: int = 0
    git_examples: tuple[str, ...] = ()       # first few uncommitted entries
    git_upstream: str | None = None
    plan_open_count: int = 0                 # unchecked checkboxes across plan docs
    plan_open_phases: int = 0                # phases not yet marked complete
    plan_next: str = ""                      # first unchecked item / current phase
    plan_doc_ref: str = ""                   # the doc that produced plan_next
    orphaned_thread_age_hours: int | None = None  # latest substantive prompt with no follow-up commit

    @property
    def is_empty(self) -> bool:
        return (
            self.git_uncommitted_count == 0
            and self.git_ahead == 0
            and self.git_behind == 0
            and self.plan_open_count == 0
            and self.plan_open_phases == 0
            and self.orphaned_thread_age_hours is None
        )

    @property
    def headline(self) -> str:
        """One-token summary suitable for a narrow column."""
        if self.git_uncommitted_count > 0:
            return f"dirty {self.git_uncommitted_count}"
        if self.git_ahead > 0:
            return f"ahead {self.git_ahead}"
        if self.git_behind > 0:
            return f"behind {self.git_behind}"
        if self.plan_open_count > 0:
            return f"plan {self.plan_open_count}"
        if self.plan_open_phases > 0:
            return f"phase {self.plan_open_phases}"
        if self.orphaned_thread_age_hours is not None:
            return "orphan"
        return "\u2014"


# ─── observation record ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observations:
    """Synthesized, user-facing view of a single project."""

    purpose: str = ""           # the project's stated identity (from doc)
    focus: str = ""             # what's being asked of agents right now
    intent: str = ""            # composed user-facing intent statement
    workstream: str = ""        # synthesized current line of work
    open_issue: str = ""        # unresolved issue / decision the user should care about
    why_stopped: str = ""       # why work paused or why attention is needed now
    recent_changes: str = ""    # what landed recently, condensed from commit subjects
    attention: str = ""         # concise combined reason this project needs review now
    progress: Progress = Progress.EMPTY
    progress_summary: str = ""  # human one-liner for the progress state
    last_action: str = ""       # what the project last accomplished
    last_action_at: datetime | None = None
    last_action_source: str = ""
    window_7d: ActivityWindow = field(default_factory=lambda: ActivityWindow(7, 0, 0, 0, 0))
    window_30d: ActivityWindow = field(default_factory=lambda: ActivityWindow(30, 0, 0, 0, 0))
    window_90d: ActivityWindow = field(default_factory=lambda: ActivityWindow(90, 0, 0, 0, 0))
    flags: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    outstanding: Outstanding = field(default_factory=Outstanding)
    next_action: str = ""       # synthesized one-line next step


# ─── builders ────────────────────────────────────────────────────────────────

# Doc names ranked by authority for planning/state interpretation.
_DOC_AUTHORITY = {
    "PLAN.md": 100,
    "README.md": 90,
    "ROADMAP.md": 85,
    "NEXT_STEPS.md": 80,
    "IMPROVEMENTS.md": 75,
    "AGENTS.md": 70,
    "AGENT.md": 70,
    "CLAUDE.md": 65,
    "GEMINI.md": 65,
    "TODO.md": 60,
}

# Doc names ranked by authority for the project's *stated* identity.
# Planning docs stay in the set as a fallback, but README/ROADMAP-style docs win when present
# so the identity line is not hijacked by the current plan.
_PURPOSE_DOC_AUTHORITY = {
    "README.md": 100,
    "ROADMAP.md": 90,
    "IMPROVEMENTS.md": 80,
    "AGENTS.md": 70,
    "AGENT.md": 70,
    "CLAUDE.md": 65,
    "GEMINI.md": 65,
    "PLAN.md": 40,
    "NEXT_STEPS.md": 35,
    "TODO.md": 30,
}

# Phrases that mean "the plan says we're done." If we see these AND commits/
# prompts after the doc's mtime, we surface plan-drift.
_DONE_PHRASES = re.compile(
    r"\b(?:status\s*[:\-]\s*completed?|completed?|done|shipped|finished|stable|archived)\b",
    re.IGNORECASE,
 )

# Low-information prompt bodies: indicate the user is iterating via brief
# approvals rather than typing fresh intent.
_PROCEDURAL_RE = re.compile(
    r"^(?:proceed|continue|yes|y|ok|okay|go|next|run it|do it|please continue|retry|try again|fix it)\.?\s*$",
    re.IGNORECASE,
)

def is_procedural(text: str) -> bool:
    """True if a prompt body is a one-word approval (yes/proceed/...)."""
    return bool(_PROCEDURAL_RE.match(text.strip()))

# Signals of active work: dirty tree, in-flight tasks, fresh prompts.
_ACTIVE_PROMPT_LIMIT = 220
_FOCUS_LIMIT = 180

# Prompt-injection markers we may want to surface as a security flag.
_INJECTION_RE = re.compile(
    r"\b(?:system\s*prompt|first\s*\d+\s*char(?:acter)?s?|reply\s*with\s*only|ignore\s+(?:all\s+)?previous)\b",
    re.IGNORECASE,
)


_WORKSTREAM_DOC_DAYS = 14
_RECENT_CHANGES_DAYS = 7
_COMMIT_TAG_RE = re.compile(r"^\[[^\]]+\]\s*")
_CONVENTIONAL_PREFIX_RE = re.compile(
    r"^(?:feat|fix|docs|chore|refactor|test|perf|build|ci|style)(?:\([^)]+\))?!?:\s*",
    re.IGNORECASE,
 )
_LEADING_VERB_RE = re.compile(
    r"^(?:add|collect|fix|update|improve|rewrite|disable|create|implement|review|switch|use|wire|split|extract|rename|clean|stabilize)\s+",
    re.IGNORECASE,
 )
_TOPIC_STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "into", "of", "on", "or", "the", "to",
    "with", "without", "using", "via", "after", "before", "across", "through", "this", "that",
    "these", "those", "more", "additional", "still", "again",
}
_SUMMARY_PREFIX_RE = re.compile(r"^\s*(?:quick summary|summary)\s*:\s*", re.IGNORECASE)
_LOW_SIGNAL_COMMIT_RES = (
    re.compile(r"^@[A-Za-z0-9_-]+ has signed the CLA\b", re.IGNORECASE),
    re.compile(r"^merge (?:branch|pull request)\b", re.IGNORECASE),
    re.compile(r"^bump\b.*\bversion\b", re.IGNORECASE),
    re.compile(r"^release v?\d", re.IGNORECASE),
)


def build(report: ProjectReport, *, now: datetime | None = None) -> Observations:
    """Compute observations from a `ProjectReport`."""
    now = now or datetime.now(tz=timezone.utc)
    sigs = report.signals

    # No signals at all → empty record.
    if not sigs and not report.is_git_repo:
        return Observations(
            intent="(no signals)",
            progress=Progress.EMPTY,
            progress_summary="No git, no agent history, no plan docs were observed for this folder.",
        )

    purpose, purpose_evidence = _extract_purpose(sigs)
    focus, focus_kind = _extract_focus(sigs, now=now)
    last_action, last_action_at, last_action_source = _last_concrete_action(sigs)

    w7 = _window(sigs, now=now, days=7)
    w30 = _window(sigs, now=now, days=30)
    w90 = _window(sigs, now=now, days=90)

    drift_info = _detect_plan_drift(sigs)
    drift_count = drift_info[0] if drift_info else None
    drift_doc = drift_info[1] if drift_info else None
    flags = _flags(report, sigs, w30=w30, drift=drift_count, focus_kind=focus_kind, focus=focus)

    progress = _classify_progress(report, sigs, w7=w7, w30=w30, w90=w90, drift=drift_count,
                                  has_purpose=bool(purpose), focus_kind=focus_kind)
    progress_summary = _progress_summary(report, progress, w7=w7, w30=w30, w90=w90,
                                         last_action_at=last_action_at, drift=drift_count)

    intent = _compose_intent(purpose, focus, focus_kind, last_action, progress)
    outstanding = _build_outstanding(report, sigs, now=now)
    recent_changes = _recent_changes_summary(sigs, now=now)
    workstream = _workstream_summary(
        sigs, now=now, purpose=purpose, focus=focus, focus_kind=focus_kind,
        last_action=last_action, recent_changes=recent_changes,
    )
    open_issue = _open_issue_summary(report, outstanding=outstanding,
                                     drift_count=drift_count, drift_doc=drift_doc)
    why_stopped = _why_stopped_summary(report, outstanding=outstanding,
                                       drift_count=drift_count, drift_doc=drift_doc)
    attention = _attention_summary(open_issue=open_issue, why_stopped=why_stopped)
    next_action = _next_action(report, progress, outstanding=outstanding,
                               drift_count=drift_count, drift_doc=drift_doc,
                               focus_kind=focus_kind)

    evidence: list[str] = []
    if purpose_evidence:
        evidence.append(purpose_evidence)
    if focus and focus_kind == "prompt":
        _d = _days_since(now, _latest(sigs, 'prompt'))
        evidence.append(f"recent prompt within {_d if _d is not None else '?'}d")
    if last_action_source:
        evidence.append(f"last action: {last_action_source}")
    if drift_count is not None:
        evidence.append(f"plan-drift: doc says complete, {drift_count} commits since")

    return Observations(
        purpose=purpose,
        focus=focus,
        intent=intent,
        workstream=workstream,
        open_issue=open_issue,
        why_stopped=why_stopped,
        recent_changes=recent_changes,
        attention=attention,
        progress=progress,
        progress_summary=progress_summary,
        last_action=last_action,
        last_action_at=last_action_at,
        last_action_source=last_action_source,
        window_7d=w7, window_30d=w30, window_90d=w90,
        flags=tuple(flags),
        evidence=tuple(evidence),
        outstanding=outstanding,
        next_action=next_action,
    )


# ─── helpers: extraction ─────────────────────────────────────────────────────

def _extract_purpose(sigs: Iterable[Signal]) -> tuple[str, str]:
    docs = [s for s in sigs if s.kind == "doc"]
    if not docs:
        return "", ""

    def score(s: Signal) -> tuple[int, datetime]:
        name = s.ref.rsplit("/", 1)[-1]
        return _PURPOSE_DOC_AUTHORITY.get(name, 0), s.timestamp

    best = max(docs, key=score)
    # `summary` is `[<rel-path>] <prose>` — strip the bracketed prefix.
    text = best.summary
    if text.startswith("["):
        end = text.find("] ")
        if end != -1:
            text = text[end + 2 :]
    text = clean_doc_prose(text)
    text = _SUMMARY_PREFIX_RE.sub("", text)
    return text, f"doc:{best.ref}"


def _extract_focus(sigs: Iterable[Signal], *, now: datetime) -> tuple[str, str]:
    """Return (focus_text, kind). kind ∈ {"prompt","procedural","",}.

    Pick the most recent substantive (non-procedural) prompt within 30 days.
    If the only recent prompts are procedural ("proceed", "yes"), return them
    flagged so the renderer can say "iterating with brief approvals."
    """
    cutoff = now - timedelta(days=30)
    recent = sorted(
        (s for s in sigs if s.kind == "prompt" and s.timestamp >= cutoff),
        key=lambda s: s.timestamp, reverse=True,
    )
    if not recent:
        return "", ""
    for s in recent:
        if not _PROCEDURAL_RE.match(s.summary.strip()):
            return _trim(s.summary, _FOCUS_LIMIT), "prompt"
    # everything was procedural
    return _trim(recent[0].summary, _FOCUS_LIMIT), "procedural"


def _last_concrete_action(sigs: Iterable[Signal]) -> tuple[str, datetime | None, str]:
    """The most recent thing that *happened* (commit > session > doc-edit)."""
    candidates = [s for s in sigs if s.kind in ("commit", "session", "doc")]
    if not candidates:
        return "", None, ""
    best = max(candidates, key=lambda s: s.timestamp)
    if best.kind == "doc":
        # For doc edits the *event* is the file change; the file's prose lives
        # in the dedicated docs section. Reporting it here as last_action would
        # duplicate a long quote into the metadata block.
        ref = best.ref or "plan doc"
        summary = f"edited {ref}"
    else:
        summary = _trim(best.summary, 180)
    return summary, best.timestamp, f"{best.source}:{best.kind}"


def _planning_doc_score(ref: str) -> int:
    name = (ref or "").lower()
    base = name.rsplit("/", 1)[-1]
    if "/plans/" in name or "plan" in base:
        return 100
    if "next_steps" in base or "next-steps" in base or base == "next.md" or "next" in base:
        return 90
    if "roadmap" in base:
        return 85
    if "todo" in base:
        return 80
    if "improvement" in base:
        return 78
    return 0


def _split_doc_signal(s: Signal) -> tuple[str, str]:
    ref = s.ref or "doc"
    body = s.summary
    if body.startswith("["):
        end = body.find("] ")
        if end != -1:
            body = body[end + 2 :]
    return ref, body


def _planning_doc_summary(sigs: Iterable[Signal], *, now: datetime) -> str:
    cutoff = now - timedelta(days=_WORKSTREAM_DOC_DAYS)
    docs: list[tuple[int, datetime, str]] = []
    for s in sigs:
        if s.kind != "doc" or s.timestamp < cutoff:
            continue
        score = _planning_doc_score(s.ref or "")
        if score == 0:
            continue
        _, rest = _split_doc_signal(s)
        clean = clean_doc_prose(rest) if rest else ""
        clean = _SUMMARY_PREFIX_RE.sub("", clean)
        summary = first_sentence(clean, limit=180) if clean else ""
        if summary:
            docs.append((score, s.timestamp, summary))
    if not docs:
        return ""
    return max(docs, key=lambda item: (item[0], item[1]))[2]


def _commit_topic_fragment(summary: str) -> str:
    text = _COMMIT_TAG_RE.sub("", summary).strip()
    text = _CONVENTIONAL_PREFIX_RE.sub("", text).strip()
    text = _LEADING_VERB_RE.sub("", text).strip()
    text = text.lstrip(":- ").strip()
    return _trim(text, 80)


def _topic_tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _TOPIC_STOPWORDS
    }


def _similar_topic(a: str, b: str) -> bool:
    if a == b or a in b or b in a:
        return True
    ta = _topic_tokens(a)
    tb = _topic_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    return overlap >= min(len(ta), len(tb)) and overlap > 0


def _join_phrases(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _is_low_signal_commit(summary: str) -> bool:
    return any(rx.search(summary.strip()) for rx in _LOW_SIGNAL_COMMIT_RES)


def _recent_changes_summary(sigs: Iterable[Signal], *, now: datetime) -> str:
    cutoff = now - timedelta(days=_RECENT_CHANGES_DAYS)
    commits = sorted(
        (s for s in sigs if s.kind == "commit" and s.timestamp >= cutoff),
        key=lambda s: s.timestamp, reverse=True,
    )
    fragments: list[str] = []
    for commit in commits:
        if _is_low_signal_commit(commit.summary):
            continue
        fragment = _commit_topic_fragment(commit.summary)
        if not fragment or any(_similar_topic(fragment, existing) for existing in fragments):
            continue
        fragments.append(fragment)
        if len(fragments) == 3:
            break
    if not fragments:
        return ""
    if len(fragments) == 1:
        return f"Recent commit: {fragments[0]}."
    return f"Recent commits focused on {_join_phrases(fragments)}."


def _workstream_summary(sigs: Iterable[Signal], *, now: datetime, purpose: str, focus: str,
                        focus_kind: str, last_action: str, recent_changes: str) -> str:
    plan_summary = _planning_doc_summary(sigs, now=now)
    if plan_summary:
        return plan_summary
    if focus and focus_kind == "prompt":
        return _trim(focus.rstrip("."), 180) + "."
    if recent_changes:
        return recent_changes
    if purpose:
        return first_sentence(purpose, limit=180)
    if last_action:
        return f"Latest concrete action: {last_action}."
    return ""


def _open_issue_summary(report: ProjectReport, *, outstanding: Outstanding,
                        drift_count: int | None, drift_doc: str | None) -> str:
    o = outstanding
    if drift_count is not None:
        ref = drift_doc or o.plan_doc_ref or "plan doc"
        return f"{ref} no longer matches the code that landed after it was marked complete."
    if o.plan_next:
        return _trim(o.plan_next.rstrip("."), 140) + "."
    if o.git_ahead > 0 and o.git_upstream:
        return f"{o.git_ahead} local commit(s) still need to reach {o.git_upstream}."
    if o.git_ahead > 0:
        return f"{o.git_ahead} local commit(s) still need to be pushed."
    if o.git_behind > 0 and o.git_upstream:
        return f"Local HEAD is behind {o.git_upstream} by {o.git_behind} commit(s)."
    if o.git_behind > 0:
        return f"Local HEAD is behind upstream by {o.git_behind} commit(s)."
    if not report.is_git_repo and report.signals:
        return "Activity exists here, but there is no git history boundary yet."
    return ""


def _why_stopped_summary(report: ProjectReport, *, outstanding: Outstanding,
                         drift_count: int | None, drift_doc: str | None) -> str:
    o = outstanding
    if drift_count is not None:
        ref = drift_doc or o.plan_doc_ref or "plan doc"
        return f"The documented completion state in {ref} and the actual code history diverged."
    reasons: list[str] = []
    if o.git_uncommitted_count > 0:
        reasons.append("work is still only in the working tree")
    if o.orphaned_thread_age_hours is not None:
        reasons.append(f"the last substantive prompt is {o.orphaned_thread_age_hours}h old with no follow-up commit")
    if reasons:
        sentence = reasons[0] if len(reasons) == 1 else f"{reasons[0]}, and {reasons[1]}"
        return sentence[:1].upper() + sentence[1:] + "."
    if o.git_ahead > 0:
        return "The work landed locally, but the branch was not pushed upstream."
    if o.git_behind > 0:
        return "Upstream moved ahead before local review picked the work back up."
    if not report.is_git_repo and report.signals:
        return "The folder has activity history but no git repo, so the state is harder to recover safely."
    return ""


def _attention_summary(*, open_issue: str, why_stopped: str) -> str:
    parts = [part.strip().rstrip(".") for part in (open_issue, why_stopped) if part.strip()]
    if not parts:
        return ""
    return ". ".join(parts) + "."


def _window(sigs: Iterable[Signal], *, now: datetime, days: int) -> ActivityWindow:
    cutoff = now - timedelta(days=days)
    commits = prompts = sessions = 0
    days_seen: set[str] = set()
    for s in sigs:
        if s.timestamp < cutoff:
            continue
        if s.kind == "commit":
            commits += 1
        elif s.kind == "prompt":
            prompts += 1
        elif s.kind == "session":
            sessions += 1
        days_seen.add(s.timestamp.date().isoformat())
    return ActivityWindow(days=days, commits=commits, prompts=prompts,
                          sessions=sessions, distinct_days=len(days_seen))


def _detect_plan_drift(sigs: list[Signal]) -> tuple[int, str] | None:
    """If the highest-authority doc declares completion AND commits exist after
    the doc's mtime, return `(post_doc_commit_count, doc_ref)`. Else None.
    """
    docs = [s for s in sigs if s.kind == "doc"]
    if not docs:
        return None
    best = max(docs, key=lambda s: (_DOC_AUTHORITY.get(s.ref.rsplit("/", 1)[-1], 0), s.timestamp))
    if not _DONE_PHRASES.search(best.summary):
        return None
    later = [s for s in sigs if s.kind == "commit" and s.timestamp > best.timestamp]
    if not later:
        return None
    return (len(later), best.ref or "plan doc")


def _flags(report: ProjectReport, sigs: list[Signal], *,
           w30: ActivityWindow, drift: int | None,
           focus_kind: str, focus: str) -> list[str]:
    flags: list[str] = []
    if report.git_dirty:
        flags.append("dirty-tree")
    if drift:
        flags.append("plan-drift")
    sources = {s.source for s in sigs}
    if len(sources & {"claude", "gemini", "omp", "opencode", "kiro"}) >= 4:
        flags.append("tool-cluster")
    if not any(s.kind == "doc" for s in sigs):
        flags.append("no-docs")
    if focus_kind == "procedural":
        flags.append("procedural-prompts")
    if any(s.kind == "prompt" and _INJECTION_RE.search(s.summary) for s in sigs):
        flags.append("prompt-injection-detected")
    # tracking-upstream heuristic: only commits, no prompts, on a non-default branch
    if w30.prompts == 0 and w30.commits > 0 and (report.git_branch or "").lower() not in ("main", "master", ""):
        flags.append("upstream-only")
    return flags


# ─── helpers: classification ─────────────────────────────────────────────────

def _classify_progress(report: ProjectReport, sigs: list[Signal], *,
                       w7: ActivityWindow, w30: ActivityWindow, w90: ActivityWindow,
                       drift: int | None,
                       has_purpose: bool, focus_kind: str) -> Progress:
    last = report.last_active
    if last is None:
        # only docs, or nothing
        if any(s.kind == "doc" for s in sigs):
            return Progress.STUB
        return Progress.EMPTY

    now = datetime.now(tz=timezone.utc)
    age = (now - last).days

    if drift:
        return Progress.DRIFTING

    if w7.commits > 0 and w7.prompts > 0:
        # touched in the last day with prompts and commits both
        if (now - last) <= timedelta(hours=24):
            return Progress.HOT
        return Progress.ACTIVE
    if age <= 7:
        return Progress.ACTIVE
    if age <= 30:
        if report.git_dirty or focus_kind in ("prompt", "procedural"):
            return Progress.PAUSED
        if w30.commits >= 3 and w30.prompts == 0 and not has_purpose:
            return Progress.SHIPPED
        return Progress.COOLING
    if age <= 90:
        return Progress.IDLE
    # > 90 days
    if w90.prompts == 0 and w90.commits > 0:
        return Progress.TRACKING
    return Progress.DORMANT


def _progress_summary(report: ProjectReport, progress: Progress, *,
                      w7: ActivityWindow, w30: ActivityWindow, w90: ActivityWindow,
                      last_action_at: datetime | None,
                      drift: int | None) -> str:
    last = report.last_active
    age = _humanize_delta(datetime.now(tz=timezone.utc) - last) if last else "—"
    base = {
        Progress.HOT: f"Touched today across {w7.distinct_days} day(s); "
                      f"{w7.commits} commit(s), {w7.prompts} prompt(s) this week.",
        Progress.ACTIVE: f"Touched {age}; {w7.commits} commit(s), {w7.prompts} prompt(s) in the last 7 days.",
        Progress.PAUSED: f"Last touched {age}; mid-flight ({_paused_reason(report, w30)}).",
        Progress.COOLING: f"Last touched {age}; no fresh prompts in 30 days.",
        Progress.IDLE: f"Idle; last touched {age}.",
        Progress.DORMANT: f"Dormant; last touched {age}.",
        Progress.SHIPPED: f"Last commit {age}; plan complete and clean tree.",
        Progress.DRIFTING: (f"Plan doc declares complete, but {drift} commit(s) "
                            f"have landed since — review intent."),
        Progress.TRACKING: f"Only upstream-style commits; last {age}.",
        Progress.STUB: "Documentation only — no commits or agent prompts observed.",
        Progress.EMPTY: "No signals observed for this folder.",
    }
    return base.get(progress, "")


def _paused_reason(report: ProjectReport, w30: ActivityWindow) -> str:
    parts = []
    if report.git_dirty:
        parts.append("uncommitted changes")
    if w30.prompts:
        parts.append(f"{w30.prompts} prompt(s) in 30d")
    return ", ".join(parts) if parts else "in-flight"


# ─── helpers: composition ────────────────────────────────────────────────────

def _compose_intent(purpose: str, focus: str, focus_kind: str,
                    last_action: str, progress: Progress) -> str:
    """Combine the available pieces into a single user-facing intent line."""
    if purpose and focus and focus_kind == "prompt":
        return f"{purpose} Currently: {focus}"
    if purpose and focus_kind == "procedural":
        return f"{purpose} Currently iterating with brief approvals."
    if purpose:
        return purpose
    if focus_kind == "prompt":
        return f"[active] {focus}"
    if focus_kind == "procedural":
        return "[active] iterating with brief approvals."
    if last_action:
        return f"[recent action] {last_action}"
    return "(no observations)"


# ─── small utilities ─────────────────────────────────────────────────────────

# \u2500\u2500\u2500 helpers: outstanding + next-action ---------------------------------------

# Minimum hours since the latest substantive prompt before we call its thread
# 'orphaned' \u2014 below this we're probably mid-conversation.
_ORPHAN_MIN_HOURS = 4
_ORPHAN_MAX_DAYS = 7


def _build_outstanding(report: ProjectReport, sigs: list[Signal], *, now: datetime) -> Outstanding:
    """Pull the forward-looking work signals into one struct."""
    # Plan-doc aggregation
    plan_open_count = 0
    plan_open_phases = 0
    plan_next = ""
    plan_doc_ref = ""
    if report.plan_summaries:
        # Sort doc refs by authority so the highest-priority next-item wins.
        ranked = sorted(
            report.plan_summaries.items(),
            key=lambda kv: -_DOC_AUTHORITY.get(kv[0], 0),
        )
        for path, summary in ranked:
            plan_open_count += summary.open_items
            plan_open_phases += max(0, summary.total_phases - summary.complete_phases)
            if not plan_next and summary.next_item:
                plan_next = summary.next_item
                plan_doc_ref = path
        if not plan_next and plan_open_phases > 0:
            # Fall back to surfacing the first incomplete phase by authority.
            for path, summary in ranked:
                if summary.total_phases > summary.complete_phases:
                    plan_next = f"phase {summary.complete_phases + 1} of {summary.total_phases}"
                    plan_doc_ref = path
                    break

    # Orphaned-thread detection: latest substantive prompt with no later commit.
    orphan_hours: int | None = None
    substantive = [s for s in sigs
                   if s.kind == "prompt" and not _PROCEDURAL_RE.match(s.summary.strip())]
    if substantive:
        latest = max(substantive, key=lambda s: s.timestamp)
        delta = now - latest.timestamp
        if (timedelta(hours=_ORPHAN_MIN_HOURS) <= delta <= timedelta(days=_ORPHAN_MAX_DAYS)):
            commits_after = any(s.kind == "commit" and s.timestamp > latest.timestamp for s in sigs)
            if not commits_after:
                orphan_hours = int(delta.total_seconds() / 3600)

    return Outstanding(
        git_uncommitted_count=len(report.git_uncommitted),
        git_ahead=report.git_ahead,
        git_behind=report.git_behind,
        git_examples=tuple(report.git_uncommitted[:3]),
        git_upstream=report.git_upstream,
        plan_open_count=plan_open_count,
        plan_open_phases=plan_open_phases,
        plan_next=plan_next,
        plan_doc_ref=plan_doc_ref,
        orphaned_thread_age_hours=orphan_hours,
    )


def _next_action(report: ProjectReport, progress: Progress, *,
                 outstanding: Outstanding,
                 drift_count: int | None, drift_doc: str | None,
                 focus_kind: str) -> str:
    """Synthesize a one-line 'what to do next' from progress + outstanding work."""
    o = outstanding
    # Drifting trumps everything: the plan needs reconciliation first.
    if drift_count is not None:
        ref = drift_doc or o.plan_doc_ref or "the plan doc"
        return (f"Reconcile {ref}: it says complete but "
                f"{drift_count} commit(s) have landed since. Update or remove the completion marker.")
    if not report.is_git_repo and progress is not Progress.EMPTY:
        return "Run `project-commander tidy` to init this folder as a git repo."
    if o.plan_next and o.git_uncommitted_count > 0:
        pieces = [
            f"resolve the next plan item: {_trim(o.plan_next, 100)}",
            f"commit {o.git_uncommitted_count} uncommitted file(s)",
        ]
        return ". Then ".join([p[0].upper() + p[1:] for p in pieces]) + "."
    pieces: list[str] = []
    if o.git_uncommitted_count > 0:
        pieces.append(f"commit {o.git_uncommitted_count} uncommitted file(s)")
    if o.git_ahead > 0 and o.git_upstream:
        pieces.append(f"push {o.git_ahead} commit(s) to {o.git_upstream}")
    elif o.git_ahead > 0:
        pieces.append(f"push {o.git_ahead} unpushed commit(s)")
    if o.git_behind > 0 and o.git_upstream:
        pieces.append(f"pull {o.git_behind} commit(s) from {o.git_upstream}")
    if not pieces:
        if o.orphaned_thread_age_hours is not None:
            return ("Resume the last prompt thread or commit progress "
                    f"({o.orphaned_thread_age_hours}h since last prompt, no follow-up commit).")
        if o.plan_next and progress in (Progress.HOT, Progress.ACTIVE):
            ref = f" ({o.plan_doc_ref})" if o.plan_doc_ref else ""
            return f"Pick up the next plan item{ref}: {_trim(o.plan_next, 100)}."
        if progress is Progress.PAUSED:
            return "Decide whether to resume or stash this project."
        if progress is Progress.SHIPPED:
            return "No action — shipped and clean."
        if progress is Progress.STUB:
            return "Either start work or remove this folder."
        if progress is Progress.EMPTY:
            return "Either populate this folder or remove it."
        if progress in (Progress.IDLE, Progress.DORMANT):
            return "No active work — archive or revisit."
        return ""
    return ". Then ".join([p[0].upper() + p[1:] for p in pieces]) + "."



def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _latest(sigs: Iterable[Signal], kind: str) -> datetime | None:
    matches = [s.timestamp for s in sigs if s.kind == kind]
    return max(matches) if matches else None


def _days_since(now: datetime, ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return max(0, (now - ts).days)


def _humanize_delta(delta: timedelta) -> str:
    days = delta.days
    if days < 0:
        return "in the future"
    if days == 0:
        hours = delta.seconds // 3600
        if hours == 0:
            return f"{delta.seconds // 60}m ago"
        return f"{hours}h ago"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 7}w ago"
    return f"{days // 365}y ago"
