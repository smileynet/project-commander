"""Discover candidate project directories under a root."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def discover_projects(root: Path) -> list[Path]:
    """Return one absolute path per immediate subdirectory of `root`.

    No filtering on git-ness — non-git folders (notes, docs collections,
    archives) often still carry plan documents and conversation history.
    Hidden directories (those starting with `.`) are skipped.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        out.append(child.resolve())
    return out


def filter_projects(projects: Iterable[Path],
                    *, only: list[str] | None = None,
                    exclude: list[str] | None = None) -> list[Path]:
    """Filter the discovered list by basename glob match."""
    import fnmatch
    keep: list[Path] = []
    for p in projects:
        name = p.name
        if only and not any(fnmatch.fnmatch(name, pat) for pat in only):
            continue
        if exclude and any(fnmatch.fnmatch(name, pat) for pat in exclude):
            continue
        keep.append(p)
    return keep
