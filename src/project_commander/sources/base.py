"""Protocol shared by every signal source."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Signal


class SourceScanner(Protocol):
    """Each scanner emits signals for a given absolute project path.

    Stateful sources (those that need to read a global session index once and
    then dispatch by project) build their state in `__init__` so the per-project
    scan stays cheap.
    """

    name: str

    def scan(self, project: Path) -> list[Signal]: ...
