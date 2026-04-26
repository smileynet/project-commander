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
| **Project detail** | *"What was this work really about, what is unresolved, why did it stop here, and what do I do first?"* — a self-sufficient resume brief followed by an auditable trail of commits / prompts / sessions / plan-doc edits. |
| **JSON / markdown** | Scripting downstream, or sharing a static report. Plain fleet markdown stays tabular by default, but `--since N` now switches markdown into the same review-digest JTBD as the terminal output. JSON includes both the interpreted observations and the raw signals. |

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
│  ──  workstream      │
│  ──  open issue      │
│  ──  why stopped     │
│  ──  attention       │
│  ──  intent          │
│  ──  flags / evidence│
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
| **Docs** | `PLAN.md`, `README.md`, `ROADMAP.md`, `NEXT_STEPS.md`, `IMPROVEMENTS.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `TODO.md` | Stable project identity from README/ROADMAP-style docs, plus planning state / done-phrase detection for plan-drift and next-step synthesis. |

## Intent: identity + focus

The fleet-table `Intent` cell composes two layers:

```
  IDENTITY                             FOCUS
  what the project is FOR              what it is doing RIGHT NOW
  ──────────────────────────           ─────────────────────────────
  highest-authority identity doc, +    latest non-procedural prompt
  usually README / ROADMAP             within the last 30 days
       │                                       │
       └───────────────┬───────────────────────┘
                       ▼
            "<identity>  Currently: <focus>"
```

Identity authority is intentionally different from planning authority: README/ROADMAP-style docs win when present so the stable project identity does not get hijacked by the latest plan doc. Planning docs feed the detail report's `What's planned next` section.

"Substantive" means *not* a one-word approval. Procedural prompts (`yes`, `proceed`, `ok`, `next`, `continue`, `go`, `do it`, `retry`) are filtered out and surfaced separately as a flag \u2014 see below.

## The four-question briefing card

The detail view answers four reader questions, in order, with synthesized prose rather than evidence dumps. The headings *are* the questions, so the reader can skim:

1. **What is it?** \u2014 the project's stable identity, sourced from `README` / `ROADMAP` / `AGENTS` (planning docs are intentionally demoted here so the identity line does not flip every plan revision).
2. **What's been happening?** \u2014 a one-sentence synthesis of the last week of activity. Recent commits get topic-fragmented and joined; falls back to last concrete action or latest prompt when no commits landed.
3. **Where it stands** \u2014 the unresolved condition that explains the current situation: dirty tree, ahead/behind upstream, orphaned thread, plan-drift, or a clean checkpoint. One synthesized paragraph, not a bulleted evidence list.
4. **What's planned next** \u2014 the forward-looking direction, sourced from a plan doc when one exists. When workstream synthesis would only repeat \"What's been happening?\", the section degrades gracefully to a `_No plan doc found_` line.

Below those four sections, a `**Your first action:**` callout names the single safest next move (commit, push, pull, init, reconcile). The card closes with a one-line `<sub>Inspect: \u2026</sub>` footer that lists the plan doc, last commit, last prompt, identity doc, and working-tree state \u2014 a pointer trail rather than another evidence section.

This is the JTBD boundary: the reader sitting down to a half-remembered project gets enough synthesis on screen to decide whether to resume, archive, or change direction \u2014 without re-reading recent commits or scanning prompt history.

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

## Inspect footer

The card carries one footer line of source pointers, not a section of evidence:

```
  <sub>Inspect: plan `.sisyphus/plans/optimal-plan-forward.md` \u00b7
        last commit `2026-04-23` \u00b7
        last prompt `2026-04-24` (`opencode`) \u00b7
        identity `README.md` \u00b7
        working tree (13 files)</sub>
```

If the reader wants the underlying material, the footer points them straight at it. The detail card itself never enumerates raw commits or prompts \u2014 the user can open the listed file or run `git log` directly.

## Period in review (`--since N`)

When `--since` is set with `N \u2264 30`, the report switches from a fleet table to a scan-first triage digest. The aim is brutal compression: a reader with dozens of projects sees, in seconds, *what needs me, what's new, what moved.*

```
  Last 7 day(s)
  _Since 2026-04-19 \u00b7 28 active project(s) \u00b7 197 commit(s) \u00b7 123 substantive prompt(s)_

  ## Needs your attention (20)
  _These have a clear next move. Pick one and finish it._

  - **code-knowledge** \u2014 Reconcile AGENTS.md: it says complete but 10 commit(s) have landed since\u2026 _2d ago \u00b7 dirty, ahead, drift._
  - **dotfiles** \u2014 Pull 7 commit(s) from origin/main. _2h ago \u00b7 behind._
  \u2026

  ## New this week (8)
  _Repos that landed in your worktrees for the first time._

  - **kimi-cavekit** _(Wed)_ \u2014 Cavekit into core + 11 phase skills.
  \u2026

  ## Moved this week (n)
  _Quiet activity \u2014 commits, prompts, or upstream sync._
```

Three exclusive sections answer the user's three weekly questions:

- **Needs your attention** \u2014 has an actionable open state (dirty / ahead / behind / orphan / drift / no-git). The lead clause is the next-action verb (commit, push, pull, init, reconcile), not a count.
- **New this week** \u2014 first commit landed in the window.
- **Moved this week** \u2014 had activity but is in a settled state.

Each row is one line. The previous \"`Start with:` / `Look at:`\" two-line evidence dump per row was retired \u2014 the inspect footer of the per-project detail report covers the same information for whichever project the reader chooses to resume.

Section caps at 8 rows with `\u2026 +N more` overflow.

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
