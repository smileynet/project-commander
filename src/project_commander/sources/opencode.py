"""OpenCode session scanner.

OpenCode persists session metadata as opaque storage records:
- `~/.local/share/opencode/storage/directory-readme/ses_*.json` includes
  `injectedPaths` (the workspace cwd) and `updatedAt` (epoch millis).
- `~/.local/share/opencode/storage/session_diff/ses_*.json` carries file
  patches per session; the parent directory's mtime is a usable activity
  signal even when the directory-readme record is missing.

User-prompt content for OpenCode sessions is stored under
`~/.claude/transcripts/ses_*.jsonl` — these JSONL files use the same shape
as Claude Code transcripts (`type:"user"`, `content` string) but lack a `cwd`
field, so we link them by `sessionId`.

Strategy:
1. Build a `sessionId -> cwd` index from `directory-readme/`.
2. For each project in scope, emit a `session` signal at `updatedAt` and
   harvest user prompts from the matching transcript file (when present).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from .claude import _summarize, _is_noise


class OpenCodeScanner:
    name = "opencode"

    def __init__(self,
                 storage_root: Path,
                 transcripts_root: Path | None,
                 *, max_prompts: int = 8) -> None:
        # storage_root: ~/.local/share/opencode/storage
        # transcripts_root: ~/.claude/transcripts (optional)
        self.storage_root = storage_root
        self.transcripts_root = transcripts_root
        self.max_prompts = max_prompts
        self._cwd_to_sessions: dict[Path, list[tuple[str, datetime]]] = defaultdict(list)
        self._build_index()

    def _build_index(self) -> None:
        readme_dir = self.storage_root / "directory-readme"
        if not readme_dir.is_dir():
            return
        for f in readme_dir.iterdir():
            if not f.name.startswith("ses_") or f.suffix != ".json":
                continue
            try:
                rec = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            sid = rec.get("sessionID") or f.stem
            ts_ms = rec.get("updatedAt")
            paths = rec.get("injectedPaths") or []
            if not isinstance(paths, list) or not isinstance(ts_ms, (int, float)):
                continue
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            for p in paths:
                if not isinstance(p, str) or not p:
                    continue
                self._cwd_to_sessions[Path(p).resolve()].append((sid, ts))

    def scan(self, project: Path) -> list[Signal]:
        sessions = self._cwd_to_sessions.get(project.resolve(), [])
        if not sessions:
            return []
        sessions.sort(key=lambda x: x[1], reverse=True)
        signals: list[Signal] = []
        # session-level signals
        for sid, ts in sessions[:5]:
            signals.append(Signal(
                source="opencode", kind="session", timestamp=ts,
                summary=f"opencode session {sid}", ref=sid,
            ))
        # prompts from transcripts
        if self.transcripts_root is None:
            return signals
        prompts: list[Signal] = []
        for sid, _ in sessions:
            if len(prompts) >= self.max_prompts:
                break
            f = self.transcripts_root / f"{sid}.jsonl"
            if not f.is_file():
                continue
            for sig in _read_transcript(f, sid):
                if len(prompts) >= self.max_prompts:
                    break
                prompts.append(sig)
        return signals + prompts


def _read_transcript(f: Path, sid: str) -> list[Signal]:
    out: list[Signal] = []
    try:
        with f.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "user":
                    continue
                content = evt.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                if _is_noise(content):
                    continue
                ts_raw = evt.get("timestamp")
                if not isinstance(ts_raw, str):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
                    continue
                out.append(Signal(
                    source="opencode", kind="prompt", timestamp=ts,
                    summary=_summarize(content), ref=sid,
                ))
    except OSError:
        return out
    return out
