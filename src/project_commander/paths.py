"""Path encoding helpers for cross-tool session-directory naming.

Different agent tools use different conventions to derive a "project key"
from a working directory. This module isolates that knowledge so the source
scanners stay focused on payload parsing.

Conventions observed:
- Claude Code: replaces `/` with `-` in the absolute path.
  `/home/sam/code/foo` -> `-home-sam-code-foo`
- Oh-My-Pi: same scheme but typically rooted at `/home/<user>` so the
  prefix is stripped, leaving `-code-foo`.
- Gemini CLI: stores chats under the workdir's *basename* only
  (`~/.gemini/tmp/foo/...`). Collisions on basename are possible across the
  filesystem; we accept that and verify via internal session payloads when
  available.
- Kiro / Amazon Q: filename is `chat-history-<md5(abspath)>.json`. The path
  is hashed without a trailing slash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def claude_key(path: Path) -> str:
    """Encode an absolute project path to Claude's project-dir name."""
    p = str(path.resolve())
    return p.replace("/", "-")


def omp_key(path: Path, home: Path) -> str:
    """Encode an absolute project path to Oh-My-Pi's session-dir name.

    OMP prefixes are relative to the user's home: `/home/sam/code/foo`
    becomes `-code-foo` (the `/home/sam` prefix is dropped).
    """
    abs_path = str(path.resolve())
    home_str = str(home.resolve())
    rel = abs_path[len(home_str):] if abs_path.startswith(home_str) else abs_path
    if not rel.startswith("/"):
        rel = "/" + rel
    return rel.replace("/", "-")


def gemini_key(path: Path) -> str:
    """Gemini CLI uses the basename of the workdir."""
    return path.resolve().name


def kiro_hash(path: Path) -> str:
    """md5 of the absolute path (no trailing slash) — the Kiro convention."""
    return hashlib.md5(str(path.resolve()).encode()).hexdigest()
