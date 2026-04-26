"""Kiro / Amazon Q CLI scanner.

Kiro persists chat history as `~/.aws/amazonq/history/chat-history-<md5>.json`
where `<md5>` is the MD5 of the workspace's absolute path (no trailing slash).
The file format is a LokiJS database snapshot. In practice, the `tabs`
collection only retains *currently open* tabs, so the file is usually a
near-empty database. We therefore use file mtime as the activity signal and
expose any non-empty `tabs` rows as `session` summaries.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from ..paths import kiro_hash


class KiroScanner:
    name = "kiro"

    def __init__(self, history_root: Path) -> None:
        # history_root: ~/.aws/amazonq/history
        self.history_root = history_root

    def history_file(self, project: Path) -> Path:
        return self.history_root / f"chat-history-{kiro_hash(project)}.json"

    def scan(self, project: Path) -> list[Signal]:
        f = self.history_file(project)
        if not f.is_file():
            return []
        signals: list[Signal] = []
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        signals.append(Signal(
            source="kiro", kind="session", timestamp=mtime,
            summary="kiro/amazon-q workspace activity",
            ref=f.name,
        ))
        # try to surface live tabs if any
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return signals
        for col in data.get("collections", []) if isinstance(data, dict) else []:
            if col.get("name") != "tabs":
                continue
            for row in col.get("data", []) or []:
                if not isinstance(row, dict):
                    continue
                title = row.get("title") or row.get("name") or "tab"
                ts_raw = row.get("updatedAt") or row.get("createdAt")
                ts = mtime
                if isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
                signals.append(Signal(
                    source="kiro", kind="session", timestamp=ts,
                    summary=str(title)[:200], ref=str(row.get("historyId", "")),
                ))
        return signals
