"""Core data shapes shared by scanners and the aggregator.

A `Signal` is one observation about a project from a single source. Sources
emit `Signal` objects keyed by absolute project path. The aggregator collapses
signals into a `ProjectReport`.

Design intent:
- A signal carries enough to render the report without going back to disk.
- `kind` distinguishes commit / prompt / doc so the renderer can pick a
  representative slice per project.
- Timestamps are timezone-aware UTC. Naive datetimes are rejected at the
  aggregator boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

SignalKind = Literal["commit", "prompt", "doc", "session", "filesystem"]
Source = Literal["git", "claude", "gemini", "omp", "opencode", "kiro", "docs", "fs"]


@dataclass(frozen=True)
class PlanDocSummary:
    """Structured view of a plan/todo document."""

    path: str                  # rel-path inside the project (e.g. 'PLAN.md')
    total_items: int = 0       # count of `- [ ]` / `- [x]` checkbox items
    open_items: int = 0        # unchecked checkboxes
    total_phases: int = 0      # roman-numeral or 'Phase N' headings
    complete_phases: int = 0   # phase headings marked complete
    next_item: str = ""        # first unchecked checkbox text


@dataclass(frozen=True)
class Signal:
    """A single observation about a project."""

    source: Source
    kind: SignalKind
    timestamp: datetime
    summary: str
    ref: str = ""  # path / commit sha / session id — implementation-dependent

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(f"Signal timestamp must be timezone-aware: {self!r}")
        # normalize to UTC for stable comparisons
        if self.timestamp.utcoffset() != timezone.utc.utcoffset(None):
            object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))
        # collapse surrounding whitespace; keep newlines for multiline doc summaries
        cleaned = self.summary.strip()
        if cleaned != self.summary:
            object.__setattr__(self, "summary", cleaned)


@dataclass
class ProjectReport:
    """Aggregated view of one project across all sources."""

    path: Path
    name: str
    signals: list[Signal] = field(default_factory=list)
    intent: str = ""
    intent_evidence: list[str] = field(default_factory=list)
    git_branch: str | None = None
    git_dirty: bool = False
    is_git_repo: bool = False
    observations: "object | None" = None  # populated by aggregator; observations.Observations
    git_ahead: int = 0                    # commits HEAD has that upstream lacks
    git_behind: int = 0                   # commits upstream has that HEAD lacks
    git_upstream: str | None = None       # tracking ref, e.g. 'origin/main'
    git_uncommitted: list[str] = field(default_factory=list)  # ['M src/foo.py', ...]
    plan_summaries: dict[str, PlanDocSummary] = field(default_factory=dict)

    @property
    def last_active(self) -> datetime | None:
        return max((s.timestamp for s in self.signals), default=None)

    @property
    def sources_active(self) -> list[Source]:
        return sorted({s.source for s in self.signals})

    def filter(self, *, sources: Iterable[Source] | None = None,
               kinds: Iterable[SignalKind] | None = None) -> list[Signal]:
        srcs = set(sources) if sources is not None else None
        kds = set(kinds) if kinds is not None else None
        out = []
        for s in self.signals:
            if srcs is not None and s.source not in srcs:
                continue
            if kds is not None and s.kind not in kds:
                continue
            out.append(s)
        return out

    def recent(self, *, n: int = 5,
               sources: Iterable[Source] | None = None,
               kinds: Iterable[SignalKind] | None = None) -> list[Signal]:
        return sorted(self.filter(sources=sources, kinds=kinds),
                      key=lambda s: s.timestamp, reverse=True)[:n]
