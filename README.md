# project-commander

> The fleet check-in for developers who keep losing track of their own work.

## Recognize this?

- **You don't remember what's in flight.** It's Monday. You touched
  a handful of projects last week. You can't recall which. You have
  uncommitted work *somewhere*; you don't want to context-switch
  until you find it.
- **Things slip through the cracks.** With this many projects, some
  never got `git init`'d. Others have weeks of uncommitted work you
  forgot about. Forks are out of sync with upstream. You don't have
  time to manually check 60 folders for hygiene.
- **Your plans lie to you.** That `PLAN.md` says "completed". You
  committed three times to that project on Wednesday. Either the
  plan is stale or you forgot the project shipped — and you can't
  tell which without re-reading everything.
- **Your tool history is scattered.** You used Claude Code on this
  one, Cursor on that one, OpenCode for a sprint, then Codex came
  out and now you're using that. The thinking you did with each
  agent lives in a different format under a different home-dir
  path. Reconstructing what you asked any of them is archaeology.
- **You can't see your own capacity.** You'd say you're active on
  three projects. Your dirty-tree count says eleven. Something has
  to give and you can't see the pattern.
- **Coming back hurts.** You haven't opened that folder in two
  weeks. You spend forty-five minutes reading commits, prompts,
  and plan docs before you can write a line of code. By the time
  you're back in the zone, half the morning is gone.
- **Status updates are archaeology.** It's Friday. Someone wants to
  know what shipped. Your recall is poor; your commit messages are
  worse. You promise to send something "in a bit" and quietly dread
  it.
- **Your project folder has gotten away from you.** There are folders
  in there you don't remember creating. Are they dead? Half-finished?
  Sitting there judging you?

`project-commander` is the tool you wish existed when those moments
hit. It doesn't help you write code. It helps you remember **what
code you've been writing**, **what you said you'd do with it**, and
**where the gap between those two has opened up**.

It does that without LLM calls, without a daemon, and without
sending anything anywhere. Every report is reproducible from the
state of your local filesystem.

## What you'll see

The default view — one row per project, sorted by recency:

```
  Project              Last active   Progress   Sources   Git       Intent
  ───────────────────  ────────────  ─────────  ────────  ────────  ─────────────────────
  project-commander    22m ago       Hot        DGOP      main*     The fleet check-in…
                                                                    Currently: focus on …
  best_practices       4h ago        Drifting   GCD       main*     A reusable best-pra…
                                                                    Currently: ship the …
  catalyst             41w ago       Dormant    G         main      Fleet of cooperatin…
```

The detail view (`--project NAME`) opens with what the project is
*for*, what you were doing on it most recently, the flags worth
attention, and the receipts (commits + agent prompts + plan-doc
edits).

## Install

```sh
pip install -e .
```

Python ≥ 3.10. The only runtime dependency is
[`rich`](https://rich.readthedocs.io).

## First run

```sh
project-commander report
```

Auto-detects your project folder (any of `~/code`, `~/projects`, `~/src`,
`~/dev`, `~/work`, `~/repos`, `~/git`, plus the macOS-cased variants),
reads your tool storage, prints the table. No daemon, no cache, no
config file. Override with `--root <path>` or set
`PROJECT_COMMANDER_ROOTS=path1:path2` if your projects live somewhere
else — or in more than one place.

## How it shows up in your workflow

**Monday morning, no idea what's in flight.**

```sh
project-commander report --since 7
```

Filters to projects with any activity in the last 7 days. Dirty
trees flag themselves. The `Drifting` rows are the projects where
plan and reality diverged — those are the ones you'd otherwise miss.

**Coming back to a project after a break.**

```sh
project-commander report --project cdda_improved
```

The detail view answers four questions in this order: *What is this
for? What was I doing on it most recently? What's flagged? Where's
the evidence?* By the time you finish reading you should know
whether to dive in or close the tab.

**End-of-week status update.**

```sh
project-commander report --since 14 --format markdown > review.md
```

Substantive prompts are kept; bare approvals (`yes`, `proceed`) are
collapsed into a one-line summary so the record reads like work
notes, not a chat log. Edit, share, archive.

**Cleaning house in your project folder.**

```sh
project-commander report | grep -E 'Dormant|Empty|Stub'
```

Things you forgot you started. The Evidence trail tells you why each
was classified that way, so you can decide: revive, archive, delete.

**A creeping sense something is off.**

Look at the Flags column. `prompt-injection-detected` flags prompts
that probed for system-prompt extraction. `tool-cluster` shows the
projects every agent in your stack has converged on (often
unintended — the place leakage happens). `procedural-prompts` flags
projects where you're approving an agent rather than directing one.

**Catching up on hygiene.**

```sh
project-commander tidy --dry-run   # show what would change
project-commander tidy             # do it
project-commander tidy --sync      # also `git fetch` everywhere
project-commander tidy --push      # push only branches that don’t contain hygiene commits
```

By default the `tidy` subcommand runs two opinionated, safe-by-design
actions across every configured project root:

- **Init repos that aren't repos.** A folder with content but no `.git/`
  gets `git init` + an initial commit. Tagged with the hygiene trailer
  (see below) so it's identifiable as auto-created later.
- **Checkpoint stale dirty trees.** Working trees that have been dirty
  for at least seven days get one auto-commit. The message says it's a
  hygiene checkpoint, not curated work.

Every commit `tidy` makes carries a `Project-Commander-Hygiene: true`
trailer. That's the boundary between *work you did* and *housekeeping
the tool did*. The push policy honors it: even with `--push`, the tool
**refuses to push any branch that has hygiene commits in its unpushed
range** — only your real work gets pushed.

Tunable knobs: `--no-init`, `--no-commit`, `--stale-age N`, `--sync`,
`--push`, `--project <glob>`, `--exclude <glob>`. Run
`project-commander tidy --help` for the full list.
## Run it the easy way with just

A `justfile` ships at the repo root. Common recipes:

```sh
just                  # list available recipes
just smoke            # quick fleet view (last 7 days)
just report           # fleet markdown report  → reports/
just detail NAME=foo  # detail markdown for one project → reports/foo.md
just publish          # regenerate the full report set
just test             # run pytest
```

`reports/` is gitignored. It is generated from your private project
activity — not source-controlled.

## Skills for your agent harness

If you run an OMP/cavekit-style harness, `skills/` ships an installable
skill bundle:

- **`pc-triage`** — the agent runs the tool, applies triage rules
  (urgent / this week / when-you-have-time / for-your-records), and
  hands back an action list. Useful as a Monday-morning kickoff.

See [`skills/README.md`](skills/README.md) for installation and
additional candidate skills (`pc-resume`, `pc-weekly`).

## Reference

```sh
project-commander                                  # show subcommand help
project-commander report                           # all projects
project-commander report --since N                 # only projects active in last N days
project-commander report --limit N                 # cap to top N
project-commander report --project <glob>          # detail view (repeatable)
project-commander report --exclude <glob>          # hide matching folders (repeatable)
project-commander report --format json|markdown    # alternate output
project-commander report --disable <source>        # skip a source (repeatable)
project-commander report --root <path>             # scan a specific root (repeatable)
project-commander report --no-color                # plain output

project-commander tidy [--dry-run] [--sync] [--push] [--no-init] [--no-commit] [--stale-age N]
```

## Going deeper

- **How does it produce these reports?** A user-facing walkthrough
  with diagrams, the Report key, and the heuristics behind Progress
  and Intent: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Want to extend or contribute?** Module layout, scanner protocol,
  adding a new source, candidate-source roadmap, testing:
  [`AGENTS.md`](AGENTS.md).

## Privacy

`project-commander` reads only your local filesystem. No network
calls, no LLM calls, no telemetry. Reports are reproducible from
disk state.

## Limitations

- Gemini CLI keys sessions by workdir basename only; two repos with
  the same basename collide. A typical project folder rarely has same-name siblings.
- Kiro's chat-history file is usually a near-empty LokiJS database;
  we use file mtime as the activity signal.
- OpenCode prompt content lives in `~/.claude/transcripts/`. If
  those are pruned, only session-level activity is reported.
- Intent prefers explicit docs. A stale `README.md` will dominate
  the reported intent — which is exactly when `Drifting` fires.
  Use `--project NAME` for the detail view when you suspect drift.
