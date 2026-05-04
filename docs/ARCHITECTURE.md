# Architecture

`project-commander` answers one question: **what is the state of every
project in your project folder(s) right now?**

It does that by reading every place a project leaves a trail — git
history, agent conversation logs across seven coding tools, and plan
docs in the repo itself — folding all of it into one record per
project, then rendering that record in whichever shape you asked for.

This document is the user-facing mental model: how the pipeline flows,
which sources contribute, what the columns mean, and which output
shape each subcommand produces. For module layout, scanner protocol,
the LLM provider implementations, and how to extend the tool, see
[`AGENTS.md`](../AGENTS.md).

## The mental model

Every subcommand walks the same five-stage pipeline. Stages 1–3 are
shared; stage 4 picks one of two interpretation paths; stage 5 picks
the renderer for the asked-for shape.

```
                project-commander <subcommand> [flags]
                              │
                              ▼
            ┌───────────────────────────────────┐
        1.  │   Resolve roots                   │   --root  >  $PROJECT_COMMANDER_ROOTS  >  $HOME auto
            │   Discover projects               │   walks each root, applies globs, dedups
            └─────────────────┬─────────────────┘
                              │
                              │   one task per project, in parallel (8 workers default)
                              ▼
            ┌───────────────────────────────────┐
        2.  │   Scan for signals                │   7 scanners read disk and emit list[Signal]
            │   (git + 5 agent tools + docs)    │   each Signal: source, kind, UTC timestamp, summary
            └─────────────────┬─────────────────┘
                              │
                              ▼
            ┌───────────────────────────────────┐
        3.  │   Fold into ProjectReport         │   per-project: signals[], branch, dirty, last_active
            └─────────────────┬─────────────────┘
                              │
                              ▼
            ┌───────────────────────────────────┐
        4.  │   Interpret  →  Observations      │   deterministic core: Progress state, Outstanding
            │                                   │   item, flags, intent, attention, activity windows
            │     [optional LLM augmentation]   │   prose narrator for 3 surfaces; fail-soft to det.
            └─────────────────┬─────────────────┘
                              │
                              ▼
            ┌───────────────────────────────────┐
        5.  │   Render                          │   fleet table  /  4-question briefing card
            │                                   │   weekly review  /  recap  /  catchup
            │                                   │   verify (json)  /  audit  /  markdown
            └───────────────────────────────────┘
```

Three behavioral guarantees fall out of this shape:

- **Reads from disk every run.** No daemon, no database. The
  deterministic core has no cache; the optional LLM narrator has a
  content-addressed cache under `$XDG_CACHE_HOME/project-commander/`,
  invalidated automatically by any signal change.
- **Deterministic core.** Stage 4's interpretation is rule-based and
  reproducible. Same disk state → same report. The LLM only adds
  prose to three sections; the structure and the data are unchanged.
- **One bad source can't break a report.** Per-source and per-project
  failures surface as a single failure row instead of taking down the
  whole run.

## How sources interact with the filesystem

Seven scanners feed stage 2. Each reads from a different place:
the project folder itself, your home directory's per-tool storage, or
in the case of OpenCode a join across both.

```
                        /<your code root>/<project>/
                                    │
                                    │  in-tree
                                    ▼
                          ┌──────────────────┐
                          │  git              │  .git/, working tree, branch, commits
                          │  docs             │  README, AGENTS, PLAN, ROADMAP,
                          │                   │  NEXT_STEPS, IMPROVEMENTS, TODO, ...
                          └──────────────────┘


                        $HOME/<per-tool storage>/
                                    │
                                    │  agent session histories
                                    ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │                                                                         │
       │  ~/.claude/projects/<encoded-cwd>/*.jsonl              ──►   claude     │
       │                                                                         │
       │  ~/.gemini/tmp/<basename>/{logs.json, chats/}          ──►   gemini     │
       │                                                                         │
       │  ~/.omp/agent/sessions/<encoded-cwd>/*.jsonl           ──►   omp        │
       │                                                                         │
       │  ~/.local/share/opencode/storage/   ⨝                                   │
       │  ~/.claude/transcripts/                                ──►   opencode   │
       │                                                                         │
       │  ~/.aws/amazonq/history/chat-history-<md5(path)>.json  ──►   kiro       │
       │                                                                         │
       └─────────────────────────────────────────────────────────────────────────┘
```

Each tool encodes the project's working directory into its own
storage path differently — Claude does `/`→`-` on the absolute path,
OMP strips `$HOME` first, Gemini uses just the basename (so two repos
named `dotfiles` collide), Kiro hashes the path with MD5. OpenCode
doesn't encode at all; instead, the storage JSON records the working
directory and the scanner joins on it.

The scanners contribute different *kinds* of information:

| Source | Reads | What it tells you |
|---|---|---|
| **git** | The repo itself | The objective record. Branch, dirty flag, last 50 commits with subjects + timestamps. |
| **Claude Code** | `~/.claude/projects/<encoded-cwd>/*.jsonl` | Your prompts in this directory. The intent signal — what you asked for, in your words. |
| **Gemini CLI** | `~/.gemini/tmp/<basename>/{logs.json,chats/}` | Prompts and chats from Gemini sessions. |
| **Oh-My-Pi** | `~/.omp/agent/sessions/<encoded-cwd>/*.jsonl` | OMP harness session prompts. |
| **OpenCode** | `~/.local/share/opencode/storage/` ⨝ `~/.claude/transcripts/` | OpenCode session prompts; storage JSON tells us which session belonged to which working directory. |
| **Kiro / Amazon Q** | `~/.aws/amazonq/history/chat-history-<md5(abspath)>.json` | Activity signal via file mtime. |
| **Docs** | `PLAN.md`, `README.md`, `ROADMAP.md`, `NEXT_STEPS.md`, `IMPROVEMENTS.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `TODO.md` | Stable project identity, plus planning state for plan-drift and next-step synthesis. |

Roughly: **git** says what landed, **agent tools** say what was asked
for, **docs** say what the project is supposed to be.

## How signals become a report

Stage 4 transforms the raw signal stream into something readable. The
transformation is one-way and deliberately layered so the boundary
between *parsing*, *aggregating*, and *interpreting* stays sharp.

```
   Signal              ProjectReport            Observations              Render
   ───────────────────────────────────────────────────────────────────────────────────

   one observation     all signals folded       interpreted state         stdout
   per source          per project              per project

   source: git         signals[]                progress: Active          fleet table
   kind: commit        branch, dirty            outstanding: dirty 13     detail card
   ts: 2026-04-23      git_ahead/behind         flags: [drift, ...]       weekly review
   summary: feat...    last_active              intent: "..."             recap
                       is_git_repo              7d/30d/90d windows        verify (json)
                                                next_action: commit       audit / catchup

   ↑                   ↑                        ↑                         ↑
   parsing only        aggregation              all heuristics            formatting only
                       no decisions             live here                 no decisions
```

Anywhere the report says something interpretive — "Drifting", "Hot",
"plan-drift detected", "review project state" — it came out of the
*Observations* layer. The renderer never decides what state a project
is in. If a heuristic feels wrong, that is the only place to fix it.

## Optional LLM augmentation

Three of the prose surfaces benefit from a real narrative — places
where the deterministic core hits a ceiling distinguishing *what
landed* from *what was discussed*, or naming themes that span
projects:

| Surface | What the LLM adds |
|---|---|
| `report --project NAME` (detail card) | Replaces the body of *What is it*, *What's been happening*, *What's planned next* with synthesized prose. *Where it stands* stays deterministic. |
| `report --since N` (weekly review) | Adds one 3–5 sentence cross-project recap above the deterministic triage table. **One** call per invocation, not per project. |
| `recap` | Augments per-project paragraphs with synthesized "what's been happening" prose. |

When narration runs, the inspect footer marks the body as
`_synthesized prose_` so you can verify any claim against the source
files listed there, or rerun with `--no-llm` to compare.

If no provider is reachable, every surface falls back to the
deterministic synthesis — same shape, same data, less polished prose.
The narrator is opt-in by environment, default-on when a provider is
detected:

```
   ANTHROPIC_API_KEY  ──►  Anthropic (claude-3-5-haiku-latest)
   OPENAI_API_KEY     ──►  OpenAI    (gpt-4o-mini)
   OLLAMA_HOST        ──►  Ollama    (llama3.1, local)

   none of the above  ──►  deterministic only
   --no-llm flag      ──►  deterministic only
```

For provider implementations, prompt contracts, cache key
construction, and fail-soft layers, see the **Narrative module**
section in `AGENTS.md`.

## What you read

Every subcommand renders the same `Observations` data into a
different shape:

```
                      project-commander
                              │
     ┌────────┬───────┬───────┼──────┬──────┬─────┬──────┐
     ▼        ▼       ▼       ▼      ▼      ▼     ▼      ▼
  report    tidy   catchup  verify  dod  audit  recap
     │        │       │       │      │     │      │
  fleet    init    delta-  PASS/  DOD.md prompt- per-period
  detail   stale   since-  FAIL   diff   →commit narrative
  weekly   fetch   cursor  json   PASS/  ratios,
           push                   FAIL/  flags
           archive                DONE/
                                  MANUAL
  read     writes  read    read   read   read   read
  only     +reads  only    only   only   only   only
```

| Subcommand | Best for |
|---|---|
| `report` | Survey. Default fleet table; `--project NAME` opens the 4-question briefing card; `--since N` (N ≤ 30) is the weekly review. |
| `tidy` | Hygiene. Init missing repos, checkpoint stale dirty trees, optionally fetch/push, optionally archive dormant clean projects with `--prune`. The only writing subcommand. |
| `catchup` | Deltas since *you* last looked. Persists a cursor; each run reports what changed since the cursor and advances it. |
| `verify` | Closure checks the *tool* defines. Five named PASS/FAIL checks; `--format json` + non-zero exit on FAIL is suitable for agent chaining. |
| `dod` | Closure checks *you* define. Reads a per-project `DOD.md` checklist; recognized criteria are auto-evaluated, the rest are surfaced as MANUAL. PASS/FAIL/DONE/MANUAL/SKIP per criterion + a progress percent so an agent can diff perceived state against the goal. |
| `audit` | Did agent prompts actually convert into landed code? Looks 24h forward from each substantive prompt for a follow-up commit. |
| `recap` | Per-project retrospective narrative across `--quarter`, `--year`, `--month`, or `--since N`. |

### The fleet table (default)

```
  Project              State          Outstanding   Sources   Git       Intent
  ───────────────────  ─────────────  ────────────  ────────  ────────  ─────────────────────
  cdda_improved        Hot · 7h        dirty 13      DGOP      master*   Cataclysm: Dark Days Ahead is a turn-based…
```

Rows are grouped by attention band — *Needs attention* first, then
*Active*, then *Quiet*. Within a band, rows sort by recency.

### The 4-question briefing card

The detail view (`report --project NAME`) answers four reader
questions, in order, with synthesized prose rather than evidence
dumps. The headings *are* the questions, so the reader can skim:

1. **What is it?** — stable identity from `README` / `ROADMAP` /
   `AGENTS`. Planning docs are intentionally demoted here so the
   identity line does not flip every plan revision.
2. **What's been happening?** — the recent activity arc; commits get
   topic-fragmented, prompts-without-commits get distinguished from
   shipped work.
3. **Where it stands** — the unresolved condition: dirty tree,
   ahead/behind upstream, orphaned thread, plan-drift, or a clean
   checkpoint.
4. **What's planned next** — forward-looking direction from a plan
   doc when one exists.

A `**Your first action:**` callout names the single safest next move
(commit, push, pull, init, reconcile). The card closes with a one-line
`<sub>Inspect: …</sub>` footer pointing at the underlying plan doc,
last commit, last prompt, identity doc, and working-tree state.

### The weekly review

`report --since N` with `N ≤ 30` switches the renderer from a fleet
table to a triage digest. Three exclusive sections answer three
weekly questions:

```
   ## Needs your attention   (has an actionable open state)
   ## New this week          (first commit landed in the window)
   ## Moved this week        (had activity but is in a settled state)
```

Each row is one line, leading with the next-action verb (commit, push,
pull, init, reconcile). Each section caps at 8 rows with `… +N more`
overflow. The *Week in review* paragraph (LLM-synthesized when a
provider is available) sits above the three sections and names
specific projects + cross-project themes.

## Where it scans

Both `report` and the other read-only subcommands resolve project
roots in this order, taking the first source that yields any:

```
   1.  --root <path>             repeatable on the command line
   2.  $PROJECT_COMMANDER_ROOTS  os.pathsep-separated paths
                                 (':' on POSIX, ';' on Windows)
   3.  auto-detect under $HOME   every existing folder named
                                 code, projects, src, dev, work,
                                 repos, git, plus the macOS-cased
                                 Code, Projects, Dev
```

Auto-detect returns *every* matching folder, in declared order — a
user with both `~/code` and `~/work` gets both scanned by default.
Projects are deduped by absolute path, so symlinks pointing at the
same physical directory yield one entry, not two.

If nothing resolves, the command exits with an error pointing at the
override knobs.

## Report key

### State column

Combines progress and last-active recency into one cell, formatted
`{progress} · {age}`:

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
state and plan-doc structure; this column drives the *Your first
action* line in the detail view.

```
   dirty N         N uncommitted files in the work tree
   ahead N         N commits HEAD has that the upstream tracking ref lacks
   behind N        N commits the upstream has that HEAD lacks
   plan N          N unchecked items in the highest-authority plan doc
   phase N         N plan-doc phases not yet marked complete
   orphan          most recent substantive prompt has no follow-up commit
   —               clean: no uncommitted work, no unpushed commits, no orphan thread
```

Headline priority is `dirty` > `ahead` > `behind` > `plan` > `phase`
> `orphan`. If multiple conditions hold, the most-actionable one
wins; the detail view always shows the full list.

### Sources column

Each letter means one tool has touched this project recently. The
order is alphabetical, not ranked.

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
highest-authority identity doc (README/ROADMAP-style), focus pulled
from the most recent substantive prompt.

Identity authority is intentionally different from planning authority:
README/ROADMAP-style docs win for identity so the stable project
identity does not get hijacked by the latest plan doc. Planning docs
feed the detail card's *What's planned next* section.

"Substantive" means *not* a one-word approval. Procedural prompts
(`yes`, `proceed`, `ok`, `next`, `continue`, `go`, `do it`, `retry`)
are filtered out and surfaced separately as a flag.

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

## Subcommand cheat sheet

```
   # report --- read only
   project-commander report                              all projects, sorted by recency
   project-commander report --since 7                    weekly review (N ≤ 30) with cross-project recap
   project-commander report --limit 20                   top 20 most-recent
   project-commander report --project cdda_*             4-question briefing card
   project-commander report --exclude pi-*               hide noisy folders
   project-commander report --format json                structured output
   project-commander report --format markdown            shareable report
   project-commander report --disable kiro               skip a source you don't use
   project-commander report --root /other/path           scan a specific root (repeatable)
   project-commander report --no-llm                     force deterministic synthesis

   # tidy --- writes; --dry-run shows the plan first
   project-commander tidy --dry-run                      preview hygiene plan
   project-commander tidy                                run init + commit-stale (default on)
   project-commander tidy --no-commit                    init only; never auto-commit dirty trees
   project-commander tidy --stale-age 14                 raise stale threshold from 7 to 14 days
   project-commander tidy --sync                         + git fetch --all per repo
   project-commander tidy --push                         + push branches with no hygiene commits
   project-commander tidy --prune                        + plan ARCHIVE moves for dormant clean projects

   # catchup --- delta digest since persisted cursor
   project-commander catchup                             show deltas since last invocation, advance cursor
   project-commander catchup --since 6h                  preview a window without advancing
   project-commander catchup --reset-cursor              start fresh

   # verify --- closure checks suitable for agent chaining
   project-commander verify --project NAME --format json   structured PASS/FAIL
   project-commander verify                                fleet-wide JSON array, exit 1 on any FAIL

   # dod --- user-defined definition of done
   project-commander dod --project NAME                     terminal table + progress bar
   project-commander dod --project NAME --format json       structured PASS/FAIL/DONE/MANUAL/SKIP
   project-commander dod                                    fleet roll-up; only projects with DOD.md
   project-commander dod --project NAME --file ./done.md    use a non-default checklist file

   # audit --- prompt → commit causality
   project-commander audit --project NAME --since 30     ratios, orphans, plan-drift flag

   # recap --- per-period retrospective narrative
   project-commander recap --quarter                     paragraph per project, by category
   project-commander recap --since 30                    arbitrary day window

   # roots --- same resolution policy for every subcommand
   project-commander report                              auto-detect $HOME conventions
   PROJECT_COMMANDER_ROOTS=~/work:~/clients pcmd report  scan two roots from env var
   pcmd tidy --root ~/work --root ~/personal             scan two roots from flags
```

`--project`, `--exclude`, `--disable`, `--root` repeat. Globs are
basename matches.

## Where this lives in the repo

If you want to read or extend the code — module layout, scanner
protocol, narrator provider implementations, prompt contracts, cache
key construction, the candidate-source roadmap — see
[`AGENTS.md`](../AGENTS.md). This document keeps the user-facing view
of *how the report is produced*; that one keeps the contributor-facing
view of *how the code is structured*.
