"""Claude Code session scanner.

Sessions live at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Each
line is one event; user prompts have `type:"user"` and a `message.content`
that is either a string or a list of `{"type":"text","text":...}` blocks.

We extract:
- `prompt` signals from user messages with substantive text content
- `session` signal at the file's last-modified time as a coarse fallback
"""

from __future__ import annotations

import json

from datetime import datetime, timezone
from pathlib import Path

from ..models import Signal
from ..paths import claude_key

# Skip injected synthetic prompts that are agent system directives, not user input.
_NOISE_MARKERS = (
    "<!-- OMO_INTERNAL_INITIATOR -->",
    "[SYSTEM DIRECTIVE:",
)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if isinstance(t, str):
                    chunks.append(t)
        return "\n".join(chunks)
    return ""


def _is_noise(text: str) -> bool:
    return any(marker in text for marker in _NOISE_MARKERS)


class ClaudeScanner:
    name = "claude"

    def __init__(self, projects_root: Path, *, max_prompts: int = 8) -> None:
        self.projects_root = projects_root
        self.max_prompts = max_prompts

    def session_dir(self, project: Path) -> Path:
        return self.projects_root / claude_key(project)

    def scan(self, project: Path) -> list[Signal]:
        sdir = self.session_dir(project)
        if not sdir.is_dir():
            return []
        signals: list[Signal] = []
        # newest sessions first by file mtime
        files = sorted(
            (p for p in sdir.iterdir() if p.suffix == ".jsonl"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        seen_prompts = 0
        for f in files:
            if seen_prompts >= self.max_prompts:
                # still emit the file mtime as a session marker, then stop
                signals.append(_session_signal(f))
                break
            try:
                with f.open() as fh:
                    for line in fh:
                        if seen_prompts >= self.max_prompts:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if evt.get("type") != "user":
                            continue
                        msg = evt.get("message")
                        if not isinstance(msg, dict):
                            continue
                        text = _extract_text(msg.get("content"))
                        if not text or _is_noise(text):
                            continue
                        ts = _parse_ts(evt.get("timestamp"))
                        if ts is None:
                            continue
                        signals.append(Signal(
                            source="claude", kind="prompt", timestamp=ts,
                            summary=_summarize(text),
                            ref=str(evt.get("sessionId", "")),
                        ))
                        seen_prompts += 1
            except OSError:
                continue
        # Always include latest session timestamp regardless, to capture activity even
        # when the session is non-text (hooks, slash commands).
        if files:
            signals.append(_session_signal(files[0]))
        return signals


def _parse_ts(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        # JS-style ISO strings end in 'Z'
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _session_signal(f: Path) -> Signal:
    ts = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
    return Signal(
        source="claude", kind="session", timestamp=ts,
        summary=f"session {f.stem}", ref=f.stem,
    )


def _summarize(text: str, *, limit: int = 220) -> str:
    """Trim a prompt for table display while preserving its first sentence."""
    text = " ".join(text.split())  # collapse whitespace
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"
