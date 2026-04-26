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

from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal

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
