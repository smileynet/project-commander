# Architecture

`project-commander` answers one question: **what is the state of every
project in `~/code` right now?**

It does that by reading every place a project leaves a trail — git
history, agent conversation logs across seven coding tools, and plan
docs in the repo itself — folding all of it into one record per
project, then rendering that record in whichever shape you asked for.

This document describes what the tool gives you and how it produces
each piece. For module-level internals, read the source.

## Report key

A row in the fleet table looks like this:

```
  Project              Last active   Progress   Sources   Git       Intent
  ───────────────────  ────────────  ─────────  ────────  ────────  ─────────────────────
  cdda_improved        5h ago        Hot        DGOP      master*   Cataclysm: Dark Days…
                                                                    Currently: review state
```

### Sources column

Each letter means one tool has touched this project recently. The
order in the cell is alphabetical, not ranked.

```
  G   git              commits, branch, dirty flag
  C   Claude Code      prompts from ~/.claude/projects/
  M   geMini CLI       prompts from ~/.gemini/tmp/
  O   Oh-My-Pi         sessions from ~/.omp/agent/sessions/
  P   oPencode         joined from opencode storage + claude transcripts
  K   Kiro / Amazon Q  activity from ~/.aws/amazonq/history/
  D   Docs             PLAN.md / README.md / ROADMAP.md / etc. in the project
```

Reading examples:

```
  DGOP    docs + git + OMP + opencode have all touched this project
  CDMGOP  six tools converge here  →  expect a 'tool-cluster' flag
  G       only git activity        →  no agent-tool history found
  O       only OMP activity        →  not a git repo, but a real session lives here
  —       no signals at all        →  empty folder
```

### Git column

```
  main           clean tree, on main branch
  main*          uncommitted changes  (the * triggers the 'dirty-tree' flag)
  feature/x*     dirty work-in-flight on a feature branch
  (detached)     no current branch (detached HEAD)
  —              not a git repo
```

### Last active

Resolves to the most recent observed event from any source. Coarsens
as it ages — `0m`, `5h`, `3d`, `4w`, then ISO date past a year.

### Intent

`<purpose> Currently: <focus>` — purpose pulled from the project's
plan doc, focus pulled from the most recent substantive prompt. See
[Intent](#intent-purpose--focus) below.

### Progress

One word per project. See [Progress](#progress-where-the-project-is-in-its-lifecycle)
below for the full vocabulary.

## What you get

Three views, same underlying data, different shapes.

```
  $ project-commander                            →  fleet table (default)
  $ project-commander --project cdda_*           →  per-project detail
  $ project-commander --format json | markdown   →  structured / shareable
```

| View | Best for |
|---|---|
| **Fleet table** | *"Which projects am I active on, and what was I doing?"* — one row per project, sorted by recency. |
| **Project detail** | *"Show me the receipts for one project"* — full audit trail with progress summary, purpose, focus, flags, evidence, activity stats, and recent commits / prompts / sessions / plan-doc edits. |
| **JSON / markdown** | Scripting downstream, or sharing a static report. JSON includes both the interpreted observations and the raw signals. |

## How signals fuse into a report

For every project under `~/code`, seven scanners read disk and emit
observations called `Signal`s. The aggregator collects them, the
observations layer interprets them, and a renderer prints them.

```
              ┌─ git log ──────────────────────────────────┐
              │                                            │
              ├─ ~/.claude/projects/<encoded-cwd>/*.jsonl ─┤
              │                                            │
              ├─ ~/.gemini/tmp/<basename>/{logs,chats} ────┤
              │                                            │
   ~/code/foo ├─ ~/.omp/agent/sessions/-code-foo/*.jsonl ──┤  →  list[Signal]
              │                                            │     each with:
              ├─ opencode storage  +  claude transcripts ──┤       source
              │                                            │       kind
              ├─ ~/.aws/amazonq/history/<md5(path)>.json ──┤       timestamp (UTC)
              │                                            │       summary
              └─ PLAN.md / README.md / ROADMAP.md / ... ───┘       ref
                                                                    │
                                                                    ▼
                                                         ┌──────────────────────┐
                                                         │     ProjectReport    │
                                                         │  ──  signals[]       │
                                                         │  ──  branch, dirty   │
                                                         │  ──  last_active     │
                                                         └──────────┬───────────┘
                                                                    │
                                                          observations.build()
                                                                    │
                                                                    ▼
                                                         ┌──────────────────────┐
                                                         │     Observations     │
                                                         │  ──  progress (enum) │
                                                         │  ──  purpose / focus │
                                                         │  ──  intent          │
                                                         │  ──  flags           │
                                                         │  ──  evidence        │
                                                         │  ──  7d/30d/90d      │
                                                         └──────────┬───────────┘
                                                                    │
                                            ┌───────────────────────┼───────────────────────┐
                                            ▼                       ▼                       ▼
                                       fleet table           project detail            json / markdown
```

The seven signal sources, in detail:

| Source | Reads | Contributes |
|---|---|---|
| **git** | The repo itself | Branch, dirty flag, last 10 commits with subjects + timestamps. The objective record. |
| **Claude Code** | `~/.claude/projects/<encoded-cwd>/*.jsonl` | Prompts you typed at Claude in this directory. The *intent* signal — what you asked for, in your words. |
| **Gemini CLI** | `~/.gemini/tmp/<basename>/{logs.json,chats/}` | Prompts and chats from Gemini sessions. |
| **Oh-My-Pi** | `~/.omp/agent/sessions/-code-<name>/*.jsonl` | OMP harness session prompts. |
| **OpenCode** | `~/.local/share/opencode/storage/` ⨝ `~/.claude/transcripts/` | OpenCode session prompts; the storage JSON tells us which session belonged to which working directory. |
| **Kiro / Amazon Q** | `~/.aws/amazonq/history/chat-history-<md5(abspath)>.json` | Kiro chat-history file mtime as activity signal. |
| **Docs** | `PLAN.md`, `README.md`, `ROADMAP.md`, `NEXT_STEPS.md`, `IMPROVEMENTS.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `TODO.md` | The *purpose* signal, plus mtime + done-phrase detection for plan-drift. |

## Intent: purpose + focus

The `Intent` cell composes two layers:

```
  PURPOSE                              FOCUS
  what the project is FOR              what it is doing RIGHT NOW
  ──────────────────────────           ─────────────────────────────
  highest-authority plan doc,    +     latest non-procedural prompt
  first prose paragraph                within the last 30 days
       │                                       │
       └───────────────┬───────────────────────┘
                       ▼
            "<purpose>  Currently: <focus>"
```

Plan-doc authority is a fixed score. The first file that exists in
the project takes the purpose slot:

```
  PLAN.md   →   README.md   →   ROADMAP.md   →   NEXT_STEPS.md   →   IMPROVEMENTS.md
   100            90              85                 80                  75
                                                                          │
            AGENTS.md   →   CLAUDE.md / GEMINI.md   →   TODO.md   ◄───────┘
              70                  65                       60
```

"Substantive" means *not* a one-word approval. Procedural prompts
(`yes`, `proceed`, `ok`, `next`, `continue`, `go`, `do it`, `retry`)
are filtered out and surfaced separately as a flag — see below.

## Progress: where the project is in its lifecycle

Eleven states. Most flow naturally along a recency axis; four are
special states that depend on plan-doc state and signal mix.

### Recency-driven states

```
  Recency:   today           7d            7–30d                 30–90d        90d+
  ───────────┬───────────────┬─────────────┬─────────────────────┬─────────────┬─────
             │               │             │                     │             │
         ┌───▼────┐          │             │                     │             │
         │  Hot   │          │             │                     │             │
         └────────┘          │             │                     │             │
                       ┌─────▼────┐        │                     │             │
                       │  Active  │        │                     │             │
                       └──────────┘        │                     │             │
                                    ┌──────▼─────┐               │             │
                                    │   Paused   │  ←  if dirty tree           │
                                    │            │     or in-flight prompts    │
                                    │  Cooling   │  ←  otherwise               │
                                    └────────────┘               │             │
                                                            ┌────▼────┐        │
                                                            │  Idle   │        │
                                                            └─────────┘        │
                                                                          ┌────▼────┐
                                                                          │ Dormant │
                                                                          └─────────┘
```

### Special states (set by plan + signal mix, not recency)

```
  Drifting    plan doc says "completed"  but commits keep landing
              →  the plan is lying; review intent

  Shipped     plan doc says "completed"  +  clean tree  +  no fresh prompts
              →  properly landed

  Tracking    commits on a non-main branch  with zero prompts
              →  upstream sync / mirror fork

  Stub        documentation only           no commits, no prompts
              →  README on its own

  Empty       no signals observed at all
              →  truly nothing
```

Each state ships with a one-line summary that names concrete numbers,
e.g. *"Touched today across 5 day(s); 10 commit(s), 12 prompt(s) this
week."*

## Flags: things worth your attention

Flags surface conditions you'd otherwise have to spot manually.

```
  dirty-tree                   uncommitted changes in the repo
  plan-drift                   plan declares done + commits after the doc's mtime
  tool-cluster                 ≥4 different tools have touched this project
  upstream-only                commits on a non-main branch with zero prompts
  no-docs                      no plan doc in any of the recognized names
  procedural-prompts           every recent prompt is yes/proceed/ok/etc.
  prompt-injection-detected    recent prompts probe for system-prompt extraction
```

`procedural-prompts` is *not* a defect — it tells you *"I'm approving
an agent here, not directing it."* Useful as a usage-shape signal.

## Evidence: every claim is auditable

The detail view always ends with a one-line evidence trail:

```
  Evidence: doc:PLAN.md; recent prompt within 0d;
            last action: git:commit;
            plan-drift: doc says complete, 5 commits since
```

Every interpreted line in the report can be traced back to specific
signals. If the headline says **Drifting**, the evidence says *which*
doc, *which* phrase triggered it, and *how many* commits came after.

## End-to-end runtime

What happens, in order, when you run the tool:

```
  $ project-commander  [--since N]  [--project glob]  [--format json|markdown]
                       [--exclude glob]  [--limit N]  [--disable source]
       │
       │  1.  discover
       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  walk ~/code, drop hidden dirs                                          │
  │  apply --project / --exclude basename globs                             │
  └─────────────────────────────────────────────────────────────────────────┘
       │
       │  2.  fan out  (ThreadPoolExecutor, 8 workers, one task per project)
       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  for each project, in parallel:                                         │
  │      run all 7 scanners  →  list[Signal]                                │
  │      fold into ProjectReport                                            │
  │      observations.build()  →  Observations                              │
  └─────────────────────────────────────────────────────────────────────────┘
       │
       │  3.  filter
       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  apply --since (recency cutoff) and --limit (top N)                     │
  └─────────────────────────────────────────────────────────────────────────┘
       │
       │  4.  render
       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │      default                       →  rich table to stdout             │
  │      --project                     →  per-project detail view          │
  │      --project + --format markdown →  per-project markdown report      │
  │      --format json | markdown      →  structured / shareable output    │
  └─────────────────────────────────────────────────────────────────────────┘
```

Three behavioral guarantees:

- **Reads from disk every run.** No cache, no daemon, no database.
  If the report changes, something on disk changed.
- **Heuristics, not LLMs.** All intent and progress detection is
  rule-based. Same input, same report. Fast, free, offline.
- **One bad source can't break a report.** Per-source and per-project
  failures are caught and surfaced as a single failure row instead of
  taking down the whole run.

## What you can ask for

```
  project-commander                       all projects, sorted by recency
  project-commander --since 7             only projects active in the last week
  project-commander --limit 20            top 20 most-recent
  project-commander --project cdda_*      detail view for matching folders
  project-commander --exclude pi-*        hide noisy folders
  project-commander --format json         structured output
  project-commander --format markdown     shareable report
  project-commander --disable kiro        skip a source you don't use
  project-commander --root /other/path    scan somewhere other than ~/code
```

`--project`, `--exclude`, `--disable` repeat. Globs are basename
matches.

## Adding a new source

If you adopt another coding tool, you can teach `project-commander`
to read it without touching the existing scanners.

```
   sources/<your-tool>.py
        │
        │  emit list[Signal] from disk
        ▼
   register in cli.py     +     pick a single-letter flag in report._SRC_FLAGS
        │                                       │
        └───────────────────┬───────────────────┘
                            ▼
        the fleet table, detail view, observations layer,
        and JSON output pick it up automatically — they
        group by (source, kind) without caring which tools
        happen to be present.
```

Each `Signal` declares its `kind` (`commit` / `prompt` / `doc` /
`session` / `filesystem`) so the renderer knows which section to put
it in. Adding a new kind is a deliberate model change — every reader
must decide how to handle it, which is the point.

## Candidate signal sources

The seven scanners shipped today cover git + the major agent CLIs +
in-tree docs. There are still high-value signal sources outside that
set; this section names them so anyone implementing one (or evaluating
whether to) starts with the same map.

### bd (beads) — graph issue tracker

[`bd`](https://github.com/gastownhall/beads) is a distributed graph
issue tracker designed for coding agents. Several projects under
`~/code` already use it (`bd onboard` is the convention); the global
`AGENTS.md` references it as the issue-tracking tool of choice.

**Why it matters here.** Agent prompts tell us what the user *asked
for*; commits tell us what *landed*. `bd` fills the gap between those
two: what work is *planned*, what is *in flight*, and what is *done* —
structured, with timestamps, dependencies, and priorities. That is
exactly the layer `project-commander` currently has to *infer* from
prompt + commit cadence.

**Where the data lives.** Per project:

```
  <project>/.beads/embeddeddolt/   default (embedded Dolt DB)
  <project>/.beads/dolt/           server mode
```

Discovery is trivial: presence of `<project>/.beads/` tells us this
project uses bd.

**What a `BdScanner` would emit.**

```
  Signal(source="bd", kind="task",        ...)   # one per open issue
  Signal(source="bd", kind="task_closed", ...)   # one per recent close
  Signal(source="bd", kind="task_active", ...)   # currently in_progress
```

`task` is a new `SignalKind`. Adding it is intentional — it forces the
renderer and observations layer to decide how to display it.
Provisional plan:

- **`Observations.purpose` / `focus`**: an open `bd` issue with the
  highest priority becomes a strong focus candidate, often more
  reliable than the latest prompt.
- **Activity windows**: closed-task counts feed the same
  `commits/prompts/sessions` triplet, exposed as a fourth column.
- **New flag `blocked`**: at least one open issue with status
  `blocked` and no movement in 14 days.
- **New progress state `Stalled`**: open `bd` ready-queue is empty
  *and* no commits in 30 days — work is not being created and not
  being shipped.

**Implementation outline.** `bd` ships JSON output (`bd list --json`,
`bd ready --json`, `bd stats --json`). The scanner shells out from the
project root, parses JSON, emits one signal per task. Falls back to
reading the Dolt database directly only if the CLI is unavailable
(unlikely — if `.beads/` exists, the user has bd installed).

Single-letter flag: **`B`** for `bd` in the `Sources` column.

### Other candidates worth considering

```
  GitHub issues / PRs        per-project remote API; needs auth + rate-limit
  Linear / Jira              org-level trackers; per-project filter required
  shell history              ~/.zsh_history grep for `cd <project>` jumps
  filesystem activity        recent file mtimes outside git history
  test-run history           pytest/jest cache files indicate "last test run"
```

The bar for inclusion is the same as the existing seven: a clean
per-project key, structured timestamps, and a story the renderer
can tell that the existing sources cannot.