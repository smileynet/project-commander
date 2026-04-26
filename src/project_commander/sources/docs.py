"""In-project plan/intent documentation scanner.

Picks up files that explicitly state what the project is about or what comes
next. The first non-trivial paragraph of each file becomes the signal summary.

Files scanned (top-level only, plus a few well-known plan dirs):
- README.md / README.rst / README.txt
- AGENTS.md
- PLAN.md, IMPROVEMENTS.md, NEXT_STEPS.md, TODO.md, ROADMAP.md
- .sisyphus/*.md, .kiro/specs/**/*.md, .cavekit/*.md, .specify/spec.md

Timestamps are file mtimes — best available for "the doc currently asserts
this intent as of <date>".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import PlanDocSummary, Signal


# Names of docs we'll attempt to extract structure from. Other docs become
# prose-only signals via the regular scan path.
_STRUCTURED_DOCS = (
    "PLAN.md", "NEXT_STEPS.md", "TODO.md", "IMPROVEMENTS.md", "ROADMAP.md",
)

_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")
_PHASE_HEADING_RE = re.compile(
    r"^\s*#{1,4}\s+(?P<label>(?:[IVXLCDM]+\.|Phase\s+\d+|Step\s+\d+)[^\n]*?)\s*$",
    re.IGNORECASE,
)
_COMPLETE_MARKER_RE = re.compile(
    r"\((?:complete|completed|done|shipped)\)|~~[^~]+~~|\[x\]",
    re.IGNORECASE,
)

_TOP_LEVEL = (
    "README.md", "README", "README.rst", "README.txt",
    "AGENTS.md", "AGENT.md", "CLAUDE.md", "GEMINI.md",
    "PLAN.md", "IMPROVEMENTS.md", "NEXT_STEPS.md", "TODO.md", "ROADMAP.md",
)
_PLAN_DIR_GLOBS = (
    ".sisyphus/*.md",
    ".sisyphus/plans/*.md",
    ".kiro/specs/*.md",
    ".kiro/specs/**/*.md",
    ".cavekit/*.md",
    ".specify/*.md",
    "specs/*.md",
)


def _summary_from_markdown(text: str, *, limit: int = 320) -> str:
    """Return the first non-trivial paragraph as a single-line summary.

    Heuristic, in order of preference:
      1. The first prose paragraph (non-heading, non-comment, non-frontmatter).
      2. If only headings exist, the first heading's text.

    Strips leading markdown list markers so the result reads cleanly in a table.
    """
    in_frontmatter = False
    heading: str | None = None
    prose: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped:
            if prose:
                break
            continue
        if stripped.startswith("---") and not prose and heading is None:
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if stripped.startswith("<!--"):
            continue
        # Frontmatter-style metadata-only lines like `Date: 2026-04-21` or
        # `Status: completed` \u2014 skip ONLY when the whole line is metadata
        # (no continued prose after the value). A line like `**Status:**
        # experimental, offline. Not published to npm. Not wired into ...`
        # carries real content; we keep it and let observations.clean_doc_prose
        # strip the leading `Status:` prefix.
        first_word = stripped.split(":", 1)[0].strip("*_ ").lower()
        is_meta_key = first_word in {
            "date", "status", "author", "title", "tags",
            "category", "published", "updated", "version",
        }
        if is_meta_key and ":" in stripped[:30]:
            after_colon = stripped.split(":", 1)[1].strip("* ")
            # If short and no sentence-ending period, treat as pure metadata.
            if len(after_colon) < 60 and "." not in after_colon:
                continue
        # Pure-HTML lines (e.g. `<p align="center">`, `<a href="...">`, `<picture>`)
        # waste the prose budget; skip them entirely.
        if stripped.startswith("<") and stripped.endswith(">"):
            continue
        # Blockquote-only chrome lines (`>`, `> [!TIP]`, `> **Building in Public**`)
        # produce noisy intent strings; strip the marker and keep the content if any.
        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()
            if not stripped or stripped.startswith("[!") and stripped.endswith("]"):
                continue
        if stripped.startswith("#"):
            if heading is None:
                heading = stripped.lstrip("# ").strip()
            continue
        if stripped[:2] in ("- ", "* ", "+ "):
            stripped = stripped[2:]
        elif stripped[:1].isdigit() and stripped[:3].endswith(". "):
            stripped = stripped.split(". ", 1)[1]
        prose.append(stripped)
        if sum(len(p) for p in prose) > limit:
            break
    if prose:
        summary = " ".join(prose)
    elif heading:
        summary = heading
    else:
        return ""
    summary = " ".join(summary.split())
    if len(summary) > limit:
        summary = summary[: limit - 1].rsplit(" ", 1)[0] + "\u2026"
    return summary


class DocsScanner:
    name = "docs"

    def scan(self, project: Path) -> list[Signal]:
        signals: list[Signal] = []
        seen: set[Path] = set()
        for name in _TOP_LEVEL:
            p = project / name
            if p.is_file() and p not in seen:
                seen.add(p)
                signals.append(_doc_signal(p, project))
        for pattern in _PLAN_DIR_GLOBS:
            for p in project.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    signals.append(_doc_signal(p, project))
        return [s for s in signals if s is not None]

    def plan_summaries(self, project: Path) -> dict[str, PlanDocSummary]:
        out: dict[str, PlanDocSummary] = {}
        for name in _STRUCTURED_DOCS:
            p = project / name
            if not p.is_file():
                continue
            try:
                text = p.read_text(errors="replace")
            except OSError:
                continue
            summary = _parse_plan_structure(text, path=name)
            if summary.total_items > 0 or summary.total_phases > 0:
                out[name] = summary
        return out


def _doc_signal(p: Path, project: Path) -> Signal | None:
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return None
    summary = _summary_from_markdown(text) or p.stem
    rel = p.relative_to(project) if project in p.parents or p.parent == project else p.name
    ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    return Signal(
        source="docs", kind="doc", timestamp=ts,
        summary=f"[{rel}] {summary}",
        ref=str(rel),
    )


def _parse_plan_structure(text: str, *, path: str) -> PlanDocSummary:
    """Parse a structured plan document.

    Counts:
    - checkbox items (`- [ ]`, `- [x]`); next_item is the first unchecked
    - phase headings matching `## I. ...`, `## Phase 1 ...`, `## Step 1 ...`;
      a phase is 'complete' if its heading line carries (complete) / (done) /
      a strikethrough, or if the next non-empty line within the same heading
      block is a `**Status:** completed` marker
    """
    total_items = open_items = 0
    total_phases = complete_phases = 0
    next_item = ""

    lines = text.splitlines()
    for idx, raw in enumerate(lines):
        cb = _CHECKBOX_RE.match(raw)
        if cb:
            total_items += 1
            if cb.group("mark") == " ":
                open_items += 1
                if not next_item:
                    # strip residual markdown chrome from the item text
                    item_text = cb.group("text")
                    item_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", item_text)
                    item_text = re.sub(r"`([^`]+)`", r"\1", item_text)
                    next_item = item_text.strip()
            continue
        ph = _PHASE_HEADING_RE.match(raw)
        if ph:
            total_phases += 1
            heading_complete = bool(_COMPLETE_MARKER_RE.search(raw))
            if not heading_complete:
                # peek ahead: a phase block can be marked complete by a
                # following `**Status:** completed` line within 5 lines
                for follow in lines[idx + 1 : idx + 6]:
                    s = follow.strip().lower()
                    if s.startswith("**status:** complete") or s.startswith("status: complete"):
                        heading_complete = True
                        break
            if heading_complete:
                complete_phases += 1
    return PlanDocSummary(
        path=path,
        total_items=total_items,
        open_items=open_items,
        total_phases=total_phases,
        complete_phases=complete_phases,
        next_item=next_item,
    )