"""Detect project intent from collected signals.

Intent is a one-line "what is this project trying to do" that a human can
read at a glance. We rely on three layered signals, in order of trust:

1. Explicit plan/intent docs (`PLAN.md`, `README.md`, `AGENTS.md`, …) —
   the project's own statement of itself.
2. The most recent user prompt across all conversation tools — what the
   developer last asked an agent to do here.
3. The most recent commit subject — what last actually shipped.

We never call out to an LLM: that would make this tool slow, network-bound,
and non-reproducible. The synthesized line is a cite-the-source quotation
chosen by recency and source priority. Evidence is preserved separately so a
reader can tell where it came from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import ProjectReport, Signal


# README/PLAN docs are the most authoritative claim of intent; commits are
# verified action; prompts are intent-in-flight.
_DOC_PRIORITY = {
    "PLAN.md": 100,
    "README.md": 90,
    "ROADMAP.md": 85,
    "NEXT_STEPS.md": 80,
    "IMPROVEMENTS.md": 75,
    "AGENTS.md": 70,
    "TODO.md": 60,
}


def detect(report: ProjectReport, *, recent_window_days: int = 30) -> tuple[str, list[str]]:
    """Return `(intent_line, evidence_lines)` for the report.

    Evidence is a small audit trail describing which signals contributed.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=recent_window_days)
    evidence: list[str] = []

    # 1. doc-derived intent
    doc_pick = _best_doc(report.signals)
    if doc_pick is not None:
        evidence.append(f"doc:{doc_pick.ref} ({_when(doc_pick.timestamp)})")

    # 2. most recent meaningful user prompt
    prompt_pick = _latest_prompt(report.signals, cutoff=cutoff)
    if prompt_pick is not None:
        evidence.append(f"prompt:{prompt_pick.source} ({_when(prompt_pick.timestamp)})")

    # 3. most recent commit
    commit_pick = _latest_kind(report.signals, "commit")
    if commit_pick is not None:
        evidence.append(f"commit:{commit_pick.ref} ({_when(commit_pick.timestamp)})")

    intent = _compose(doc_pick, prompt_pick, commit_pick)
    return intent, evidence


def _compose(doc: Signal | None, prompt: Signal | None, commit: Signal | None) -> str:
    if doc is not None:
        # docs are explicit statements; prefer them verbatim
        return _trim(doc.summary, limit=200)
    if prompt is not None:
        return f"[active] {_trim(prompt.summary, limit=200)}"
    if commit is not None:
        return f"[recent commit] {_trim(commit.summary, limit=200)}"
    return "(no signals)"


def _best_doc(signals: Iterable[Signal]) -> Signal | None:
    docs = [s for s in signals if s.kind == "doc"]
    if not docs:
        return None
    def score(s: Signal) -> tuple[int, datetime]:
        # ref looks like "[README.md] ..." stored as bracketed prefix in summary;
        # fall back to looking at the trailing path component of ref.
        name = s.ref.rsplit("/", 1)[-1]
        return _DOC_PRIORITY.get(name, 0), s.timestamp
    return max(docs, key=score)


def _latest_prompt(signals: Iterable[Signal], *, cutoff: datetime) -> Signal | None:
    prompts = [s for s in signals if s.kind == "prompt" and s.timestamp >= cutoff]
    if not prompts:
        return None
    return max(prompts, key=lambda s: s.timestamp)


def _latest_kind(signals: Iterable[Signal], kind: str) -> Signal | None:
    matches = [s for s in signals if s.kind == kind]
    if not matches:
        return None
    return max(matches, key=lambda s: s.timestamp)


def _trim(text: str, *, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _when(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d")
