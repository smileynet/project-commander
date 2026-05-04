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
├── discovery.py      walk project root(s), basename glob filter
├── paths.py          per-tool cwd → session-key translations (pure functions)
├── models.py         Signal (frozen, UTC-enforced), ProjectReport,
│                     PlanDocSummary (checkbox + phase counts)
├── aggregator.py     build_report, build_all (threadpool fanout)
├── observations.py   Progress enum, ActivityWindow, Outstanding,
│                     clean_doc_prose, first_sentence, build()
├── tidy.py           hygiene: init / commit-stale / fetch / push / archive (prune)
├── catchup.py        delta digest since persisted cursor (J3 — catch up)
├── verify.py         per-project closure checks with structured exit code (J5 — verify)
├── audit.py          prompt → commit causality + ratios (J7 — audit agent behavior)
├── recap.py          per-period retrospective narrative (J6 — reconstruct)
├── narrative.py      optional LLM narrator (anthropic/openai/ollama) for
│                     detail-card sections + weekly recap; fail-soft to deterministic
├── report.py         render_table (J2 fleet) / render_review (J2 weekly) /
│                     render_detail (J1 briefing card) / render_json / render_markdown
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
├── test_scanners.py     synthetic-fixture tests for each scanner + aggregator
├── test_tidy.py         tidy planner + push-refusal + prune planner/executor tests
├── test_catchup.py      cursor persistence + since-parsing + classification
├── test_verify.py       per-check PASS/FAIL/SKIP + verdict aggregation + JSON shape
├── test_audit.py        prompt → commit window matching + ratios + flags
├── test_recap.py        category routing + narrative synthesis
├── test_narrative.py    narrator providers (mocked HTTP), cache, JSON parsing,
│                        wiring through render_detail + render_review_markdown
├── test_roots.py        multi-root discovery + dedup + env-var precedence
└── test_observations.py chrome stripping, sentence truncation, plan-doc parsing,
                        Outstanding builder, next-action synthesis
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
               progress enum, intent/workstream/open-issue/
               why-stopped text, flags, evidence, 7d/30d/90d activity windows
```

| Layer | Reads | Produces | Has business logic? |
|---|---|---|---|
| **Sources** (`sources/*.py`) | Disk (git, JSON, markdown, ...) | `list[Signal]` | No — only parsing |
| **Aggregator** (`aggregator.py`) | Source output | `ProjectReport` + `Observations` | Just orchestration |
| **Observations** (`observations.py`) | `ProjectReport` | `Observations` | Yes — all heuristics live here |
| **Renderer** (`report.py`) | `ProjectReport.observations` | Stdout | No — interprets nothing |

If a heuristic feels wrong, it lives in `observations.py`. The
renderer never decides what state a project is in.

## Root resolution

There is no single "the project root". Both `report` and `tidy`
resolve roots in this order, taking the first source that yields any:

1. **`--root <path>` flags** (repeatable on the command line).
2. **`PROJECT_COMMANDER_ROOTS` env var** (OS pathsep-separated:
   colon on POSIX, semicolon on Windows).
3. **Auto-detect under `$HOME`** — every existing folder named in
   `cli._DEFAULT_ROOT_NAMES`: `code`, `projects`, `src`, `dev`,
   `work`, `repos`, `git`, plus the macOS-cased variants `Code`,
   `Projects`, `Dev`. *All* matching folders are returned, in the
   declared order. A user with both `~/code` and `~/work` gets
   both scanned by default.

If the resolution returns nothing (no flags, no env var, none of
the conventional folders exist), the command prints a friendly
error pointing the user at `--root` / `PROJECT_COMMANDER_ROOTS`
and exits non-zero.

`discovery.discover_projects_in_roots(roots)` is the public entry
point that handles dedup-by-absolute-path across the multi-root
list. Symlinks are followed via `Path.resolve()`, so two roots
pointing at the same physical directory yield one entry, not two.

Adding a new root convention: append to `_DEFAULT_ROOT_NAMES` in
`cli.py` and add a parametrized case to `tests/test_roots.py`.
Don't add anything that is not a near-universal convention; the
`--root` flag and env var are there for one-offs.


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
   network, no real project-folder access.

The aggregator, observations layer, and renderer pick up the new
source automatically — they iterate over `report.signals` and group
by `(source, kind)`.

## Tidy module (`src/project_commander/tidy.py`)

The `tidy` subcommand applies hygiene actions to the fleet:
initialize folders that have content but no `.git/`, checkpoint
dirty trees that have been idle too long, optionally fetch and push.
It is wired into `cli.py` via a lightweight subcommand dispatch:

```python
if incoming and incoming[0] == "tidy":
    from . import tidy
    return tidy.main(incoming[1:])
```

Default invocation (`project-commander`) still routes to the report
renderer; flag-only invocations are unchanged.

### Three units, one rule

```
Action.INIT          for each non-git folder with visible content
Action.COMMIT_STALE  for each dirty repo idle >= --stale-age days
Action.FETCH         for each repo with a remote (when --sync)
Action.PUSH          for each repo with an upstream    (when --push)
```

**Every commit `tidy` makes carries the trailer
`Project-Commander-Hygiene: true`** (constant `HYGIENE_TRAILER`).
That trailer is the boundary between *work* and *housekeeping*. The
push executor refuses any branch that has at least one hygiene
commit in its `upstream..HEAD` range.

### Planning vs execution

`plan(report, config, now)` is **pure** — same inputs, same plan, no
subprocesses. It returns `list[PlannedAction]`. Tests live in
`tests/test_tidy.py` and don't shell out.

`execute(action, dry_run)` runs the actual git commands via the
private `_git()` helper (which uses `subprocess.run(check=False, ...)`,
so we can branch on return code). Each executor returns an
`ExecutedAction` with an `ok` flag plus stdout/stderr.

If you add a new action kind:

1. Extend the `Action` enum.
2. Add an executor `execute_<kind>()` returning `ExecutedAction`.
3. Register it in `_EXECUTORS`.
4. Decide in `plan()` when it should be queued.
5. Add a justfile recipe if it deserves a top-level alias.
6. Cover it in `tests/test_tidy.py`.

### Why not reuse `aggregator.build_all` for tidy discovery?

Tidy needs git state and a rough "last activity" timestamp — not
every agent's session history. Walking seven scanners across 80
projects to make a hygiene decision is wasteful. `_build_reports`
in `tidy.py` runs only the git scanner and falls back to filesystem
mtime (skipping `.git/`, `node_modules/`, caches, etc.) for
`last_active`. If you find yourself wanting more signals here,
consider whether what you really want is the full report.

### Safety guardrails

- `git commit --no-verify` is used so pre-commit hooks don't block
  hygiene commits.
- The push executor never runs `git push --force` and never amends.
- `_has_visible_content` ignores hidden files (so a folder with
  only `.DS_Store` does not get auto-init'd).
- `commit-stale` requires `last_active` to be present (a project
  with no signals at all is left alone).
- `--dry-run` always pre-renders the same plan that execution would
  follow, so nothing happens during preview.


## Narrative module (`src/project_commander/narrative.py`)

Optional LLM augmentation for three prose surfaces: the detail card's
*What is it / What's been happening / What's planned next* sections,
the weekly review's *Week in review* recap paragraph, and the recap
subcommand's per-project paragraphs. Everything else (status header,
*Where it stands*, Inspect footer, weekly triage table, all subcommand
JSON output) stays deterministic.

### Provider abstraction

Single protocol with two methods:

```python
class Narrator(Protocol):
    def narrate(self, inputs: NarrativeInputs) -> NarrativeOutput | None: ...
    def narrate_weekly(self, inputs: WeeklyInputs) -> WeeklyOutput | None: ...
```

Three provider impls reach external services over `urllib` (no new
dependencies). Each shares a `_chat(system, user, *, max_tokens)`
helper so the public methods only differ in prompt + parser:

| Provider | Trigger | Default model | Endpoint |
|---|---|---|---|
| `AnthropicNarrator` | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-latest` | `POST https://api.anthropic.com/v1/messages` |
| `OpenAINarrator` | `OPENAI_API_KEY` | `gpt-4o-mini` | `POST https://api.openai.com/v1/chat/completions` (with `response_format: json_object`) |
| `OllamaNarrator` | `OLLAMA_HOST` reachable | `llama3.1` | `POST <host>/api/chat` (with `format: json`, 120s timeout) |
| `DisabledNarrator` | otherwise | --- | (no call) |

Auto-detect order in `make_narrator`: anthropic > openai > ollama >
disabled. Override via `--llm-provider` / `--llm-model` flags or
`PROJECT_COMMANDER_LLM` / `PROJECT_COMMANDER_LLM_MODEL` env vars.

### Prompt contract

Per-project (`narrate`) input is one user message carrying capped
excerpts:

- Identity: up to 2 of `README.md` / `AGENTS.md` / `CLAUDE.md` /
  `GEMINI.md` (in that priority order), 600 chars each.
- Plan: first hit of `PLAN.md` / `ROADMAP.md` / `NEXT_STEPS.md` /
  `IMPROVEMENTS.md` / `TODO.md` (in that priority order), 1000 chars.
- Recent commits: 30 most recent subjects with dates.
- Recent substantive prompts: 15 most recent subjects with dates and
  source (procedural one-word approvals are filtered out by
  `is_procedural` before sending).
- Branch state: branch, dirty/uncommitted count, ahead/behind
  upstream, last activity timestamp.

Total per-project input: roughly 1500-2000 tokens.

Weekly (`narrate_weekly`) input is the aggregated triage data --- the
same shape the deterministic `render_review_markdown` produces ---
with totals (active projects, commits, substantive prompts), window
dates, and the three buckets (top 8 each as `(name, action, outcome)`
triples).

Both system prompts enforce: plain prose, no marketing language,
distinguish shipped from attempted, never invent project names or
outcomes. Output is strict JSON:

```
  narrate         {"what_it_is": "...", "whats_been_happening": "...", "whats_planned": "..."}
  narrate_weekly  {"week_in_review": "..."}
```

### Cache

`CachedNarrator` wraps any provider with a content-addressed JSON
cache under `$XDG_CACHE_HOME/project-commander/narrative/` (typically
`~/.cache/`).

Cache key:

```
  per-project:  SHA-256(model_id + "\n--\n" + build_user_message(inputs))
  weekly:       SHA-256(model_id + "::weekly" + "\n--\n" + build_weekly_message(inputs))
```

Two namespaces, one root directory. Any change to a project's
commits / prompts / docs --- or to the model id --- yields a different
key, so stale entries are simply never hit. There is no TTL.
Failure outputs are *not* cached: a transient API error never
poisons future runs.

### Fail-soft layers

Narration must never break a report. Three failure paths all return
`None` and trigger the deterministic fallback:

1. No provider configured (`DisabledNarrator`).
2. Provider call raises (timeout, 4xx, 5xx, malformed transport)
   --- caught in `_post_json`. Errors logged to stderr only when
   `PROJECT_COMMANDER_LLM_VERBOSE=1`.
3. Output JSON malformed, missing keys, or trivially short
   (`is_usable()` rejects fields < 8 chars for `narrate`, `< 16` for
   `narrate_weekly`).

When narration *is* used, the Inspect footer prepends
`_synthesized prose_` so the user knows the body was LLM-generated
and can rerun with `--no-llm` to compare.

### Where the narrator runs

Wired in `cli._run_report`, `recap.run`, and the `render_detail*` /
`render_review*` helpers in `report.py`. Default-on where the
marginal value is high:

| Surface | LLM call count | Default |
|---|---|---|
| `report --project NAME` | 1 per project (cached) | on |
| `report --since N` (N ≤ 30) | 1 per invocation, not per project | on |
| `recap` | 1 per project (cached) | on |
| `report` (fleet table), `report --since` >30 | --- | off |
| `tidy` / `verify` / `audit` / `catchup` | --- | off (state, not narrative) |

Pass `--no-llm` anywhere to force deterministic synthesis.

### Adding a new provider

1. Create a `@dataclass` provider class with `_chat(system, user,
   *, max_tokens) -> str | None`.
2. Add `narrate(self, inputs)` and `narrate_weekly(self, inputs)`
   that build the right user message and parse the response (or
   reuse the shared `parse_response` / `parse_weekly_response`
   helpers).
3. Wire into `make_narrator` with an env-var trigger and a default
   model.
4. Add a CLI choice in `cli._add_common_scan_args` (`--llm-provider`).
5. Add a smoke test in `tests/test_narrative.py` mocking
   `_post_json` so the test never hits the network.

## Definition-of-Done module (`src/project_commander/dod.py`)

The `dod` subcommand is the user-defined complement to `verify`. `verify`
ships five hardcoded closure checks that apply to every project the same
way; `dod` reads a per-project `DOD.md` checklist and reports progress
against whatever criteria the project's author wrote.

### File contract

Discovery is case-insensitive over the project root. Recognized names:
`DOD.md`, `DEFINITION_OF_DONE.md`, `DEFINITION-OF-DONE.md`. `--file PATH`
overrides the default lookup (only meaningful when the run targets a
single project via `--project NAME`).

Anything that isn't a `- [ ]` / `- [x]` checkbox line is ignored, so the
file may carry headings, prose, comments, or grouping --- the parser only
looks at the checkboxes. Markdown chrome (`**bold**`, `*italic*`,
`` `code` ``) inside the criterion text is stripped before pattern
matching.

### Status states

Five outcomes per criterion, mapped onto the user's view of "done":

|State|Meaning|
|---|---|
|`PASS`|auto-check matched and passed|
|`FAIL`|auto-check matched and failed|
|`DONE`|user marked the source line `[x]` (hand-confirmed)|
|`MANUAL`|no auto-check pattern matched; awaiting user confirmation|
|`SKIP`|auto-check matched but is not applicable in this project (e.g. `branch_in_sync` with no upstream configured)|

Roll-up:

	complete    = PASS + DONE
	outstanding = FAIL + MANUAL
	progress    = complete / (complete + outstanding)   (SKIP excluded)
	is_complete = file_exists and outstanding == 0 and total_relevant > 0

Exit code is 1 when any project has outstanding work, 0 otherwise ---
mirroring `verify` so the same chaining patterns work.

### Auto-check registry

Each entry is a `_Pattern(name, regex, check)` tuple. The first matching
regex wins, so order matters --- more specific phrasings come first.
Currently shipped:

|name|matches phrases like|delegates to|
|---|---|---|
|`verify_all`|"verify passes", "all verify checks pass"|`verify.verify_one`|
|`working_tree_clean`|"working tree clean", "no uncommitted", "no dirty"|`verify._check_working_tree`|
|`branch_in_sync`|"pushed to origin", "in sync with upstream", "no unpushed"|`verify._check_branch_sync`|
|`branch_is_main`|"on main branch", "branch is master/trunk"|local check|
|`no_orphan_thread`|"no orphan threads", "every prompt has a follow-up commit"|`verify._check_orphan_thread`|
|`no_plan_drift`|"no plan-drift", "plan matches reality"|`verify._check_plan_drift`|
|`plan_complete`|"plan complete", "all phases done", "every checkbox checked"|local check on `PlanDocSummary`|
|`prompts_substantive`|"substantive prompts", "no procedural-only prompts"|`verify._check_substantive_prompts`|

Patterns are conservative on purpose: an unmatched criterion becomes
`MANUAL` rather than risk a false `PASS`. False `MANUAL` is recoverable
(the user adds a pattern, or ticks `[x]`); a false `PASS` is a closure
lie.

### `[x]` is authoritative

A user-checked line is taken as `DONE` even if its text would otherwise
match an auto-check that would FAIL. This is by design: the user has
hand-confirmed something the tool cannot reliably observe, and we don't
undermine that. Auditors who want the strict view keep items unchecked
and rely on `PASS`.

### Adding a new auto-check

1. Write `_check_<name>(report) -> CheckResult` returning `PASS` / `FAIL`
   / `SKIP` with a short `detail` string. Reuse `verify` primitives where
   possible; only add a fresh check when the closure question is genuinely
   new.
2. Append a `_Pattern(name, regex, check)` to `_PATTERNS` in `dod.py`.
   Order it by specificity --- shorter / more general phrasings go later
   so a more specific match wins.
3. Add coverage to `tests/test_dod.py`:
   - one canonical-phrasing match in `test_match_pattern_recognizes_canonical_phrasings`
   - one PASS test and one FAIL test (and a SKIP test if the check has a
     skip path) using `_report` + `evaluate_items`
4. If the new check needs a signal source that doesn't exist yet (e.g.
   "tests pass" --- there's no test scanner today), add the scanner first
   following the rules under [`## Adding a new source`](#adding-a-new-source);
   pattern matching without a real signal would only ever return `SKIP`.

### Where the subcommand runs

|Surface|Use|
|---|---|
|`dod --project NAME`|terminal table for one project: progress bar + criteria with status + Next action|
|`dod --project NAME --format json`|single-object structured output for agent chaining|
|`dod`|fleet roll-up: one summary line per project that has a `DOD.md`; projects without are summarized at the bottom|
|`dod --format markdown`|GFM table per project for `reports/` artifacts|

`dod` does **not** invoke the LLM narrator. The criteria-by-criteria
status is mechanical and deterministic by design --- a "did you ship?"
answer that is reproducible turn-to-turn is the entire point of the
feature.

`DOD.md` itself is **not** picked up by `DocsScanner`. The file is owned
by the `dod` subcommand alone; treating it as a regular doc signal would
bleed closure criteria into the identity / planning extraction in
`observations.py`.

## Adding a new subcommand

Subcommands beyond `report` and `tidy` (catchup, verify, audit, recap, dod)
follow a consistent pattern:

1. Create `src/project_commander/<name>.py` with:
   - One or more dataclasses for output rows (`@dataclass(frozen=True)`).
   - A pure entry function (e.g. `synthesize`, `classify`, `verify_one`)
     that takes `ProjectReport` + an `Observations` view and returns
     structured output. Pure means same inputs → same output, no
     subprocesses, no I/O.
   - `add_subparser(subparsers)` that registers the command and calls
     `cli._add_common_scan_args(parser)` for shared flags.
   - `run(args)` that calls `cli.build_reports(args, *,
     git_recent_commits=N)` and dispatches to the renderer.

2. Wire the subparser in `cli.main`:
   ```python
   from . import <name>
   <name>.add_subparser(sub)
   ```

3. If your subcommand needs more git history than the default 50
   commits/project (e.g. `audit` uses 200, `recap` uses 500), pass
   `git_recent_commits=N` to `build_reports`.

4. If your subcommand needs persistent state across runs (cursors,
   caches), use `$XDG_STATE_HOME` / `$XDG_CACHE_HOME` with the
   `project-commander/` namespace. Catchup is the example.

5. Add `tests/test_<name>.py`:
   - Pure-logic tests that build synthetic `ProjectReport`s and assert
     on the entry function's structured output.
   - Renderer tests on the JSON / markdown / terminal forms.
   - End-to-end tests on `run(args)` only when the integration is the
     point.

6. If you add a new `SignalKind` along the way, that's a model change
   --- update `models.SignalKind`, every reader that pattern-matches
   on kinds, and `tests/test_scanners.py`.


## Candidate signal sources

The seven scanners shipped today cover git + the major agent CLIs +
in-tree docs. There are still high-value signal sources outside that
set; this section names them so anyone implementing one starts with
the same map.

### bd (beads) — graph issue tracker

[`bd`](https://github.com/gastownhall/beads) is a distributed graph
issue tracker designed for coding agents. Several projects already use it (`bd onboard` is the convention).

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
`code_root` per test using `tmp_path` and never touch a real project
folder or run any network calls.
`tests/test_scanners.py` covers each source plus the
aggregator and observations layer; `tests/test_tidy.py` covers the
pure planner and the push-refusal logic (using a sandboxed local
bare repo — still no network).

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
