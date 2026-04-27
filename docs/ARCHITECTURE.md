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

"Substantive" means *not* a one-word approval. Procedural prompts (`yes`, `proceed`, `ok`, `next`, `continue`, `go`, `do it`, `retry`) are filtered out and surfaced separately as a flag — see below.

## The four-question briefing card

The detail view answers four reader questions, in order, with synthesized prose rather than evidence dumps. The headings *are* the questions, so the reader can skim:

1. **What is it?** — the project's stable identity, sourced from `README` / `ROADMAP` / `AGENTS` (planning docs are intentionally demoted here so the identity line does not flip every plan revision).
2. **What's been happening?** — a one-sentence synthesis of the last week of activity. Recent commits get topic-fragmented and joined; falls back to last concrete action or latest prompt when no commits landed.
3. **Where it stands** — the unresolved condition that explains the current situation: dirty tree, ahead/behind upstream, orphaned thread, plan-drift, or a clean checkpoint. One synthesized paragraph, not a bulleted evidence list.
4. **What's planned next** — the forward-looking direction, sourced from a plan doc when one exists. When workstream synthesis would only repeat \"What's been happening?\", the section degrades gracefully to a `_No plan doc found_` line.

Below those four sections, a `**Your first action:**` callout names the single safest next move (commit, push, pull, init, reconcile). The card closes with a one-line `<sub>Inspect: …</sub>` footer that lists the plan doc, last commit, last prompt, identity doc, and working-tree state — a pointer trail rather than another evidence section.

This is the JTBD boundary: the reader sitting down to a half-remembered project gets enough synthesis on screen to decide whether to resume, archive, or change direction — without re-reading recent commits or scanning prompt history.

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
  <sub>Inspect: plan `.sisyphus/plans/optimal-plan-forward.md` ·
        last commit `2026-04-23` ·
        last prompt `2026-04-24` (`opencode`) ·
        identity `README.md` ·
        working tree (13 files)</sub>
```

If the reader wants the underlying material, the footer points them straight at it. The detail card itself never enumerates raw commits or prompts — the user can open the listed file or run `git log` directly.

## Optional LLM narrative layer

The deterministic synthesis hits a ceiling on three of the four detail-card
fields: identity, recent activity, and planned next work. An LLM narrator can
do better there, specifically by distinguishing *what landed* (commits) from
*what was discussed* (prompts without follow-up commits) and by surfacing
contradictions between plan claims and commit reality.

The fourth section (`Where it stands`) and the status header / Inspect footer
stay deterministic --- they are mechanical state and the user needs them as an
audit anchor next to the synthesized prose.

### Provider abstraction

`narrative.py` defines a single protocol:

```python
class Narrator(Protocol):
    def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None: ...
```

Three concrete implementations reach external services over `urllib` (no new
dependencies):

- `AnthropicNarrator` --- requires `ANTHROPIC_API_KEY`. Default model:
  `claude-3-5-haiku-latest`.
- `OpenAINarrator` --- requires `OPENAI_API_KEY`. Default model: `gpt-4o-mini`.
  Uses `response_format: {"type": "json_object"}` for parse reliability.
- `OllamaNarrator` --- talks to `OLLAMA_HOST` (default `http://localhost:11434`).
  Default model: `llama3.1`. 120-second timeout for cold starts.

Plus `DisabledNarrator`, which always returns `None`, used when no provider is
available or when the user passes `--no-llm`.

Auto-detection order: anthropic > openai > ollama > disabled. Override with
`--llm-provider {auto,anthropic,openai,ollama,none}` or
`PROJECT_COMMANDER_LLM`. Override the model with `--llm-model` or
`PROJECT_COMMANDER_LLM_MODEL`.

### Fail-soft, three layers

Narration must never break a report. Failure paths that return `None` and
trigger deterministic fallback:

1. No provider configured (`DisabledNarrator`).
2. Provider call raises (timeout, 4xx, 5xx, malformed transport). Errors are
   logged to stderr only when `PROJECT_COMMANDER_LLM_VERBOSE=1`.
3. Output JSON malformed, missing keys, or fields trivially short (<8 chars).

When narration is *used*, the Inspect footer prepends `_synthesized prose_` so
the reader knows the body was LLM-generated and can rerun with `--no-llm` to
compare.

### Caching

Content-addressed JSON cache under
`$XDG_CACHE_HOME/project-commander/narrative/`. The cache key is
`SHA-256(prompt_text + model_id)`. Any change in prompt --- new commit, new
prompt, edited README/PLAN, different model --- yields a different key, so
stale entries are simply never hit. There is no TTL.

Failure outputs are *not* cached: a transient API error never poisons future
runs.

### Prompt contract

One user message per project carrying capped excerpts:

- Identity: up to 2 of `README.md` / `AGENTS.md` / `CLAUDE.md` / `GEMINI.md`,
  600 chars each.
- Plan: first hit of `PLAN.md` / `ROADMAP.md` / `NEXT_STEPS.md` /
  `IMPROVEMENTS.md` / `TODO.md`, 1000 chars.
- Recent commits: 30 most recent subjects with dates.
- Recent substantive prompts: 15 most recent subjects with dates and source
  (procedural one-word approvals are filtered out by `is_procedural`).
- Branch state: branch, dirty/uncommitted count, ahead/behind upstream,
  last activity timestamp.

Total input: roughly 1500-2000 tokens per project.

The system prompt enforces 2-4 sentences per field, plain prose, no marketing
language, distinguish shipped from attempted, and never invent. Output is
strict JSON with three keys: `what_it_is`, `whats_been_happening`,
`whats_planned`.

### Where the narrator runs

- `report --project NAME` --- on by default. Single project, one cached call.
- `report --project NAME --format markdown` --- on by default.
- `report` (fleet table) --- not invoked. One-line cells do not benefit.
- `report --since 7` (weekly review) --- not invoked. One-line entries.
- `recap` --- on by default. Per-project paragraphs benefit substantially.
- `tidy` / `verify` / `audit` / `catchup` --- not invoked. Their output is
  about state, not narrative.

Pass `--no-llm` to force deterministic synthesis on any surface.

## Period in review (`--since N`)

When `--since` is set with `N ≤ 30`, the report switches from a fleet table to a scan-first triage digest. The aim is brutal compression: a reader with dozens of projects sees, in seconds, *what needs me, what's new, what moved.*

```
  Last 7 day(s)
  _Since 2026-04-19 · 28 active project(s) · 197 commit(s) · 123 substantive prompt(s)_

  ## Needs your attention (20)
  _These have a clear next move. Pick one and finish it._

  - **code-knowledge** — Reconcile AGENTS.md: it says complete but 10 commit(s) have landed since… _2d ago · dirty, ahead, drift._
  - **dotfiles** — Pull 7 commit(s) from origin/main. _2h ago · behind._
  …

  ## New this week (8)
  _Repos that landed in your worktrees for the first time._

  - **kimi-cavekit** _(Wed)_ — Cavekit into core + 11 phase skills.
  …

  ## Moved this week (n)
  _Quiet activity — commits, prompts, or upstream sync._
```

Three exclusive sections answer the user's three weekly questions:

- **Needs your attention** — has an actionable open state (dirty / ahead / behind / orphan / drift / no-git). The lead clause is the next-action verb (commit, push, pull, init, reconcile), not a count.
- **New this week** — first commit landed in the window.
- **Moved this week** — had activity but is in a settled state.

Each row is one line. The previous \"`Start with:` / `Look at:`\" two-line evidence dump per row was retired — the inspect footer of the per-project detail report covers the same information for whichever project the reader chooses to resume.

Section caps at 8 rows with `… +N more` overflow.

## Tidy: hygiene actions

`project-commander tidy` is the second top-level subcommand. Where
`report` only reads, `tidy` writes — it applies five narrowly-scoped
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

  archive        (--prune only)  move dormant clean projects to an archive folder
                 →  shutil.move(<project> → <archive_dir>/<name>)
  hold           (--prune only)  refused archive (dirty / unpushed) — surfaces a manual decision
```

`init` and `commit-stale` are on by default; `--sync`, `--push`, and
`--prune` are explicit opt-ins because they touch the network or
filesystem in ways the user should approve. `--no-init` / `--no-commit`
turn the defaults off; `--dry-run` shows the plan without executing.

`--prune` walks the fleet for projects last touched at least
`--prune-age` days ago (default 90). Clean dormant projects get an
ARCHIVE planned move; dirty or ahead-of-upstream projects get HOLD instead
so the user resolves them first. Default destination is
`<project.parent>/archive/<name>/`, override with `--archive-dir`.

Every commit `tidy` makes carries a trailer:

```
  Project-Commander-Hygiene: true
```

This trailer is the boundary between work and housekeeping. The
`push` action refuses any branch whose `upstream..HEAD` range
contains a hygiene commit, so a `commit-stale` checkpoint never
lands on a remote without you noticing.

Plan and execute are split for testability. `tidy.plan(report,
config, now)` is a pure function returning `list[PlannedAction]`;
`tidy.execute_init / execute_commit_stale / execute_fetch /
execute_push / execute_archive / execute_hold` perform the side effects. Tests exercise the planner
with synthetic reports and exercise the push refusal against a real
local bare repo + clone, no network involved.

## Catchup: deltas since you last looked

`project-commander catchup` answers a different question from `report --since`:
the weekly digest is anchored to the calendar; catchup is anchored to *you*.
It persists a cursor at `$XDG_STATE_HOME/project-commander/catchup-cursor.txt`
(or `~/.local/state/project-commander/catchup-cursor.txt`) holding the timestamp
of the last invocation. Each run shows deltas since the cursor and then
advances it.

Three sections, each answering a different question:

- **Agent activity while you were away** — projects with prompts since the cursor
- **Upstream moved without you** — projects with new commits whose branch is behind
- **Your own work since last check** — projects where you authored progress

A project can appear in multiple sections; they are different angles, not
exclusive buckets. `--since 6h` overrides the cursor for one preview run
without advancing it. `--reset-cursor` clears the cursor so the next run
starts fresh. `--no-advance` previews without writing.

## Verify: structured PASS/FAIL closure checks

`project-commander verify [--project NAME]` runs five named checks and exits
non-zero on any FAIL:

```
  working_tree_clean    no uncommitted changes
  branch_in_sync        no ahead/behind state vs upstream
  no_orphan_thread      latest substantive prompt has a follow-up commit
  no_plan_drift         plan-doc completion claim still matches the code
  prompts_substantive   recent prompts include real direction, not just approvals
```

The terminal form is human-readable; `--format json` produces a structured
object suitable for agents. Single-project invocations emit one object;
fleet runs (no `--project`) emit an array. Scripts can chain `tidy && verify`
to gate handoff actions on a clean state.

## Audit: prompt → commit causality

`project-commander audit --project NAME --since N` shows whether agent prompts
actually converted into landed code. For each substantive prompt in the
window, the module looks forward 24 hours for any commit; "executed" if found,
"orphan" otherwise. The prompt-to-commit ratio gives a coarse handle on
conversation volume vs real progress.

Procedural prompts (yes/proceed/continue/...) are counted separately and never
count toward causality — they are approval traffic, not direction.

Audit raises the GitScanner's commit cap to 200 so that windows up to a
month see real history rather than a truncated tail.

## Recap: per-period retrospective narrative

`project-commander recap [--quarter|--year|--month|--since N]` is the memory
artifact the weekly digest deliberately is not. Each project that had
activity in the window gets a paragraph synthesized from purpose + commit
themes, grouped into:

- **Shipped** — progress state is `shipped` and activity exists in the window
- **Major arcs** — ≥ 10 commits in the window
- **Started but paused** — first commit in the window, idle for the back end
- **Quiet activity** — had activity but doesn't fit the above

Recap raises the GitScanner cap to 500 so it can see real history for
year-long windows.
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
