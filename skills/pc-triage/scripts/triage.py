#!/usr/bin/env python3
"""pc-triage — group project-commander findings by urgency, recommend actions.

Reads `project-commander --format json`, applies a fixed set of
heuristics, prints a markdown action list grouped by urgency.

Heuristics are deliberately stable so users can trust them; if you
want to change them, edit `RULES` below and call out the change.

Usage:
	triage.py [--since N] [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def _days_since(iso: str | None) -> int | None:
	if not iso:
		return None
	ts = datetime.fromisoformat(iso)
	return (datetime.now(tz=timezone.utc) - ts).days


def _find_pc() -> str | None:
	"""Locate `project-commander`. Honor an explicit env var, then PATH,
	then the same bin/ as the running interpreter (so the script just
	works when launched as `.venv/bin/python skills/.../triage.py`).
	"""
	import os
	override = os.environ.get("PROJECT_COMMANDER")
	if override:
		return override
	hit = shutil.which("project-commander")
	if hit:
		return hit
	candidate = os.path.join(os.path.dirname(sys.executable), "project-commander")
	if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
		return candidate
	return None


def _run_pc(*, since: int | None, root: str | None) -> list[dict]:
	pc = _find_pc()
	if pc is None:
		print(
			"error: `project-commander` not found. Install with "
			"`pip install -e .` from the repo root, or set PROJECT_COMMANDER\n"
			"       to the absolute path of the binary.",
			file=sys.stderr,
		)
		sys.exit(2)
	cmd = [pc, "--format", "json"]
	if since is not None:
		cmd.extend(["--since", str(since)])
	if root:
		cmd.extend(["--root", root])
	result = subprocess.run(cmd, check=True, capture_output=True, text=True)
	return json.loads(result.stdout)

def _classify(projects: list[dict]) -> dict[str, list[tuple[str, str]]]:
	"""Apply the rule cascade. First rule that matches wins for the
	primary slot; informational rules can also pile on `for_records`.
	"""
	urgent: list[tuple[str, str]] = []
	this_week: list[tuple[str, str]] = []
	when_you_have_time: list[tuple[str, str]] = []
	for_records: list[tuple[str, str]] = []

	for p in projects:
		name = p.get("name") or "(unnamed)"
		obs = p.get("observations") or {}
		flags = set(obs.get("flags") or [])
		progress = obs.get("progress") or ""
		days = _days_since(p.get("last_active"))

		# urgent
		if "prompt-injection-detected" in flags:
			urgent.append((name, "review the recent prompts; rotate or harden the affected agent"))
			continue

		# this week
		if "plan-drift" in flags:
			this_week.append((name, "update the plan doc or close it; commits have continued past 'completed'"))
			continue
		if "dirty-tree" in flags and (days or 0) > 7:
			this_week.append((name, f"commit or stash; tree dirty for {days}d"))
			continue

		# when you have time
		if progress == "dormant":
			rec = "archive or delete; no plan and no activity in 90+d" if "no-docs" in flags else "review and decide: revive or archive"
			when_you_have_time.append((name, rec))
			continue
		if progress == "stub":
			when_you_have_time.append((name, "either start or delete; documentation only, no work yet"))
			continue

		# for your records (non-exclusive)
		if "tool-cluster" in flags:
			for_records.append((name, "cross-cutting project (4+ tools); know that leakage tends to happen here"))
		if progress == "drifting":  # didn't catch above because plan-drift flag wasn't set; still worth knowing
			for_records.append((name, "currently classified Drifting (commits past plan completion)"))

	return {
		"urgent": urgent,
		"this_week": this_week,
		"when_you_have_time": when_you_have_time,
		"for_records": for_records,
	}


def _render(buckets: dict[str, list[tuple[str, str]]]) -> str:
	out: list[str] = []
	now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
	out.append("# Triage report")
	out.append("")
	out.append(f"_Generated {now}_")
	out.append("")

	sections = [
		("Urgent", buckets["urgent"], "Stop and address."),
		("This week", buckets["this_week"], "Take care of these in your next session."),
		("When you have time", buckets["when_you_have_time"], "Maintenance — not blocking work."),
		("For your records", buckets["for_records"], "No action needed; awareness only."),
	]

	if not any(items for _, items, _ in sections):
		out.append("Nothing to triage. Clean fleet.")
		return "\n".join(out) + "\n"

	for title, items, blurb in sections:
		if not items:
			continue
		out.append(f"## {title}")
		out.append("")
		out.append(f"_{blurb}_")
		out.append("")
		for name, action in items:
			out.append(f"- **{name}** — {action}")
		out.append("")

	return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="pc-triage — prioritized action list for ~/code")
	parser.add_argument("--since", type=int, default=None,
						help="only consider projects active in the last N days")
	parser.add_argument("--root", default=None,
						help="scan a different root (default: ~/code)")
	args = parser.parse_args(argv)

	projects = _run_pc(since=args.since, root=args.root)
	buckets = _classify(projects)
	sys.stdout.write(_render(buckets))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
