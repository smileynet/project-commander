# Architecture

`project-commander` answers one question: **what is the state of every
project in your project folder(s) right now?**

It does that by reading every place a project leaves a trail — git
history, agent conversation logs across seven coding tools, and plan
docs in the repo itself — folding all of it into one record per
project, then rendering that record in whichever shape you asked for.

This document describes what the tool gives you and how it produces
each piece. For module-level internals, read the source.

## Report key

Rows are grouped by attention band — *Needs attention* first, then *Active*,
then *Quiet*. Within a band, rows sort by recency.

```
  Project              State          Outstanding   Sources   Git       Intent
  ───────────────────  ─────────────  ────────────  ────────  ────────  ─────────────────────
  cdda_improved        Hot · 7h        dirty 13      DGOP      master*   Cataclysm: Dark Days Ahead is a turn-based…
```

### State column

Combines progress and last-active recency into one cell, formatted
`{progress} · {age}`. Frees a column for Outstanding while preserving
both axes.

```
  Hot · 35m       Hot, last touched 35 minutes ago
  Active · 1d     Active, last touched 1 day ago
  Drifting · 4d   Drifting, last touched 4 days ago
  Idle · 4w       Idle, last touched ~4 weeks ago
```

Color encodes attention pressure, not just progress: a clean *Hot*
renders calmer than *Drifting* or *Hot + dirty*.

### Outstanding column

What needs to happen on this project, in one token. Pulled from git
state and plan-doc structure; this column drives the *Next* line in
the detail view.

```
  dirty N         N uncommitted files in the work tree
  ahead N         N commits HEAD has that the upstream tracking ref lacks
  behind N        N commits the upstream has that HEAD lacks
  plan N          N unchecked items in the highest-authority plan doc
  phase N         N plan-doc phases not yet marked complete
  orphan          most recent substantive prompt has no follow-up commit
  —               clean: no uncommitted work, no unpushed commits, no orphan thread
```

Headline priority is dirty > ahead > behind > plan > phase > orphan.
If multiple conditions hold, the most-actionable one wins; the
detail view always shows the full list.

### Sources column

Each letter means one tool has touched this project recently. The
order in the cell is alphabetical, not ranked.

```
  G   git              commits, branch, dirty flag, ahead/behind
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

### Intent

`<purpose> Currently: <focus>` — purpose pulled from the project's
highest-authority plan doc, focus pulled from the most recent
substantive prompt. Markdown chrome (callouts, blockquote markers,
HTML, inline metadata prefixes) is stripped before display so the
cell reads as plain prose. Truncation lands at a sentence boundary,
not a character count. See [Intent](#intent-purpose--focus) below.

### Progress

One word per project. See [Progress](#progress-where-the-project-is-in-its-lifecycle)
below for the full vocabulary.

## Where it scans

There is no single "the" project folder. Both `report` and `tidy`
resolve roots in this order, taking the first source that yields
any:

```
  1.  --root <path>             repeatable on the command line
  2.  $PROJECT_COMMANDER_ROOTS  os.pathsep-separated paths
                                (":" on POSIX, ";" on Windows)
  3.  auto-detect under $HOME   every existing folder named
                                code, projects, src, dev, work,
                                repos, git, plus the macOS-cased
                                Code, Projects, Dev
```

Auto-detect returns *every* matching folder, in declared order —
a user with both `~/code` and `~/work` gets both scanned by default.
Projects are deduped by absolute path, so symlinks pointing at the
same physical directory yield one entry, not two.

If nothing resolves (no flag, no env var, none of the conventional
folders exist), the command exits with a friendly error pointing
at the override knobs.


## What you get

Three views, same underlying data, different shapes.

```
  $ project-commander report                     →  fleet table (default)
  $ project-commander report --project cdda_*           →  per-project detail
  $ project-commander report --format json | markdown   →  structured / shareable
```

| View | Best for |
|---|---|
| **Fleet table** | *"Which projects am I active on, and what was I doing?"* — one row per project, sorted by recency. |
| **Project detail** | *"Show me the receipts for one project"* — full audit trail with progress summary, purpose, focus, flags, evidence, activity stats, and recent commits / prompts / sessions / plan-doc edits. |
| **JSON / markdown** | Scripting downstream, or sharing a static report. JSON includes both the interpreted observations and the raw signals. |

## How signals fuse into a report

For every project under your project root(s), seven scanners read disk and emit
observations called `Signal`s. The aggregator collects them, the
observations layer interprets them, and a renderer prints them.

```
              ┌─ git log ──────────────────────────────────┐
              │                                            │
              ├─ ~/.claude/projects/<encoded-cwd>/*.jsonl ─┤
              │                                            │
              ├─ ~/.gemini/tmp/<basename>/{logs,chats} ────┤
              │                                            │
  <root>/foo  ├─ ~/.omp/agent/sessions/-code-foo/*.jsonl ──┤  →  list[Signal]
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

## Outstanding: what still needs to happen

Where flags describe a *condition*, *Outstanding* describes the
*work*. The detail view always carries an Outstanding block:

```
  Outstanding
    git      8 uncommitted file(s) (M src/foo.py, A tests/x.py, ...)
             branch main is 1 commit ahead of origin/main
    plan     PLAN.md: 3 unchecked item(s); next: "Add CI workflow"
    thread   last prompt 24h ago has no follow-up commit

  Next: Commit 8 uncommitted file(s). Then push 1 commit to origin/main.
```

Sources, in order:

- **git**: `status --porcelain` for uncommitted files (count + first 3
  examples), `rev-list --left-right --count <upstream>...HEAD` for
  ahead/behind once an upstream tracking ref exists.
- **plan**: `PLAN.md` / `NEXT_STEPS.md` / `TODO.md` /
  `IMPROVEMENTS.md` / `ROADMAP.md` are parsed for `- [ ]` checkboxes
  and roman-numeral / `Phase N` headings. The doc with the highest
  authority score wins the *next-item* slot.
- **thread**: latest substantive (non-procedural) prompt is
  *orphaned* if it sits 4 hours to 7 days old with no commit landing
  after it. Below 4 hours we assume mid-conversation; past 7 days the
  thread is just history.

## Next: the synthesized step

Both the detail view and the markdown handoff end with a `Next:`
line that turns the Outstanding block into a single imperative
sentence:

```
  drift fired              →  "Reconcile PLAN.md: it says complete but 5 commits
                                have landed since. Update or remove the completion
                                marker."
  no git, has signals      →  "Run `project-commander tidy` to init this folder
                                as a git repo."
  dirty + ahead            →  "Commit N uncommitted file(s). Then push M commit(s)
                                to origin/main."
  clean Hot/Active + plan  →  "Pick up the next plan item (PLAN.md): …"
  Paused                   →  "Decide whether to resume or stash this project."
  Shipped / clean          →  "No action — shipped and clean."
```

Drift always wins: the plan needs reconciliation before any other
work matters.

## Period in review (`--since N`)

When `--since` is set with `N ≤ 30`, the report switches from a
fleet table to a four-section digest tuned to the Monday-morning
JTBD:

```
  Last 7 day(s) — since 2026-04-19

  Moved forward (5)
    project-commander       10c 8p   multi-root cleanup
    cdda_improved            5c 12p  review state of project
    …

  Parked dirty (decide) (3)
    PrusaSlicer              dirty 6f   idle 22d
    agentic_enterprise       dirty 31f  idle 52d
    alpha                    no-git     idle  9d

  Flagged (1)
    best_practices           plan-drift  doc says complete, 5 commits since

  New this period (1)
    hey-ate-training         first Tue   first commit landed during the window
```

Each section answers a different question:

- **Moved forward** — *what did I touch?* commits + non-procedural
  prompts during the window, sorted by total activity
- **Parked dirty** — *what needs a decision?* dirty trees idle for \u2265 2
  days, plus folders with content but no git
- **Flagged** — *what's anomalous?* plan-drift, prompt-injection-detected,
  upstream-only, tool-cluster
- **New this period** — *what's new?* projects whose first observed
  commit landed inside the window

Each section caps at 12 rows with `… +N more` overflow. JSON and
markdown output formats keep the flat fleet shape regardless of
`--since` so downstream consumers stay simple.


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

## Tidy: hygiene actions

`project-commander tidy` is the second top-level subcommand. Where
`report` only reads, `tidy` writes — it applies four narrowly-scoped
git-hygiene actions that catch the things that slip through the
cracks of an active project folder:

```
  init           folder has commit-worthy content but no git repo
                 →  git init  +  git add  +  git commit -m "Initial import"

  commit-stale   git repo with dirty tree, no activity for >= --stale-age days
                 →  git add -A  +  git commit -m "Checkpoint stale work-in-progress"

  fetch          (--sync only)  refresh remotes without merging
                 →  git fetch --all --prune

  push           (--push only)  push branches whose unpushed range
                 contains zero hygiene commits
                 →  git push <upstream-tracking-ref>
```

`init` and `commit-stale` are on by default; `--sync` and `--push`
are explicit opt-ins because they touch the network and remote
state. `--no-init` / `--no-commit` turn the defaults off; `--dry-run`
shows the plan without executing.

Every commit `tidy` makes carries a trailer:

```
  Project-Commander-Hygiene: true
```

This trailer is the boundary between work and housekeeping. The
`push` action refuses any branch whose `upstream..HEAD` range
contains a hygiene commit, so a `commit-stale` checkpoint never
lands on a remote without you noticing. The trailer is also how
`tidy` will, in the future, distinguish its own commits from yours
for any further sweep logic.

Plan and execute are split for testability. `tidy.plan(report,
config, now)` is a pure function returning `list[PlannedAction]`;
`tidy.execute_init / execute_commit_stale / execute_fetch /
execute_push` perform the side effects. Tests exercise the planner
with synthetic reports and exercise the push refusal against a real
local bare repo + clone, no network involved.

## End-to-end runtime

What happens, in order, when you run `project-commander report`:

```
  $ project-commander report  [--since N] [--project glob] [--exclude glob]
                              [--limit N] [--format json|markdown]
                              [--disable source] [--root <path>]
       │
       │  0.  resolve roots
       ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │  --root flags > $PROJECT_COMMANDER_ROOTS > $HOME auto-detect           │
  │  dedup by absolute path                                                │
  └────────────────────────────────────────────────────────────────────────┘
       │
       │  1.  discover
       ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │  walk each root, drop hidden dirs                                      │
  │  apply --project / --exclude basename globs                            │
  └────────────────────────────────────────────────────────────────────────┘
       │
       │  2.  fan out  (ThreadPoolExecutor, 8 workers, one task per project)
       ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │  for each project, in parallel:                                       │
  │      run all 7 scanners  →  list[Signal]                              │
  │      fold into ProjectReport                                          │
  │      observations.build()  →  Observations                            │
  └───────────────────────────────────────────────────────────────────────┘
       │
       │  3.  filter
       ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │  apply --since (recency cutoff) and --limit (top N)                   │
  └───────────────────────────────────────────────────────────────────────┘
       │
       │  4.  render
       ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │      default                       →  rich table to stdout            │
  │      --project                     →  per-project detail view         │
  │      --project + --format markdown →  per-project markdown report     │
  │      --format json | markdown      →  structured / shareable output   │
  └───────────────────────────────────────────────────────────────────────┘
```

`tidy` runs a lighter pipeline: it reuses `discovery` to walk the
same roots, then runs only the **git scanner** plus a folder-mtime
check (no need to walk seven scanners to decide hygiene). Each
`PlannedAction` is rendered into a preview table; without `--dry-run`,
actions execute in order — init, then commit-stale, then optional
fetch, then optional push.

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
  # report — read only
  project-commander report                              all projects, sorted by recency
  project-commander report --since 7                    only projects active in the last week
  project-commander report --limit 20                   top 20 most-recent
  project-commander report --project cdda_*             detail view for matching folders
  project-commander report --exclude pi-*               hide noisy folders
  project-commander report --format json                structured output
  project-commander report --format markdown            shareable report
  project-commander report --disable kiro               skip a source you don't use
  project-commander report --root /other/path           scan a specific root (repeatable)

  # tidy — applies actions; --dry-run shows the plan first
  project-commander tidy --dry-run                      preview hygiene plan, do nothing
  project-commander tidy                                run init + commit-stale (default on)
  project-commander tidy --no-commit                    init only; never auto-commit dirty trees
  project-commander tidy --stale-age 14                 raise stale threshold from 7 to 14 days
  project-commander tidy --sync                         + git fetch --all per repo
  project-commander tidy --push                         + push branches with no hygiene commits

  # roots — same resolution policy for both subcommands
  project-commander report                              auto-detect $HOME conventions
  PROJECT_COMMANDER_ROOTS=~/work:~/clients pcmd report  scan two roots from env var
  pcmd tidy --root ~/work --root ~/personal             scan two roots from flags
```

`--project`, `--exclude`, `--disable` repeat. Globs are basename
matches.

## Where this lives in the repo

If you want to read or extend the code, the implementation map and
scanner protocol live in [`AGENTS.md`](../AGENTS.md) at the repo
root, alongside the procedural "how to add a new source" and the
ranked roadmap of candidate signal sources (`bd`, Cursor, Codex,
JetBrains, Aider, Cline, Continue.dev, plus filesystem and runtime
candidates). This document keeps the user-facing view of *how the
report is produced*; that one keeps the contributor-facing view of
*how the code is structured*.
