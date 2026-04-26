"""Gemini CLI session scanner.

Sessions live at `~/.gemini/tmp/<basename>/{logs.json, chats/session-*.json[l]}`.

`logs.json` is the canonical chronological log: a flat array of events, each
with a `type` (`user` is the prompt event), `message`, and `timestamp`.
Chat session files contain a `messages` array per session for richer context.

Because Gemini keys directories by basename only, two repos with the same
basename collide. We accept that — the alternative (cross-checking session
content) is brittle and `~/code` rarely has same-name siblings.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from ..paths import gemini_key
from .claude import _summarize, _parse_ts  # re-use helpers


class GeminiScanner:
    name = "gemini"

    def __init__(self, gemini_root: Path, *, max_prompts: int = 8) -> None:
        # `gemini_root` points at `~/.gemini`
        self.gemini_root = gemini_root
        self.max_prompts = max_prompts

    def project_dir(self, project: Path) -> Path:
        return self.gemini_root / "tmp" / gemini_key(project)

    def scan(self, project: Path) -> list[Signal]:
        pdir = self.project_dir(project)
        if not pdir.is_dir():
            return []
        signals: list[Signal] = []
        signals.extend(self._scan_logs(pdir / "logs.json"))
        signals.extend(self._scan_chats(pdir / "chats"))
        # Cap prompt signals to the most recent N by timestamp.
        prompts = sorted(
            (s for s in signals if s.kind == "prompt"),
            key=lambda s: s.timestamp, reverse=True,
        )[: self.max_prompts]
        non_prompts = [s for s in signals if s.kind != "prompt"]
        return prompts + non_prompts

    def _scan_logs(self, logs: Path) -> list[Signal]:
        if not logs.is_file():
            return []
        try:
            data = json.loads(logs.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        out: list[Signal] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "user":
                continue
            msg = item.get("message")
            if not isinstance(msg, str) or not msg.strip() or msg.strip().startswith("/"):
                # `/rewind`, `/clear` are control commands, not intent.
                continue
            ts = _parse_ts(item.get("timestamp"))
            if ts is None:
                continue
            out.append(Signal(
                source="gemini", kind="prompt", timestamp=ts,
                summary=_summarize(msg),
                ref=str(item.get("sessionId", "")),
            ))
        return out

    def _scan_chats(self, chats_dir: Path) -> list[Signal]:
        if not chats_dir.is_dir():
            return []
        out: list[Signal] = []
        latest_mtime: float | None = None
        for f in chats_dir.iterdir():
            if not f.is_file() or f.suffix not in (".json", ".jsonl"):
                continue
            mt = f.stat().st_mtime
            if latest_mtime is None or mt > latest_mtime:
                latest_mtime = mt
        if latest_mtime is not None:
            out.append(Signal(
                source="gemini", kind="session",
                timestamp=datetime.fromtimestamp(latest_mtime, tz=timezone.utc),
                summary=f"latest chat in {chats_dir.parent.name}",
                ref=str(chats_dir.parent.name),
            ))
        return out
