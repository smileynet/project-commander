# Contributor / Agent guide

This file is for people (and coding agents) reading or extending the
source. It covers code layout, the scanner contract, how to add a new
source, the candidate-source roadmap, testing, and project
conventions.

For *what the tool does*, see the [README](README.md). For
*user-facing how it works*, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Module layout

```
src/project_commander/
├── cli.py            argparse, default config, scanner construction, dispatch
├── discovery.py      walk ~/code, basename glob filter
├── paths.py          per-tool cwd → session-key translations (pure functions)
├── models.py         Signal (frozen, UTC-enforced), ProjectReport
├── aggregator.py     build_report, build_all (threadpool fanout)
├── observations.py   Progress enum, ActivityWindow, build(), heuristics
├── report.py         render_table / render_detail / render_detail_markdown /
│                     render_json / render_markdown
└── sources/
    ├── base.py       SourceScanner protocol (just `scan(project) -> list[Signal]`)
    ├── git.py        subprocess git log
    ├── claude.py     ~/.claude/projects/<key>/*.jsonl
    ├── gemini.py     ~/.gemini/tmp/<basename>/{logs.json,chats/}
    ├── omp.py        ~/.omp/agent/sessions/<key>/*.jsonl
    ├── opencode.py   ~/.local/share/opencode + ~/.claude/transcripts join
    ├── kiro.py       ~/.aws/amazonq/history/chat-history-<md5(abspath)>.json
    └── docs.py       in-tree PLAN/README/ROADMAP/NEXT_STEPS/AGENTS/...

tests/
└── test_scanners.py  synthetic-fixture tests; no network, no real ~/code
```

## Three-layer transformation

The pipeline is deliberately three layers with one place to interpret
signals:

```
Signal         per-observation, one source              models.Signal
   │           kind ∈ {commit, prompt, doc, session, filesystem}
   │           timezone-aware UTC enforced in __post_init__
   ▼
ProjectReport  per-project, all signals folded in       models.ProjectReport
   │           branch, dirty flag, signals[], last_active
   ▼
Observations   per-project, interpreted                 observations.Observations
               progress enum, intent text, flags, evidence,
               7d/30d/90d activity windows
```

| Layer | Reads | Produces | Has business logic? |
|---|---|---|---|
| **Sources** (`sources/*.py`) | Disk (git, JSON, markdown, ...) | `list[Signal]` | No — only parsing |
| **Aggregator** (`aggregator.py`) | Source output | `ProjectReport` + `Observations` | Just orchestration |
| **Observations** (`observations.py`) | `ProjectReport` | `Observations` | Yes — all heuristics live here |
| **Renderer** (`report.py`) | `ProjectReport.observations` | Stdout | No — interprets nothing |

If a heuristic feels wrong, it lives in `observations.py`. The
renderer never decides what state a project is in.

## Scanner protocol

Every scanner satisfies this tiny protocol (`sources/base.py`):

```python
class SourceScanner(Protocol):
    name: str
    def scan(self, project: Path) -> list[Signal]: ...
```

There are two flavors:

1. **Stateless per-call.** `GitScanner`, `DocsScanner` — they only
   need the project path and ask the filesystem directly each time.

2. **Stateful (global index, dispatch by project).** `OpenCodeScanner`
   has to read `storage/directory-readme/ses_*.json` once to learn
   which session belonged to which working directory, then return the
   matching prompts from `~/.claude/transcripts/ses_*.jsonl`. State
   is built in `__init__` (lazily on first scan) so the per-project
   call stays cheap.

`Signal` is `@dataclass(frozen=True)` and rejects naive datetimes in
`__post_init__`. Every timestamp is normalized to UTC before storage.
Don't bypass this — naive timestamps will ripple into the comparison
logic in `observations.py` and silently produce wrong activity
windows.

### Per-tool path-key translations

Each agent tool encodes the project's working directory into a
session-storage directory name differently. Those rules live in
`paths.py`:

| Tool | Convention | Function |
|---|---|---|
| Claude Code | `/` → `-` on absolute path | `paths.claude_key(p)` |
| Oh-My-Pi | strip `$HOME`, then `/` → `-` | `paths.omp_key(p, home)` |
| Gemini CLI | basename only (collisions possible) | `paths.gemini_key(p)` |
| Kiro / Amazon Q | `md5(absolute path)` | `paths.kiro_hash(p)` |
| OpenCode | indirect — session JSON records `cwd` | (no key fn; JSON lookup) |

Isolating these rules in one module is the difference between *"oh
right, that one's md5"* showing up once and showing up scattered
through three scanners.

## Adding a new source

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

Concrete checklist:

1. Create `src/project_commander/sources/<name>.py` with a class that
   has `name: str` and `scan(self, project: Path) -> list[Signal]`.
2. If the tool encodes the project path as a session-dir name, add
   the translation to `paths.py` and import it from the scanner — do
   not inline the rule.
3. Wire it into `cli.py`: add it to the `--disable` choices and
   append an instance to `scanners` in `main()`.
4. Add the single-letter flag to `_SRC_FLAGS` in `report.py`.
5. Pick a `kind` from the existing literal union (`commit` /
   `prompt` / `doc` / `session` / `filesystem`). Adding a new kind
   is a model change and forces every reader to think about how to
   handle it — which is the point.
6. Add a synthetic-fixture test in `tests/test_scanners.py`. No
   network, no real `~/code` access.

The aggregator, observations layer, and renderer pick up the new
source automatically — they iterate over `report.signals` and group
by `(source, kind)`.

## Candidate signal sources

The seven scanners shipped today cover git + the major agent CLIs +
in-tree docs. There are still high-value signal sources outside that
set; this section names them so anyone implementing one starts with
the same map.

### bd (beads) — graph issue tracker

[`bd`](https://github.com/gastownhall/beads) is a distributed graph
issue tracker designed for coding agents. Several projects under
`~/code` already use it (`bd onboard` is the convention).

**Why it matters.** Agent prompts tell us what the user *asked
for*; commits tell us what *landed*. `bd` fills the gap between
those two: what work is *planned*, what is *in flight*, and what is
*done* — structured, with timestamps, dependencies, and priorities.
That is exactly the layer `project-commander` currently has to
*infer* from prompt + commit cadence.

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

`task` is a new `SignalKind`. Adding it forces the renderer and
observations layer to decide how to display it. Provisional plan:

- **`Observations.purpose` / `focus`**: the highest-priority open
  `bd` issue becomes a strong focus candidate, often more reliable
  than the latest prompt.
- **Activity windows**: closed-task counts feed the same
  `commits/prompts/sessions` triplet as a fourth column.
- **New flag `blocked`**: at least one open issue with status
  `blocked` and no movement in 14 days.
- **New progress state `Stalled`**: open `bd` ready-queue is empty
  *and* no commits in 30 days.

**Implementation outline.** `bd` ships JSON output (`bd list --json`,
`bd ready --json`, `bd stats --json`). The scanner shells out from
the project root, parses JSON, emits one signal per task. Falls back
to reading the Dolt database directly only if the CLI is unavailable.

Single-letter flag: **`B`**.

### Tier 1 — same-shape scanners

Each reads its own per-project storage and emits prompts / sessions /
doc-edit signals exactly the way the existing seven do. No new
`SignalKind` required.

```
  Codex CLI (OpenAI)                                    flag: X
  ──────────────────
  Anchor:  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
           First event is `session_meta` with `payload.cwd`, branch,
           commit. ~/.codex/history.jsonl indexes per-prompt {ts,text}.
  Adds:    Codex sessions where Claude/Gemini/OMP/OpenCode were not used.

  Cursor (Anysphere)                                    flag: R
  ──────────────────
  Anchor:  ~/.cursor/projects/<encoded-cwd>/agent-transcripts/*.{json,txt}
           Same `/`→`-` keying as Claude Code. Also chat SQLite at
           ~/.cursor/chats/<hash>/<uuid>/store.db.
  Adds:    In-IDE agent activity that no CLI scanner sees.

  JetBrains family                                      flag: J
  ────────────────
  Anchor:  ~/.config/JetBrains/<IDE>/options/recentProjects.xml
           One file per IDE (PyCharm, IntelliJ, Rider, RustRover,
           WebStorm, ...). Each entry: `key=$USER_HOME$/code/<name>`,
           `activationTimestamp` in epoch-ms.
  Adds:    'Last opened in an IDE' signal — orthogonal to commits.

  Aider                                                 flag: A
  ─────
  Anchor:  <project>/.aider.input.history (timestamped, one prompt per
           line with `# YYYY-MM-DD HH:MM:SS.ffffff` markers)
           <project>/.aider.chat.history.md
  Adds:    A widely-used CLI agent the existing scanners do not cover.
  Source:  https://aider.chat/docs/config/options.html

  Cline (VSCode extension `saoudrizwan.claude-dev`)     flag: L
  ─────────────────────────────────────────────────
  Anchor:  ~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/
             tasks/<taskId>/{api_conversation_history.json,
                             ui_messages.json, task_metadata.json}
           Resolve task→workspace via state/taskHistory.json
           (HistoryItem.workspacePath).
  Caution: Task dirs can grow to many GB. Stat metadata only;
           never read full conversation files.
  Source:  https://github.com/cline/cline (core/storage/disk.ts)

  Continue.dev                                          flag: N
  ────────────
  Anchor:  ~/.continue/sessions/sessions.json (index — every entry has
           an explicit `workspaceDirectory` field, no path mangling)
           ~/.continue/sessions/<sessionId>.json (per-session)
           Honor $CONTINUE_GLOBAL_DIR override.
  Source:  https://github.com/continuedev/continue (core/util/paths.ts,
           core/util/history.ts)
```

### Tier 2 — filesystem signals (new `kind`s)

These aren't prompts or commits — they're proxies for *engagement*
fingerprinted by mtime or running state. Each adds a new `SignalKind`
so the observations layer must decide how to weight it.

```
  Test / lint / typecheck cache cluster        kind: "toolrun"
  Anchors: <project>/.pytest_cache/v/cache/{lastfailed,nodeids}
           <project>/.ruff_cache/  .mypy_cache/  .tox/
  Adds:    Distinguishes 'edited but never re-ran tests' from
           'iterating in a tight test/fix loop'.
  Source:  https://docs.pytest.org/en/stable/how-to/cache.html

  VSCode workspaceStorage mtime                kind: "editor_open"
  Anchor:  ~/.config/Code/User/workspaceStorage/<md5(abspath+inode)>/
           Linux uses inode; macOS/Win uses birthtime ms.
           Read directory mtime as 'last opened in VSCode';
           do NOT parse state.vscdb (schema unstable).
  Source:  https://github.com/microsoft/vscode (resourceIdentity-
           ServiceImpl.ts) — hash recipe documented there.

  Lockfile mtimes                              kind: "deps_updated"
  Anchors: <project>/{uv.lock, poetry.lock, requirements.lock,
                       package-lock.json, pnpm-lock.yaml, yarn.lock,
                       Cargo.lock, flake.lock, Gemfile.lock, go.sum,
                       Pipfile.lock, pixi.lock}
  Adds:    'Dependencies last touched N days ago' — a proxy for
           toolchain churn distinct from code commits.

  Devcontainer / Codespace marker              kind: "portable_env"
  Anchor:  <project>/.devcontainer/devcontainer.json or
           <project>/.devcontainer.json
  Adds:    'This project ships a reproducible env' — strong signal
           that the repo is meant to be used by others or by you on
           multiple machines.
  Source:  https://containers.dev/implementors/spec/

  Docker Compose runtime state                 kind: "container"
  Anchor:  Local marker at <project>/{compose.yaml,docker-compose.yml}.
           Live state via
             docker ps -a --filter \
               label=com.docker.compose.project=<basename> \
               --format json
           Containers carry com.docker.compose.project.working_dir
           with the absolute path that started them.
  Adds:    'This project has live services right now' — no other
           scanner can infer this.
  Caution: Project name defaults to basename but can be overridden
           by `-p`, COMPOSE_PROJECT_NAME, or `name:` in compose file.
  Source:  https://docs.docker.com/compose/how-tos/project-name/
```

### Tier 3 — remote enrichment (auth required)

```
  GitHub Actions run history
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
  Roo Code (Cline fork — same scanner, swapped extension ID)
```

### Considered and rejected

- **Shell history (`~/.zsh_history`)** — `cd <project>` lines exist
  but signal is noisy (typos, aborted jumps) and a privacy concern
  to scan by default.
- **direnv allow-list (`~/.local/share/direnv/allow/`)** — only
  fires on `cd` after explicit allow; correlates with `.envrc`
  presence which is already covered by lockfile-mtime tracking.
- **Tool-version files (`.python-version`, `.nvmrc`,
  `.tool-versions`, `.mise.toml`)** — adoption markers but no
  timestamp story beyond mtime; subsumed by lockfile-mtime tracking.
- **Sourcegraph Cody, GitHub Copilot Chat (VSCode)** — chat history
  in opaque SQLite (`state.vscdb`) with unstable schema. The VSCode
  workspaceStorage-mtime signal already captures the engagement
  proxy without parsing fragile internals.
- **Zed agent panel** — threads live in SQLite keyed by internal
  `worktree_id`; per-project resolution requires a join against
  Zed's worktree table. Re-evaluate when the schema stabilizes.

### Bar for inclusion

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
[`chops`](https://github.com/Shpigford/chops).

## Testing

```sh
pip install -e ".[dev]"
pytest -q
```

The suite is synthetic-fixture driven: tests build a fake `home` and
`code_root` per test using `tmp_path` and never touch real `~/code`
or run network. `tests/test_scanners.py` covers each source plus the
aggregator and observations layer.

When adding a scanner, add a fixture-based test that:

1. Builds a representative on-disk fixture under `tmp_path`.
2. Calls the scanner.
3. Asserts expected `Signal` count, `kind`s, and timestamps.

When adding an observations heuristic, add a test that constructs a
`ProjectReport` with synthetic signals and asserts the resulting
`Observations` fields (progress, flags, evidence).

## Conventions

- **Indentation:** tabs in Python files. The existing files set the
  precedent; please match.
- **No emojis** in source, comments, or docs unless explicitly
  requested.
- **Python ≥ 3.10.** Avoid 3.11+ syntax (no `tomllib` imports, no
  `Self` from typing without a guarded import, no PEP 695 generics).
  In particular, do not put backslash escapes inside f-string
  expressions — use a module constant.
- **MIT licensed.** Add a header only if you must; the LICENSE file
  covers the repo.
- **Time is always UTC.** Never store or compare naive datetimes.
  `Signal.__post_init__` enforces this; don't try to work around it.

## Releasing

There is no formal release process yet — the repo is a personal tool
on `main`, no tags, no PyPI. If that changes, this section will too.
