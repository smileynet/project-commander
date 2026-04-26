"""Git source: last-commit + recent-commit signals.

Uses `git` directly via subprocess. Reading the on-disk objects ourselves would
be faster but git's CLI handles packed refs, worktrees, alternates, and
shallow clones for free; for a daily report tool, that's the right tradeoff.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal


class GitScanner:
    name = "git"

    def __init__(self, *, recent_commits: int = 50) -> None:
        # 50 is enough for one-month windows on busy projects without bloating
        # signal storage. Window-based analyses (catchup, audit, recap) lean on this.
        self.recent_commits = recent_commits

    def _run(self, project: Path, *args: str) -> str | None:
        try:
            res = subprocess.run(
                ["git", "-C", str(project), *args],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None
        return res.stdout

    def is_repo(self, project: Path) -> bool:
        out = self._run(project, "rev-parse", "--is-inside-work-tree")
        return out is not None and out.strip() == "true"

    def branch(self, project: Path) -> str | None:
        out = self._run(project, "branch", "--show-current")
        return out.strip() if out else None

    def status_porcelain(self, project: Path) -> list[str]:
        """Return per-line `git status --porcelain` output.

        Each entry is e.g. ` M src/foo.py`, `?? newfile`, `A  tests/x.py`. Empty list
        means the tree is clean.
        """
        out = self._run(project, "status", "--porcelain")
        if not out:
            return []
        return [line for line in out.splitlines() if line.strip()]

    def is_dirty(self, project: Path) -> bool:
        return bool(self.status_porcelain(project))

    def upstream(self, project: Path) -> str | None:
        """Return the tracking ref (e.g. 'origin/main') for the current branch, or None."""
        out = self._run(project, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if not out:
            return None
        ref = out.strip()
        return ref or None

    def ahead_behind(self, project: Path, upstream: str) -> tuple[int, int]:
        """Return `(ahead, behind)` commit counts vs the named upstream tracking ref."""
        out = self._run(project, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        if not out:
            return (0, 0)
        parts = out.split()
        if len(parts) != 2:
            return (0, 0)
        try:
            behind, ahead = int(parts[0]), int(parts[1])
        except ValueError:
            return (0, 0)
        return (ahead, behind)

    def scan(self, project: Path) -> list[Signal]:
        if not self.is_repo(project):
            return []
        # %H sha, %ct committer unix timestamp, %s subject
        out = self._run(
            project, "log",
            f"-n{self.recent_commits}",
            "--pretty=format:%H%x09%ct%x09%s",
            "--no-merges",
        )
        if not out:
            return []
        signals: list[Signal] = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            sha, ts, subject = parts
            try:
                t = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
            signals.append(Signal(
                source="git", kind="commit", timestamp=t,
                summary=subject, ref=sha[:12],
            ))
        return signals
