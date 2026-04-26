# project-commander

Survey every project in `~/code` by fusing signals from git, agent
conversation history, and in-project plan documentation. Reports the
**last action observed**, **most recent changes**, and a **detected intent**
per project — at a glance, across every coding tool you use.

## Why

You probably have dozens of repos in `~/code`. Each one has a story scattered
across many places:

- `git log` says what shipped.
- `~/.claude/projects/<encoded-cwd>/*.jsonl` says what you asked Claude Code.
- `~/.gemini/tmp/<basename>/{logs.json,chats/}` says what you asked Gemini CLI.
- `~/.omp/agent/sessions/-code-<name>/*.jsonl` says what you ran through Oh-My-Pi.
- `~/.local/share/opencode/storage/directory-readme/ses_*.json` knows which
  workspaces OpenCode visited; transcripts at `~/.claude/transcripts/ses_*.jsonl`
  hold the prompts.
- `~/.aws/amazonq/history/chat-history-<md5(cwd)>.json` records when Kiro /
  Amazon Q last touched a workspace.
- `README.md`, `PLAN.md`, `AGENTS.md`, `.sisyphus/*.md`, `.kiro/specs/*` say
  what the project *intends* to be.

`project-commander` reads them all, normalizes them into per-project
`Signal`s, and renders a single report.

## Install

```sh
pip install -e .
```

Python ≥ 3.10. The only runtime dep is [`rich`](https://rich.readthedocs.io)
for the table renderer.

## Usage

```sh
project-commander                              # all projects, recent first
project-commander --since 14                   # only projects active within 14 days
project-commander --limit 20                   # top 20 most recently active
project-commander --project lacrosse-bosse     # detail view for one project
project-commander --project 'aws-*'            # detail view, glob match
project-commander --exclude 'archive-*'        # skip a glob
project-commander --format markdown            # markdown table
project-commander --format json                # full signal dump as JSON
project-commander --disable kiro --disable omp # skip individual sources
```

The default table:

```
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Project          ┃ Last active ┃ Sources ┃ Git        ┃ Intent             ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ project-commander│ 2h ago      │ GCD     │ main*      │ [README.md] …      │
│ lacrosse-bosse   │ 2d ago      │ GCO     │ main       │ [active] roster …  │
└──────────────────┴─────────────┴─────────┴────────────┴────────────────────┘
```

Source flags: **G**it, **C**laude Code, ge**M**ini, **O**h-My-Pi,
o**P**enCode, **K**iro, **D**ocs.

Git column shows the current branch and a trailing `*` if the working tree is
dirty.

## How intent is determined

Layered heuristic, never an LLM call:

1. **Plan/intent doc** — first non-trivial paragraph from the
   highest-priority doc (`PLAN.md` > `README.md` > `ROADMAP.md` > …).
2. **Most recent prompt** — if no doc, the latest user prompt across all
   conversation tools (within 30 days), prefixed `[active]`.
3. **Most recent commit** — if neither, the most recent commit subject,
   prefixed `[recent commit]`.

Evidence (which signals supplied the intent line, with dates) is included in
detail view and JSON output.

## Architecture

```
src/project_commander/
├── cli.py             argparse entry point
├── discovery.py       walk ~/code/*/, glob filter
├── paths.py           per-tool cwd → session-dir name encoding
├── models.py          Signal, ProjectReport
├── aggregator.py      run scanners in parallel, build reports
├── intent.py          synthesize intent line + evidence
├── report.py          rich table / markdown / json rendering
└── sources/
    ├── git.py
    ├── claude.py
    ├── gemini.py
    ├── omp.py
    ├── opencode.py
    ├── kiro.py
    └── docs.py
```

Each source implements `scan(project: Path) -> list[Signal]`. The aggregator
fans out across projects with a thread pool — every scanner is I/O-bound.

## Adding a new source

1. Create `src/project_commander/sources/<tool>.py` with a class exposing
   `name = "<tool>"` and `scan(project) -> list[Signal]`.
2. Register it in `cli.py` next to the other scanners.
3. Add the source flag letter to `_SRC_FLAGS` in `report.py`.
4. Add a fixture-based test in `tests/test_scanners.py`.

`Signal` timestamps must be timezone-aware UTC; the dataclass enforces this.

## Tests

```sh
pip install -e ".[dev]"
pytest
```

The test suite uses synthetic fixtures — no network, no real `~/code` access.

## Limitations

- Gemini CLI keys sessions by workdir basename only; two repos with the same
  basename collide. `~/code` rarely has same-name siblings, so we accept this.
- Kiro's chat-history file is usually a near-empty LokiJS database (only
  *currently open* tabs persist). We use file mtime as the activity signal.
- OpenCode prompt content lives in `~/.claude/transcripts/`. If those
  transcripts are pruned, only session-level activity is reported.
- The intent heuristic prefers explicit docs. If a project has a stale
  `README.md`, the reported intent will reflect the doc, not the latest work.
  Use `--project <name>` for the detail view (which also shows recent prompts
  and commits) when you suspect drift.
