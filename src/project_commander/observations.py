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


# ─── observation record ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observations:
    """Synthesized, user-facing view of a single project."""

    purpose: str = ""           # the project's stated identity (from doc)
    focus: str = ""             # what's being asked of agents right now
    intent: str = ""            # composed user-facing intent statement
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


# ─── builders ────────────────────────────────────────────────────────────────

# Doc names ranked by authority for the project's *stated* identity.
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

# Signals of active work: dirty tree, in-flight tasks, fresh prompts.
_ACTIVE_PROMPT_LIMIT = 220
_FOCUS_LIMIT = 180

# Prompt-injection markers we may want to surface as a security flag.
_INJECTION_RE = re.compile(
    r"\b(?:system\s*prompt|first\s*\d+\s*char(?:acter)?s?|reply\s*with\s*only|ignore\s+(?:all\s+)?previous)\b",
    re.IGNORECASE,
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

    drift = _detect_plan_drift(sigs)
    flags = _flags(report, sigs, w30=w30, drift=drift, focus_kind=focus_kind, focus=focus)

    progress = _classify_progress(report, sigs, w7=w7, w30=w30, w90=w90, drift=drift,
                                  has_purpose=bool(purpose), focus_kind=focus_kind)
    progress_summary = _progress_summary(report, progress, w7=w7, w30=w30, w90=w90,
                                         last_action_at=last_action_at, drift=drift)

    intent = _compose_intent(purpose, focus, focus_kind, last_action, progress)

    evidence: list[str] = []
    if purpose_evidence:
        evidence.append(purpose_evidence)
    if focus and focus_kind == "prompt":
        _d = _days_since(now, _latest(sigs, 'prompt'))
        evidence.append(f"recent prompt within {_d if _d is not None else '?'}d")
    if last_action_source:
        evidence.append(f"last action: {last_action_source}")
    if drift is not None:
        evidence.append(f"plan-drift: doc says complete, {drift} commits since")

    return Observations(
        purpose=purpose,
        focus=focus,
        intent=intent,
        progress=progress,
        progress_summary=progress_summary,
        last_action=last_action,
        last_action_at=last_action_at,
        last_action_source=last_action_source,
        window_7d=w7, window_30d=w30, window_90d=w90,
        flags=tuple(flags),
        evidence=tuple(evidence),
    )


# ─── helpers: extraction ─────────────────────────────────────────────────────

def _extract_purpose(sigs: Iterable[Signal]) -> tuple[str, str]:
    docs = [s for s in sigs if s.kind == "doc"]
    if not docs:
        return "", ""

    def score(s: Signal) -> tuple[int, datetime]:
        name = s.ref.rsplit("/", 1)[-1]
        return _DOC_AUTHORITY.get(name, 0), s.timestamp

    best = max(docs, key=score)
    # `summary` is `[<rel-path>] <prose>` — strip the bracketed prefix for prose.
    text = best.summary
    if text.startswith("["):
        end = text.find("] ")
        if end != -1:
            text = text[end + 2 :]
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
    return _trim(best.summary, 180), best.timestamp, f"{best.source}:{best.kind}"


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


def _detect_plan_drift(sigs: list[Signal]) -> int | None:
    """If the highest-authority doc declares completion AND commits exist after
    the doc's mtime, return the number of post-doc commits. Else None.
    """
    docs = [s for s in sigs if s.kind == "doc"]
    if not docs:
        return None
    best = max(docs, key=lambda s: (_DOC_AUTHORITY.get(s.ref.rsplit("/", 1)[-1], 0), s.timestamp))
    if not _DONE_PHRASES.search(best.summary):
        return None
    later = [s for s in sigs if s.kind == "commit" and s.timestamp > best.timestamp]
    return len(later) if later else None


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
    age = _humanize_delta(datetime.now(tz=timezone.utc) - last) if last else "\u2014"
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
                            f"have landed since \u2014 review intent."),
        Progress.TRACKING: f"Only upstream-style commits; last {age}.",
        Progress.STUB: "Documentation only \u2014 no commits or agent prompts observed.",
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

def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


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
