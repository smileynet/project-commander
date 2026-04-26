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

### Tier 1 — same-shape scanners (next to implement)

Each reads its own per-project storage and emits prompts / sessions /
doc-edit signals exactly the way the existing seven do. No new
`SignalKind` required.

```
  Codex CLI (OpenAI)
  ──────────────────
  Anchor:  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
           First event is `session_meta` with `payload.cwd`, branch,
           commit. ~/.codex/history.jsonl indexes per-prompt {ts, text}.
  Adds:    Codex sessions where Claude/Gemini/OMP/OpenCode were not used.
  Flag:    X  (Co**X** — `C` is taken)
  Source:  rollout file format observed locally; structure stable across
           cli_version 0.118.x.

  Cursor (Anysphere)
  ──────────────────
  Anchor:  ~/.cursor/projects/<encoded-cwd>/agent-transcripts/*.{json,txt}
           Same `/`→`-` keying as Claude Code. Also chat SQLite at
           ~/.cursor/chats/<hash>/<uuid>/store.db.
  Adds:    In-IDE agent activity that no CLI scanner sees. Validates
           `tool-cluster` more aggressively when Cursor + a CLI agent
           both touched the same project.
  Flag:    R  (cu**R**sor — `C` is taken)

  JetBrains family
  ────────────────
  Anchor:  ~/.config/JetBrains/<IDE>/options/recentProjects.xml
           One file per IDE (PyCharm, IntelliJ, Rider, RustRover,
           WebStorm, ...). Each entry: `key=$USER_HOME$/code/<name>`,
           `activationTimestamp` in epoch-ms.
  Adds:    'Last opened in an IDE' signal — orthogonal to commits and
           prompts. Catches projects you read in the IDE without typing
           at an agent.
  Flag:    J

  Aider
  ─────
  Anchor:  <project>/.aider.input.history (timestamped, one prompt per
           line with `# YYYY-MM-DD HH:MM:SS.ffffff` markers)
           <project>/.aider.chat.history.md
  Adds:    A widely-used CLI agent the existing scanners do not cover.
           Files live in the repo root, so cwd binding is trivial.
  Flag:    A
  Source:  https://aider.chat/docs/config/options.html

  Cline (VSCode extension `saoudrizwan.claude-dev`)
  ─────────────────────────────────────────────────
  Anchor:  ~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/
             tasks/<taskId>/{api_conversation_history.json,
                             ui_messages.json, task_metadata.json}
           Resolve task→workspace via
             state/taskHistory.json  (HistoryItem.workspacePath)
  Adds:    The major in-IDE Claude agent for VSCode users.
           `task_metadata.json` includes `files_in_context` and
           `model_usage` — richer focus inference than prompts alone.
  Flag:    L  (c**L**ine)
  Caution: Task dirs can grow to many GB. Stat metadata only; never
           read full conversation files.
  Source:  https://github.com/cline/cline (see core/storage/disk.ts)

  Continue.dev
  ────────────
  Anchor:  ~/.continue/sessions/sessions.json (index — every entry has
           an explicit `workspaceDirectory` field, no path mangling)
           ~/.continue/sessions/<sessionId>.json (per-session)
           Honor $CONTINUE_GLOBAL_DIR override.
  Adds:    Cross-IDE agent (ships VSCode + JetBrains extensions). The
           index file gives every session keyed by project in one read.
  Flag:    N  (co**N**tinue)
  Source:  https://github.com/continuedev/continue (core/util/paths.ts,
           core/util/history.ts)
```

### Tier 2 — filesystem signals (new `kind`s)

These aren't prompts or commits — they're proxies for *engagement*
fingerprinted by mtime or running state. Each adds a new `SignalKind`
so the observations layer must decide how to weight it.

```
  Test / lint / typecheck cache cluster        kind: "toolrun"
  ───────────────────────────────────────
  Anchors: <project>/.pytest_cache/v/cache/lastfailed
           <project>/.pytest_cache/v/cache/nodeids
           <project>/.ruff_cache/  .mypy_cache/  .tox/
  Adds:    Distinguishes 'edited but never re-ran tests' from
           'iterating in a tight test/fix loop'. Mtime read; no parsing.
  Source:  https://docs.pytest.org/en/stable/how-to/cache.html

  VSCode workspaceStorage mtime               kind: "editor_open"
  ─────────────────────────────────
  Anchor:  ~/.config/Code/User/workspaceStorage/<md5(abspath+inode)>/
           Linux uses inode; macOS/Win uses birthtime ms. Hash is
           computed on demand from the project path — no scanning.
           Read directory mtime as 'last opened in VSCode'; do NOT
           parse state.vscdb (schema unstable).
  Adds:    The peer of the JetBrains scanner for VSCode users.
  Source:  https://github.com/microsoft/vscode (resourceIdentity-
           ServiceImpl.ts) — hash recipe documented there.

  Lockfile mtimes                              kind: "deps_updated"
  ───────────────
  Anchors: <project>/{uv.lock, poetry.lock, requirements.lock,
                       package-lock.json, pnpm-lock.yaml, yarn.lock,
                       Cargo.lock, flake.lock, Gemfile.lock, go.sum,
                       Pipfile.lock, pixi.lock}
  Adds:    'Dependencies last touched N days ago' — a proxy for
           toolchain churn distinct from code commits.
  Note:    Mtime only. Reading lock contents is out of scope.

  Devcontainer / Codespace marker              kind: "portable_env"
  ────────────────────────────────
  Anchor:  <project>/.devcontainer/devcontainer.json or
           <project>/.devcontainer.json
  Adds:    'This project ships a reproducible env' — strong signal
           that the repo is meant to be used by others or by you on
           multiple machines. Mtime is last toolchain-update.
  Source:  https://containers.dev/implementors/spec/

  Docker Compose runtime state                 kind: "container"
  ────────────────────────────
  Anchor:  Local marker at <project>/{compose.yaml,docker-compose.yml}.
           Live state via
             docker ps -a --filter \
               label=com.docker.compose.project=<basename> \
               --format json
           Containers carry `com.docker.compose.project.working_dir`
           with the absolute path that started them.
  Adds:    'This project has live services right now' — no other
           scanner can infer this. Container CreatedAt + StartedAt
           are precise activity timestamps.
  Caution: Project name defaults to basename but can be overridden by
           `-p`, COMPOSE_PROJECT_NAME env, or `name:` in compose file.
  Source:  https://docs.docker.com/compose/how-tos/project-name/
```

### Tier 3 — remote enrichment (auth required)

```
  GitHub Actions run history
  ──────────────────────────
  Anchor:  Local: <project>/.github/workflows/ + git remote origin URL
           Remote: gh run list -R <owner>/<repo> --json \
                       conclusion,createdAt,headBranch,status,workflowName
  Adds:    'CI red on main since 3 days' — distinguishes Shipped-and-
           green from Shipped-but-broken.
  Caution: Requires `gh` installed + authenticated. Rate-limited.
           Fail-soft on auth issues.
  Source:  https://cli.github.com/manual/gh_run_list

  GitHub issues / PRs (open count, recent activity)
  Linear / Jira (per-project filter required)
  Roo Code (Cline fork — same scanner with swapped extension ID)
```

### Categories explicitly considered but not yet listed

These were evaluated and rejected for now. Reasons noted so the
thinking is captured:

- **Shell history (`~/.zsh_history`)** — `cd <project>` lines exist
  but signal is noisy (typos, aborted jumps) and a privacy concern
  to scan by default.
- **direnv allow-list (`~/.local/share/direnv/allow/`)** — only fires
  on `cd` after explicit allow; correlates with `.envrc` presence
  which would already be picked up by the lockfile-mtime cluster.
- **Tool-version files (`.python-version`, `.nvmrc`, `.tool-versions`,
  `.mise.toml`)** — adoption markers but no timestamp story beyond
  mtime; subsumed by lockfile-mtime tracking.
- **Sourcegraph Cody, GitHub Copilot Chat (VSCode)** — chat history
  in opaque SQLite (`state.vscdb`) with unstable schema; the VSCode
  workspaceStorage-mtime signal already captures the engagement
  proxy without parsing fragile internals.
- **Zed agent panel** — threads live in SQLite keyed by internal
  `worktree_id`; per-project resolution requires a join against
  Zed's own worktree table. Re-evaluate when the schema stabilizes.

### The bar for inclusion

Same as the existing seven:

```
  1.  Discoverable per-project anchor (file or directory pattern).
  2.  Time-stamped events (or mtime as proxy for engagement).
  3.  Tells a story the existing sources cannot tell on their own.
  4.  Either fully offline, or fail-soft when auth/network is absent.
```

Prior art surveyed: [`mr` (myrepos)](https://myrepos.branchable.com/),
[`gita`](https://github.com/nosarthur/gita),
[`ghq`](https://github.com/x-motemen/ghq),
[`lazygit` recent-repos](https://github.com/jesseduffield/lazygit),
[GitHub Pulse](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/using-pulse-to-view-a-summary-of-repository-activity),
and [`chops`](https://github.com/Shpigford/chops) (a multi-agent
dashboard with the same scanner-fanout shape, useful as a cross-check
for which agent storage paths the community considers worth scanning).
