"""Command-line interface for project-commander."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console

from . import aggregator, discovery, report
from .sources.claude import ClaudeScanner
from .sources.docs import DocsScanner
from .sources.gemini import GeminiScanner
from .sources.git import GitScanner
from .sources.kiro import KiroScanner
from .sources.omp import OmpScanner
from .sources.opencode import OpenCodeScanner


# Folders we'll auto-scan when the user does not pass --root and does not
# set $PROJECT_COMMANDER_ROOTS. We probe each one; every folder that exists
# under $HOME is included. Common Linux/Mac conventions, plus the macOS
# Capitalized variants.
_DEFAULT_ROOT_NAMES: tuple[str, ...] = (
    "code", "projects", "src", "dev", "work", "repos", "git",
    "Code", "Projects", "Dev",
)


def _default_roots(home: Path) -> list[Path]:
    """Resolve project roots from env var or auto-detection under $HOME."""
    env = os.environ.get("PROJECT_COMMANDER_ROOTS")
    if env:
        return [Path(p).expanduser().resolve()
                for p in env.split(os.pathsep) if p.strip()]
    return [(home / name).resolve()
            for name in _DEFAULT_ROOT_NAMES if (home / name).is_dir()]


def _default_config(home: Path) -> dict:
    """Locations of agent-tool storage relative to the home directory."""
    return {
        "claude_projects": home / ".claude" / "projects",
        "claude_transcripts": home / ".claude" / "transcripts",
        "gemini_root": home / ".gemini",
        "omp_sessions": home / ".omp" / "agent" / "sessions",
        "opencode_storage": home / ".local" / "share" / "opencode" / "storage",
        "kiro_history": home / ".aws" / "amazonq" / "history",
        "home": home,
    }


def _add_report_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, action="append", default=[],
                        help="project root to scan (repeatable; default: "
                             "$PROJECT_COMMANDER_ROOTS or auto-detect from "
                             "$HOME/{code,projects,src,dev,work,repos,git,...})")
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="home directory used to locate tool session stores (default: $HOME)")
    parser.add_argument("--project", action="append", default=[],
                        help="show detail view for one project (basename glob; repeatable)")
    parser.add_argument("--exclude", action="append", default=[],
                        help="exclude project basenames matching glob (repeatable)")
    parser.add_argument("--since", type=int, default=None,
                        help="only show projects active within N days")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap to top N most recently active")
    parser.add_argument("--format", choices=["table", "json", "markdown"], default="table")
    parser.add_argument("--no-color", action="store_true",
                        help="disable colored output")
    parser.add_argument("--max-workers", type=int, default=8,
                        help="parallelism for project scans (default: 8)")
    parser.add_argument("--disable", action="append", default=[],
                        choices=["git", "claude", "gemini", "omp", "opencode", "kiro", "docs"],
                        help="skip a source (repeatable)")


def _run_report(args: argparse.Namespace) -> int:
    cfg = _default_config(args.home)
    roots = ([Path(r).expanduser().resolve() for r in args.root]
             or _default_roots(args.home))
    if not roots:
        print(
            "No project roots configured. Pass --root <path>, set "
            "PROJECT_COMMANDER_ROOTS, or create one of: "
            + ", ".join(f"~/{n}" for n in _DEFAULT_ROOT_NAMES),
            file=sys.stderr,
        )
        return 1

    projects = discovery.discover_projects_in_roots(roots)
    projects = discovery.filter_projects(projects, only=args.project or None,
                                         exclude=args.exclude or None)
    if not projects:
        roots_str = ", ".join(str(r) for r in roots)
        print(f"No projects found under {roots_str}", file=sys.stderr)
        return 1

    git = None if "git" in args.disable else GitScanner()
    scanners: list[object] = []
    if "claude" not in args.disable:
        scanners.append(ClaudeScanner(cfg["claude_projects"]))
    if "gemini" not in args.disable:
        scanners.append(GeminiScanner(cfg["gemini_root"]))
    if "omp" not in args.disable:
        scanners.append(OmpScanner(cfg["omp_sessions"], home=cfg["home"]))
    if "opencode" not in args.disable:
        scanners.append(OpenCodeScanner(cfg["opencode_storage"], cfg["claude_transcripts"]))
    if "kiro" not in args.disable:
        scanners.append(KiroScanner(cfg["kiro_history"]))
    if "docs" not in args.disable:
        scanners.append(DocsScanner())

    reports = aggregator.build_all(projects, scanners, git=git,
                                   max_workers=args.max_workers)

    if args.since is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.since)
        reports = [r for r in reports
                   if r.last_active is not None and r.last_active >= cutoff]
    if args.limit is not None:
        reports = reports[: args.limit]

    console = Console(no_color=args.no_color, soft_wrap=False)

    if args.project:
        if args.format == "json":
            sys.stdout.write(report.render_json(reports) + "\n")
        elif args.format == "markdown":
            parts = [report.render_detail_markdown(r) for r in reports]
            sys.stdout.write("\n---\n\n".join(parts))
        else:
            for r in reports:
                report.render_detail(r, console)
        return 0

    if args.format == "json":
        sys.stdout.write(report.render_json(reports) + "\n")
    elif args.format == "markdown":
        sys.stdout.write(report.render_markdown(reports))
    elif args.since is not None and args.since <= 30:
        report.render_review(reports, since_days=args.since, console=console)
    else:
        report.render_table(reports, console)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="project-commander",
        description="Survey project folders and apply hygiene actions across them.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="{report,tidy}")

    rp = sub.add_parser(
        "report",
        help="Survey your project folders by fusing git, agent history, and plan docs.",
        description="Survey your project folders by fusing git, agent history, and plan docs.",
    )
    _add_report_args(rp)
    rp.set_defaults(func=_run_report)

    from . import tidy
    tidy.add_subparser(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
