"""Oh-My-Pi (OMP) session scanner.

Sessions live at `~/.omp/agent/sessions/-code-<name>/<ts>_<id>.jsonl`. Each
session begins with a `{"type":"session", "cwd":..., "timestamp":..., "title":...}`
header followed by `model_change`, `thinking_level_change`, and `message` events.
We pull user-message content where available.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Signal
from ..paths import omp_key
from .claude import _summarize, _parse_ts


class OmpScanner:
    name = "omp"

    def __init__(self, sessions_root: Path, home: Path, *, max_prompts: int = 8) -> None:
        # sessions_root is `~/.omp/agent/sessions`
        self.sessions_root = sessions_root
        self.home = home
        self.max_prompts = max_prompts

    def project_dir(self, project: Path) -> Path:
        return self.sessions_root / omp_key(project, self.home)

    def scan(self, project: Path) -> list[Signal]:
        pdir = self.project_dir(project)
        if not pdir.is_dir():
            return []
        signals: list[Signal] = []
        files = sorted(
            (p for p in pdir.iterdir() if p.suffix == ".jsonl"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        seen = 0
        for f in files:
            if seen >= self.max_prompts:
                break
            try:
                title = ""
                with f.open() as fh:
                    for line in fh:
                        if seen >= self.max_prompts:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        et = evt.get("type")
                        if et == "session":
                            title = str(evt.get("title", "") or "")
                            ts = _parse_ts(evt.get("timestamp"))
                            if ts is not None:
                                signals.append(Signal(
                                    source="omp", kind="session",
                                    timestamp=ts,
                                    summary=title or f.stem,
                                    ref=str(evt.get("id", f.stem)),
                                ))
                            continue
                        if et != "message":
                            continue
                        msg = evt.get("message", {})
                        if msg.get("role") != "user":
                            continue
                        text = _extract_user_text(msg.get("content"))
                        if not text:
                            continue
                        ts = _parse_ts(evt.get("timestamp"))
                        if ts is None:
                            continue
                        signals.append(Signal(
                            source="omp", kind="prompt", timestamp=ts,
                            summary=_summarize(text), ref=title or f.stem,
                        ))
                        seen += 1
            except OSError:
                continue
        return signals


def _extract_user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "\n".join(out)
    return ""
