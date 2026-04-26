"""Combine source scanner output into per-project reports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from . import observations as observations_mod
from .models import ProjectReport, Signal
from .sources.git import GitScanner


def build_report(project: Path,
                 scanners: Sequence[object],
                 *, git: GitScanner | None) -> ProjectReport:
    """Run all scanners on `project` and return a `ProjectReport`.

    `git` is treated specially because we surface branch/dirty metadata at the
    report level. The git scanner's *signal* output still flows in via the
    regular path.
    """
    signals: list[Signal] = []
    is_repo = False
    branch: str | None = None
    dirty = False
    if git is not None:
        is_repo = git.is_repo(project)
        if is_repo:
            branch = git.branch(project)
            dirty = git.is_dirty(project)
            signals.extend(git.scan(project))

    for scanner in scanners:
        try:
            signals.extend(scanner.scan(project))  # type: ignore[attr-defined]
        except Exception:
            # one bad source shouldn't kill the whole report
            continue

    report = ProjectReport(
        path=project, name=project.name, signals=signals,
        is_git_repo=is_repo, git_branch=branch, git_dirty=dirty,
    )
    obs = observations_mod.build(report)
    report.observations = obs
    report.intent = obs.intent
    report.intent_evidence = list(obs.evidence)
    return report


def build_all(projects: Sequence[Path],
              scanners: Sequence[object],
              *, git: GitScanner | None,
              max_workers: int = 8) -> list[ProjectReport]:
    """Build reports in parallel — most scanners are I/O-bound."""
    if not projects:
        return []
    out: list[ProjectReport] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(build_report, p, scanners, git=git): p for p in projects}
        for fut in as_completed(futures):
            try:
                out.append(fut.result())
            except Exception as exc:
                p = futures[fut]
                out.append(ProjectReport(
                    path=p, name=p.name, signals=[],
                    intent=f"(scan failed: {exc!s})",
                ))
    out.sort(key=lambda r: r.last_active or _epoch(), reverse=True)
    return out


def _epoch():
    from datetime import datetime, timezone
    return datetime.fromtimestamp(0, tz=timezone.utc)
