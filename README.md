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

The core is deterministic, offline, and reproducible from your local
filesystem. An optional LLM narrator can synthesize the briefing-card
prose and weekly recap when you have an API key or a local Ollama
running; if you don't, you get the same deterministic output as before.

## What you'll see

The default fleet view — one row per project, grouped by attention
band, sorted by recency:

```
  Project              State          Outstanding   Sources   Git       Intent
  ───────────────────  ─────────────  ────────────  ────────  ────────  ─────────────────────
  cdda_improved        Hot · 7h       dirty 13      DGOP      master*   Cataclysm: Dark Days Ahead is a turn-based…
  project-commander    Hot · 22m      dirty 8       DGOP      main*     The fleet check-in for developers…
  best_practices       Drifting · 4h  drift         GCD       main*     A reusable best-practices library…
  catalyst             Idle · 41w     —             G         main      Fleet of cooperating agents…
```

The detail view (`report --project NAME`) opens with a 4-question
briefing card:

```
**Hot** · last touched 22h ago · `master*` · 13 uncommitted file(s)

### What is it?
Cataclysm: Dark Days Ahead is a turn-based survival game…

### What's been happening?
Recent commits focused on the regression-comparison tool, item-scenario
data extraction from the monolith, and high-item profiling scenarios…

### Where it stands
Working tree has 13 uncommitted file(s). Last substantive prompt was 73h
ago with no follow-up commit.

### What's planned next
Per `.sisyphus/plans/optimal-plan-forward.md`: clean the working tree,
repair the benchmark methodology gap, then continue the now-stable
profiling pipeline…

**Your first action:** Commit 13 uncommitted file(s).

---
<sub>Inspect: plan `.sisyphus/plans/optimal-plan-forward.md` · last commit `2026-04-23` · last prompt `2026-04-24` (`opencode`) · identity `README.md` · working tree (13 files)</sub>
```

The headings are reader questions, the answers are synthesized prose,
and the inspect footer points at the source files so anything claimed
above is one click away from verification.

## Install

```sh
pip install -e .
```

Python ≥ 3.10. The only runtime dependency is
[`rich`](https://rich.readthedocs.io). LLM narration uses `urllib`
from the stdlib — no SDK install needed.

## First run

```sh
project-commander report
```

Auto-detects your project folder (any of `~/code`, `~/projects`,
`~/src`, `~/dev`, `~/work`, `~/repos`, `~/git`, plus the macOS-cased
variants), reads your tool storage, prints the table. No daemon, no
config file. Override with `--root <path>` or set
`PROJECT_COMMANDER_ROOTS=path1:path2` if your projects live somewhere
else — or in more than one place.

## Subcommands

```sh
project-commander {report,tidy,catchup,verify,audit,recap}
```

| Subcommand | Job |
|---|---|
| `report` | Survey your project folders. Default is the fleet table; `--project NAME` opens the detail card; `--since N` (N ≤ 30) switches to the weekly review with cross-project recap. |
| `tidy` | Apply hygiene actions: init missing repos, checkpoint stale dirty trees, optionally fetch/push, optionally archive dormant clean projects with `--prune`. |
| `catchup` | Show what changed since you last ran `catchup`. Persists a cursor at `~/.local/state/project-commander/`. Three sections: agent activity while you were away, upstream commits you're behind on, your own work since last check. |
| `verify` | Five named PASS/FAIL closure checks per project. JSON output with non-zero exit on any FAIL — suitable for agent chaining or pre-handoff gates. |
| `audit` | Prompt-to-commit causality for a project: which substantive prompts converted into landed code, which orphaned, the prompt-to-commit ratio, and flags like `all-orphans` or `plan-drift`. |
| `recap` | Per-project retrospective narrative across `--quarter`, `--year`, `--month`, or `--since N`. Categorizes activity (shipped / major arc / started but paused / quiet) and synthesizes a paragraph per project. |

## How it shows up in your workflow

**Monday morning, no idea what's in flight.**

```sh
project-commander report --since 7
```

The 7-day weekly review opens with a 3-5 sentence recap of what
shipped, what stalled, and any cross-project themes (LLM-synthesized
when a provider is available; deterministic otherwise). Three exclusive
triage sections follow: *Needs your attention*, *New this week*, *Moved
this week*. Dirty trees flag themselves; `Drifting` rows are the
projects where plan and reality diverged.

**Coming back to a project after a break.**

```sh
project-commander report --project cdda_improved
```

The detail card answers four reader questions (what it is, what's
been happening, where it stands, what's planned next), synthesized
from git, every agent's prompt history, and the project's own plan
docs. By the time you finish reading you should know whether to
dive in or close the tab.

**End-of-week status update.**

```sh
project-commander report --since 14 --format markdown > review.md
```

Substantive prompts are kept; bare approvals (`yes`, `proceed`) are
collapsed so the record reads like work notes, not a chat log.

**"What did I get done this quarter?"**

```sh
project-commander recap --quarter
```

One paragraph per active project, grouped Shipped / Major arcs /
Started but paused / Quiet. Same windowing for `--year`, `--month`,
or `--since N`.

**"What changed since I last checked in?"**

```sh
project-commander catchup
```

Persists a cursor at `~/.local/state/project-commander/catchup-cursor.txt`.
Each run shows deltas since the last invocation, then advances the
cursor. `--since 6h` previews a window without advancing.
`--reset-cursor` starts fresh.

**Cleaning house in your project folder.**

```sh
project-commander tidy --dry-run                  # show what would change
project-commander tidy                            # do it
project-commander tidy --sync                     # also `git fetch` everywhere
project-commander tidy --push                     # push branches without hygiene commits
project-commander tidy --prune --dry-run          # find dormant clean projects to archive
```

By default `tidy` runs two opinionated, safe-by-design actions across
every configured project root:

- **Init repos that aren't repos.** A folder with content but no
  `.git/` gets `git init` + an initial commit, tagged with the
  hygiene trailer.
- **Checkpoint stale dirty trees.** Working trees dirty for
  `--stale-age` days (default 7) get one auto-commit, marked as a
  hygiene checkpoint.

Every commit `tidy` makes carries a `Project-Commander-Hygiene: true`
trailer. That's the boundary between *work you did* and *housekeeping
the tool did*. The push policy honors it: even with `--push`, the
tool **refuses to push any branch that has hygiene commits in its
unpushed range** — only your real work gets pushed.

`--prune` adds two opt-in actions: ARCHIVE moves clean projects
dormant ≥ `--prune-age` days (default 90) into
`<project.parent>/archive/<name>/` (override with `--archive-dir`).
Dirty or ahead-of-upstream projects get a HOLD action so you can
resolve them first.

**Pre-handoff or agent-chained closure check.**

```sh
project-commander verify --project NAME --format json
```

Five named checks per project: `working_tree_clean`, `branch_in_sync`,
`no_orphan_thread`, `no_plan_drift`, `prompts_substantive`. Single-project
runs emit one JSON object; fleet runs emit an array. Exit code 1 on any
FAIL, so scripts can chain `tidy && verify` as a gate.

**"Are my agent sessions actually shipping code?"**

```sh
project-commander audit --project NAME --since 30
```

For each substantive prompt in the window, look forward 24 hours for a
follow-up commit. Surfaces prompt-to-commit ratio + flags like
`all-orphans`, `high-prompt-volume`, `plan-drift`,
`prompt-injection-detected`. Procedural approvals (yes/proceed) are
counted separately and never count as causality.

## Optional: agent-synthesized prose

Three places benefit from a real narrative:

1. The detail card's *What is it / What's been happening / What's
   planned next* sections.
2. The weekly review's *Week in review* paragraph (one call per
   invocation, cross-project, surfaces themes a one-liner-per-row
   table can't).
3. The `recap` per-project paragraphs.

The deterministic synthesis hits a ceiling on those — distinguishing
*shipped* from *attempted*, surfacing contradictions between plan
claims and commit reality, and naming themes that span projects all
benefit from an LLM. So the tool will use one when it can find one,
and stay deterministic when it can't.

| Provider | Trigger | Default model |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-latest` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` (JSON mode) |
| Ollama | `OLLAMA_HOST` reachable, defaults to `http://localhost:11434` | `llama3.1` |

Auto-detection order: Anthropic → OpenAI → Ollama → disabled. Override
with `--llm-provider {auto,anthropic,openai,ollama,none}` or
`--llm-model NAME`, or the env equivalents
`PROJECT_COMMANDER_LLM` / `PROJECT_COMMANDER_LLM_MODEL`.

When narration is used, the inspect footer prepends `_synthesized prose_`
so you know the body was LLM-generated. To force deterministic synthesis
on any surface, pass `--no-llm`.

Failure paths all fall back to deterministic output: no provider
configured, HTTP error / timeout, malformed JSON. Errors print to
stderr only when `PROJECT_COMMANDER_LLM_VERBOSE=1`.

Output is content-addressed cached at
`$XDG_CACHE_HOME/project-commander/narrative/` (typically `~/.cache/`).
Cache key is `SHA-256(prompt + model_id)`; any change to a project's
commits, prompts, or docs invalidates the entry automatically.

## Run it the easy way with just

A `justfile` ships at the repo root:

```sh
just                  # list available recipes
just smoke            # quick fleet view (last 7 days)
just report           # fleet markdown report → reports/
just detail NAME=foo  # detail markdown for one project → reports/foo.md
just publish          # regenerate the full report set
just test             # run pytest
```

`reports/` is gitignored. It is generated from your private project
activity — not source-controlled.

## Skills for your agent harness

If you run an OMP- or cavekit-style harness, `skills/` ships an
installable skill bundle:

- **`pc-triage`** — agent runs the tool, applies triage rules
  (urgent / this week / when-you-have-time / for-your-records),
  hands back a deterministic action list. Useful as a Monday-morning
  kickoff.

See [`skills/README.md`](skills/README.md) for installation.

## Reference

```sh
project-commander                                # show subcommand help

# report — read only
project-commander report                         # fleet table
project-commander report --project <glob>        # detail card (repeatable)
project-commander report --since N               # weekly review (N ≤ 30) or filtered fleet
project-commander report --limit N               # cap to top N
project-commander report --exclude <glob>        # hide matching folders (repeatable)
project-commander report --format json|markdown  # alternate output
project-commander report --disable <source>      # skip a source (repeatable)
project-commander report --root <path>           # scan a specific root (repeatable)
project-commander report --no-color              # plain output
project-commander report --no-llm                # force deterministic synthesis
project-commander report --llm-provider X        # auto|anthropic|openai|ollama|none
project-commander report --llm-model NAME        # override the LLM model id

# tidy — applies actions; --dry-run shows the plan first
project-commander tidy [--dry-run] [--no-init] [--no-commit]
                       [--stale-age N] [--sync] [--push]
                       [--prune] [--prune-age N] [--archive-dir PATH]

# catchup — delta digest since persisted cursor
project-commander catchup [--since 6h|2d|30m|1w] [--reset-cursor]
                          [--no-advance] [--format terminal|markdown]

# verify — closure checks; non-zero exit on FAIL
project-commander verify [--project NAME] [--format terminal|json]

# audit — prompt → commit causality
project-commander audit --project NAME [--since N] [--format terminal|markdown]

# recap — per-period narrative
project-commander recap [--quarter|--year|--month|--since N]
                        [--format terminal|markdown]
```

`--project`, `--exclude`, `--disable`, `--root` repeat. Globs are
basename matches. The LLM flags above are accepted by every subcommand
but only consulted on the surfaces that actually narrate (`report
--project`, `report --since`, `recap`).

## Going deeper

- **How does it produce these reports?** A user-facing walkthrough
  with diagrams, the report key, and the heuristics behind Progress
  and Intent: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Want to extend or contribute?** Module layout, scanner protocol,
  adding a new source, candidate-source roadmap, testing:
  [`AGENTS.md`](AGENTS.md).

## Privacy

The deterministic core reads only your local filesystem. No network
calls, no telemetry, reports reproducible from disk state.

The optional LLM narrator is opt-in via environment / flags. When
enabled it sends to whichever provider you configured: capped excerpts
of identity files (README/AGENTS first ~600 chars × 2 sources), the
plan-doc excerpt (first ~1000 chars), recent commit subjects with
dates (≤ 30), and recent substantive prompt subjects with dates
(≤ 15). Procedural one-word approvals (`yes`, `proceed`) are filtered
out before sending. No raw chat transcripts, no full file contents,
no project file trees. Cache lives locally under `$XDG_CACHE_HOME`.

Pass `--no-llm` to disable narration entirely on any surface.

## Limitations

- Gemini CLI keys sessions by workdir basename only; two repos with
  the same basename collide. Rare in a typical project folder.
- Kiro's chat-history file is usually a near-empty LokiJS database;
  we use file mtime as the activity signal.
- OpenCode prompt content lives in `~/.claude/transcripts/`. If those
  are pruned, only session-level activity is reported.
- Identity prefers explicit docs. A stale `README.md` will dominate
  the reported identity — which is exactly when `Drifting` fires.
  Use `--project NAME` for the detail view when you suspect drift.
- LLM narration is best-effort. The inspect footer always points at
  source files so you can verify any synthesized claim.
