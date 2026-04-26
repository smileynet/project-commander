# project-commander

> **What did I work on last week, what's still in flight, and where do my plans no longer match reality?**

If you have dozens of repos in `~/code` and split your work across
multiple coding agents — Claude Code, Cursor, Codex, Gemini, OMP,
OpenCode, Kiro, and friends — there is no single place that knows
the answer. Each tool keeps its own session history. Plans live in
`PLAN.md` files that drift out of date. Some projects are dirty. Some
haven't been touched in months. Some you can't remember starting.

`project-commander` reads every place a project leaves a trail and
collapses it into one record per project. Then it tells you, in one
view, what's actually happening across the fleet.

## The jobs you hire it to do

| When you want to… | Run |
|---|---|
| **Reorient yourself** after a weekend or context switch | `project-commander --since 7` |
| **Resume a project** and need its full story before diving back in | `project-commander --project <name>` |
| **Catch drift** — plans that say "done" but commits keep landing | look for `Drifting` in the Progress column |
| **Find tool-clusters** — projects where every agent has converged | look for `tool-cluster` in the Flags |
| **Share status** with a teammate or your future self | `project-commander --format markdown > status.md` |
| **Notice abandoned work** — dirty trees, stale prompts, no commits | look for `Paused` projects with `dirty-tree` |
| **Audit security** — flag prompt-injection probes across the fleet | look for `prompt-injection-detected` |

## What you'll see

The default view — one row per project, sorted by recency:

```
  Project              Last active   Progress   Sources   Git       Intent
  ───────────────────  ────────────  ─────────  ────────  ────────  ─────────────────────
  project-commander    22m ago       Hot        DGOP      main*     Survey every project…
                                                                    Currently: focus on …
  best_practices       4h ago        Drifting   GCD       main*     A reusable best-pra…
                                                                    Currently: ship the …
  catalyst             41w ago       Dormant    G         main      Fleet of cooperatin…
```

`Sources` is a per-letter map of which tools touched this project:
**G**it, **C**laude, ge**M**ini, **O**MP, o**P**encode, **K**iro,
**D**ocs. `DGOP` means docs + git + OMP + opencode have all left a
trail.

The detail view (`--project`) gives you the full audit trail for one
project: progress summary, purpose, focus, last action, flags, the
evidence behind each claim, 7d/30d activity counts, and the actual
recent commits, prompts, sessions, and plan-doc edits.

## Install

```sh
pip install -e .
```

Python ≥ 3.10. The only runtime dependency is
[`rich`](https://rich.readthedocs.io).

## First run

```sh
project-commander
```

That's it. Walks `~/code`, reads your tool storage, prints the table.
No daemon, no cache, no config file.

## Five things you can ask it

### Reorient me — what did I work on last week?

```sh
project-commander --since 7
```

Filters to projects with any activity in the last 7 days. Sorted
recency-first. Clears the noise of 60+ stale folders.

### Show me everything about one project before I dive in

```sh
project-commander --project cdda_improved
```

Detail view. The first thing in the output is what the project is
*for*; the second is what you were doing on it most recently. Then
flags, evidence, activity stats, and recent commits / prompts /
sessions / plan-doc edits.

### Are any of my plans out of date?

```sh
project-commander | grep Drifting
```

`Drifting` means the highest-priority plan doc declares the project
"completed" or "shipped" while commits keep landing. Either close the
plan or update its status.

### Which projects has my whole agent fleet touched?

```sh
project-commander --format json | jq -r '
  .[] | select((.observations.flags // []) | index("tool-cluster")) | .name
'
```

`tool-cluster` fires when ≥4 different tools have touched a project.
These are the cross-cutting "main" projects — likely your dotfiles,
notes, or whatever you reach for from every agent.

### Share status with a teammate (or my future self)

```sh
project-commander --since 7 --format markdown > status-this-week.md
project-commander --project key-project --format markdown > project-audit.md
```

The first is a fleet markdown table. The second is a single-project
audit with proper headings, an activity table, recent commits, and
the substantive prompts (procedural one-liners are summarized
separately so they don't clutter the report).

## Reference

```sh
project-commander                          # all projects
project-commander --since N                # only projects active in last N days
project-commander --limit N                # cap to top N
project-commander --project <glob>         # detail view for matching folders (repeatable)
project-commander --exclude <glob>         # hide matching folders (repeatable)
project-commander --format json|markdown   # alternate output
project-commander --disable <source>       # skip a source (repeatable)
project-commander --root <path>            # scan somewhere other than ~/code
project-commander --no-color               # plain output
```

## Going deeper

- **How does it produce these reports?** A user-facing walkthrough
  with diagrams, the Report key, and the heuristics behind Progress
  and Intent: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Want to extend or contribute?** Module layout, the scanner
  protocol, adding a new source, the candidate-source roadmap, and
  testing: [`AGENTS.md`](AGENTS.md).

## Privacy

`project-commander` reads only your local filesystem. No network
calls, no LLM calls, no telemetry. The report is fully reproducible
from disk state.

The generated `reports/` directory is gitignored; it's derived from
your private `~/code` activity and is not meant to be committed.

## Limitations

- Gemini CLI keys sessions by workdir basename only; two repos with
  the same basename collide. `~/code` rarely has same-name siblings.
- Kiro's chat-history file is usually a near-empty LokiJS database
  (only currently open tabs persist). We use file mtime as the
  activity signal.
- OpenCode prompt content lives in `~/.claude/transcripts/`. If those
  transcripts are pruned, only session-level activity is reported.
- The intent heuristic prefers explicit docs. If a project has a
  stale `README.md`, the reported intent will reflect the doc, not
  the latest work — which is exactly when `Drifting` fires. Use
  `--project <name>` for the detail view when you suspect drift.
