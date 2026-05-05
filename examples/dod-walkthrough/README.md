# `dod` walkthrough — replaying a real epic against an outcome target

Reproducible end-to-end demonstration of the `dod` subcommand. The
walkthrough replays the actual arc of [`line-cook-web`](https://github.com/smileynet/line-cook-web)'s
`demo-4o4` epic — *Phase 4: Workflow Progress Indicators* — from a
fresh plan to a plate-able feature, running `dod` at six milestones
along the way.

The point of this example is **not** "does the tool count files
correctly." The point is whether the tool can answer a single
question at every milestone:

> *Can the user do the thing yet?*

That question has an answer because the `DOD.md` says, in plain prose
at the top, what *the thing* is.

## The target

Every `DOD.md` should open with a `## Target` section: one user-
observable outcome statement the criteria support. The walkthrough
commits this:

```markdown
## Target

When this epic is done, an operator monitoring a Line Cook session
sees the active workflow phase (PREP → COOK → SERVE → TIDY) at a
glance on the dashboard, with live updates as phases transition. The
command center shows the current phase per project. Demo mode supports
exploration without a real session, and dashboard restart preserves
phase state.
```

Five concrete, testable user-experience outcomes. The criteria are
written in the same language — *what the user can do* — with a
parenthetical file path so the `file_exists` auto-check can verify
the artifact:

```markdown
- [ ] Operators see workflow phase progression on the dashboard
      (internal/web/templates/workflow_progress.templ exists)
- [ ] The dashboard knows when phases transition
      (internal/workflow/detect.go exists)
- [ ] Phase transitions broadcast live to the dashboard
      (internal/workflow/tracker.go exists)
- [ ] The command center shows current phase per project
      (internal/web/templates/command_center.templ exists)
- [ ] Phase state survives a dashboard restart
      (internal/db/session_phases.go exists)
- [ ] Operators can explore the dashboard without a live session
      (internal/demo/workflow.go exists)
- [ ] BDD coverage proves the operator-facing behavior
      (tests/e2e/workflow_progress_test.go exists)
- [ ] The epic is signed off and documented
      (docs/features/demo-4o4-acceptance.md exists)
```

Plus four mechanical hygiene checks (`Working tree clean`, `Pushed to
origin`, ...) and four kitchen-staff sign-offs (`Maitre BDD review
APPROVED`, ...). Sixteen criteria total, but the *story* the report
tells is the target above.

## Run it

From the repo root:

```sh
PCMD=".venv/bin/python -m project_commander" \
  examples/dod-walkthrough/walkthrough.sh
```

Or, if `project-commander` is on `PATH`:

```sh
examples/dod-walkthrough/walkthrough.sh
```

The script builds a sandbox under `$(mktemp -d)`, walks the project
through six commit milestones, runs `dod` at each one, and cleans up.
Pass `--keep` to retain the sandbox; pass `--no-llm` to force
deterministic output. When an LLM provider is auto-detected
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_HOST`), each
milestone also gets a 2-3 sentence observation describing distance
from the target.

## The journey

Six milestones mirror the real commit history:

| step | what shipped (in user terms)                            | mirrors        | progress | percent |
|------|---------------------------------------------------------|----------------|----------|---------|
| T0   | plan committed; no operator-facing behavior yet         | `8f2d28c`      | 3/16     | 19%     |
| T1   | phase state has somewhere to live (persistence)         | `17ce368`      | 4/16     | 25%     |
| T2   | the stepper UI exists (but it is not wired)             | `41d87d0`      | 5/16     | 31%     |
| T3   | operators see live phase progression end-to-end         | `4c5fc17`/`6f08a02` | 9/16 | 56%   |
| T4   | demo mode lets people explore without a real session    | `de25494`      | 10/16    | 62%     |
| T5   | epic is signed off; full target deliverable             | `47a6582`      | 16/16    | 100%    |

Each transition runs `dod` and the next-action pivots to the next
specific blocker — phrased in the same outcome language the criteria
are written in:

```
T0    Address `Operators see workflow phase progression on the dashboard ...`: not found ... (19%)
T1    Address `Operators see workflow phase progression on the dashboard ...`: not found ... (25%)
T2    Address `The dashboard knows when phases transition ...`: not found ...                (31%)
T3    Address `Operators can explore the dashboard without a live session ...`: not found ... (56%)
T4    Address `The epic is signed off and documented ...`: not found ...                     (62%)
T5    Definition of Done met.                                                                (100%)
```

The string in the user's `next_action` is the next *user outcome*
that's blocked, not "AC4 fails because file X is missing."

## T1 — mid-flight (mechanical-only)

Right after the persistence layer lands. One outcome verified, seven
still missing. Source: [`expected-outputs/01-T1-mid-flight.txt`](expected-outputs/01-T1-mid-flight.txt).

```
line-cook-web  DoD OPEN  DOD.md

  Target: When this epic is done, an operator monitoring a Line Cook session
  sees the active workflow phase (PREP → COOK → SERVE → TIDY) at a glance on
  the dashboard, with live updates as phases transition. The command center
  shows the current phase per project. Demo mode supports exploration without
  a real session, and dashboard restart preserves phase state.

  [██████░░░░░░░░░░░░░░░░░░]  4/16  (25%)

  [  FAIL]  Operators see workflow phase progression on the dashboard
            (internal/web/templates/workflow_progress.templ exists)
            not found: internal/web/templates/workflow_progress.templ
  [  FAIL]  The dashboard knows when phases transition
            (internal/workflow/detect.go exists)
            not found: internal/workflow/detect.go
  [  FAIL]  Phase transitions broadcast live to the dashboard
            (internal/workflow/tracker.go exists)
            not found: internal/workflow/tracker.go
  [  FAIL]  The command center shows current phase per project
            (internal/web/templates/command_center.templ exists)
            not found: internal/web/templates/command_center.templ
  [  PASS]  Phase state survives a dashboard restart
            (internal/db/session_phases.go exists)
            found at internal/db/session_phases.go
  [  FAIL]  Operators can explore the dashboard without a live session
            (internal/demo/workflow.go exists)
            not found: internal/demo/workflow.go
  [  FAIL]  BDD coverage proves the operator-facing behavior
            (tests/e2e/workflow_progress_test.go exists)
            not found: tests/e2e/workflow_progress_test.go
  [  FAIL]  The epic is signed off and documented
            (docs/features/demo-4o4-acceptance.md exists)
            not found: docs/features/demo-4o4-acceptance.md
  [  PASS]  Working tree clean
  [  PASS]  Pushed to origin           at parity with origin/main
  [  FAIL]  All plan phases complete   8 unchecked item(s), 5 open phase(s)
  [  PASS]  Verify passes              5 of 5 checks passed (rest skipped)
  [MANUAL]  Maitre BDD review APPROVED
  [MANUAL]  Sous-chef code quality review APPROVED
  [MANUAL]  Critic E2E coverage review PASS
  [MANUAL]  All 7 ACs verified end-to-end in browser

  Next: Address `Operators see workflow phase progression on the dashboard
  (internal/web/templates/workflow_progress.templ exists)`: not found ...
```

The Target sits at the top so a reader who scans top-to-bottom learns
*what's being built* before *what's missing*. The next-action reads
like a user-outcome blocker, with the file path as supporting evidence.

## With agent-generated observation

When an LLM provider is configured, each milestone gets a 2-3 sentence
observation appended below the table. The observation is grounded in
the **target**, not the criteria list — the system prompt explicitly
forbids enumerating which ACs passed or naming file paths. The reader
already sees that table.

> **The observation answers one question:** *Can the user do the thing
> yet, and what's blocking them if not?*

Real samples captured live from a walkthrough run with Ollama serving
`qwen2.5:3b` (a small 3B-parameter local model). Source files:
[`07-T1-with-llm-observation.txt`](expected-outputs/07-T1-with-llm-observation.txt) ·
[`08-T3-with-llm-observation.txt`](expected-outputs/08-T3-with-llm-observation.txt) ·
[`09-T5-with-llm-observation.txt`](expected-outputs/09-T5-with-llm-observation.txt).

> **T1** (25% — persistence layer just landed; UI not yet built)
>
> Operators cannot yet see the active workflow phase at a glance on
> the dashboard, as the template file
> `internal/web/templates/workflow_progress.templ` does not exist.

> **T3** (56% — UI + detection + tracker + tests landed; demo mode missing)
>
> Operators can see the active workflow phase (PREP → COOK → SERVE →
> TIDY) on the dashboard with live updates, but cannot explore the
> dashboard without a real session in demo mode, so full exploration
> functionality is not yet deliverable.

> **T5** (100% — full target deliverable)
>
> Operators can now see the active workflow phase (PREP → COOK →
> SERVE → TIDY) on the dashboard with live updates, but they still
> cannot explore the demo mode without a real session.
> *(Note: this T5 observation is wrong — demo mode IS deliverable at
> T5; this is a model accuracy slip, not a prompt-shape problem. See
> the model-size note below.)*

The shape is correct in all three: outcome-language, no AC numbers,
no test files, "operators can ... but cannot yet ..." structure. The
T1 and T3 observations are also factually accurate. The T5
observation is the kind of thing the mechanical table guards against:
the user reading the report sees `16/16 (100%) DoD DONE` in the header
and 16 PASS/DONE rows in the table, and treats the prose as
commentary, not as the closure decision.

### Model size matters

The samples above come from `qwen2.5:3b`, a small local model. With a
3B-parameter model the *shape* is reliable (outcome language, no
enumeration) but *factual accuracy* is mixed: roughly 4/6 milestones
in this walkthrough produced accurate observations; the others
confabulated user-outcome claims that the criteria statuses
contradicted. Larger models (`claude-3-5-haiku-latest`,
`gpt-4o-mini`) follow the system prompt's grounding rules more
strictly and produce accurate observations more consistently.

This is exactly why the design treats the observation as
**advisory**: the mechanical status, percent, `next_action`, and exit
code are all derived deterministically from the criteria. The
observation is a layer on top, useful when accurate, ignorable when
not. Pass `--no-llm` to skip it entirely.

### What the LLM is *not* allowed to do (per the system prompt)

- ✗ "AC1 and AC3 are PASS but AC4 is FAIL because `command_center.templ`
  has not been created."
- ✗ "The persistence layer file at `internal/db/session_phases.go`
  exists, and the BDD test coverage criterion has been met."
- ✗ "5 of 8 file_exists criteria are now passing."

### What the LLM *is* asked to do

- ✓ "Operators can now see workflow phase progression on the
  dashboard with live updates, but the command center still does not
  show per-project phase, so the multi-project overview is not
  deliverable yet."
The observation is **advisory**, never load-bearing. The mechanical
status table, the percent, the `next_action`, and the exit code are
all unaffected by the narrator. Failures (no provider, transport
error, malformed JSON, too-short response) are caught and surface as
`observation=""` — the deterministic core never breaks. Pass `--no-llm`
to force deterministic output.

## T5 — DONE

After the acceptance doc lands and the four manual reviews are ticked.
Source: [`expected-outputs/04-T5-shipped.txt`](expected-outputs/04-T5-shipped.txt).

```
line-cook-web  DoD DONE  DOD.md

  Target: When this epic is done, an operator monitoring a Line Cook session
  sees the active workflow phase (PREP → COOK → SERVE → TIDY) ...

  [████████████████████████]  16/16  (100%)

  [  PASS]  Operators see workflow phase progression on the dashboard ...
  [  PASS]  The dashboard knows when phases transition ...
  [  PASS]  Phase transitions broadcast live to the dashboard ...
  [  PASS]  The command center shows current phase per project ...
  [  PASS]  Phase state survives a dashboard restart ...
  [  PASS]  Operators can explore the dashboard without a live session ...
  [  PASS]  BDD coverage proves the operator-facing behavior ...
  [  PASS]  The epic is signed off and documented ...
  [  PASS]  Working tree clean
  [  PASS]  Pushed to origin           at parity with origin/main
  [  PASS]  All plan phases complete   all checkboxes ticked, all phases complete
  [  PASS]  Verify passes              5 of 5 checks passed (rest skipped)
  [  DONE]  Maitre BDD review APPROVED        user-confirmed
  [  DONE]  Sous-chef code quality review APPROVED   user-confirmed
  [  DONE]  Critic E2E coverage review PASS   user-confirmed
  [  DONE]  All 7 ACs verified end-to-end in browser  user-confirmed
```

`DoD DONE` in the header. Exit code 0. The full target — operators see
phase progression at a glance, command center shows current phase,
demo mode supports exploration, restart preserves state — is
deliverable.

## Fleet roll-up

The walkthrough adds two sibling sandbox projects: `api-rewrite`
(its own concrete DoD, intentionally OPEN with a dirty tree) and
`quiet-tool` (no `DOD.md` at all). Source:
[`expected-outputs/06-fleet-rollup.txt`](expected-outputs/06-fleet-rollup.txt).

```
  OPEN  [░░░░░░░░░░░░]  0/4   api-rewrite     Address `Schema introspection at api/schema.graphql exists`: ...
  DONE  [████████████]  16/16 line-cook-web   Definition of Done met.

1 of 2 project(s) with DOD.md are complete.
1 project(s) have no DOD.md: quiet-tool
```

## The agent driver loop

```python
import subprocess, json

def step():
	out = subprocess.run(
		["project-commander", "dod", "--project", "line-cook-web",
		 "--root", "/path/to/code", "--format", "json"],
		capture_output=True, text=True,
	)
	return json.loads(out.stdout), out.returncode

while True:
	state, rc = step()
	if rc == 0:
		print(f"Target met: {state['target']}")
		break

	# The target is the why. The observation describes distance from it.
	# The next_action is the single concrete blocker the tool picked.
	if state.get("target"):
		print(f"target:   {state['target']}")
	if state.get("observation"):
		print(f"observed: [{state['percent']}%] {state['observation']}")
	print(f"next:     {state['next_action']}")

	fail = next((c for c in state["criteria"] if c["status"] == "FAIL"), None)
	if fail is not None:
		handle_fail(fail)
		continue

	manual = next((c for c in state["criteria"] if c["status"] == "MANUAL"), None)
	if manual is not None:
		prompt_user(manual["text"])
		break
```

Three layers, one shared truth:

|Field|What it is|Used as|
|---|---|---|
|`target`|the user-observable outcome the project promised|the *why* — printed once, anchors the loop|
|`observation`|prose distance from the target|advisory print, never a decision input|
|`next_action`|one criterion-specific blocker the tool picked|the concrete action the agent or user takes|
|`criteria[]`|per-row mechanical state|structured backup when `next_action` is too coarse|
|exit code|0 if all PASS+DONE; 1 otherwise|the loop condition|

## What this demonstrates

1. **The Target is the contract.** It states what shipping looks like
   in user terms, before any criterion exists. Without it, "5/16
   complete" is meaningless — complete *toward what*?

2. **Criteria are written in outcome-first language.** "Operators see
   workflow phase progression on the dashboard" is what's being
   shipped; `internal/web/templates/workflow_progress.templ` is the
   evidence. The file path is in parentheses because it's secondary —
   the auto-check uses it; the human reading the report is told what
   the user gets.

3. **The relative diff is criterion-by-criterion in user terms.** As
   each milestone lands, the `next_action` pivots to the next *blocked
   user outcome* — not the next missing file.

4. **The observation answers "can the user do the thing yet?"** The
   system prompt is explicitly trained against criteria-enumeration
   prose. The mechanical table is below the observation; duplicating
   it in prose is forbidden. The observation lives in the user's
   language, not the project's.

5. **The mechanical core is the source of truth.** Run `--no-llm` for
   reproducibility. The exit code never depends on the observation.
   The deterministic path is fully documented in `expected-outputs/01-06`
   and verified byte-exact against fresh runs.

## What's in this directory

```
examples/dod-walkthrough/
├── README.md                 ← you are here
├── walkthrough.sh            ← reproducible T0→T5 journey
└── expected-outputs/
    ├── 01-T1-mid-flight.txt           terminal at T1 (25%, OPEN, --no-llm)
    ├── 02-T1-mid-flight.json          same state, agent-friendly
    ├── 03-T3-mid-flight.txt           terminal at T3 (56%, OPEN, --no-llm)
    ├── 04-T5-shipped.txt              terminal at T5 (100%, DONE, --no-llm)
    ├── 05-T5-shipped.md               markdown for a PR description
    ├── 06-fleet-rollup.txt            fleet view across 3 sandbox projects
    ├── 07-T1-with-llm-observation.txt T1 + LLM observation
    ├── 08-T3-with-llm-observation.txt T3 + LLM observation
    └── 09-T5-with-llm-observation.txt T5 + LLM observation
```

The `01-` through `06-` files are deterministic — re-running
`walkthrough.sh --no-llm` reproduces them byte-for-byte. The `07-`
through `09-` files are real LLM-narrated outputs from one walkthrough
run; rerunning with a different model produces different observations,
by design.

## See also

- [`AGENTS.md` § Definition-of-Done module](../../AGENTS.md) — file
  contract, the `## Target` section, status states, the auto-check
  registry, narrator wiring, and the `[x]`-is-authoritative principle.
- [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — user-facing
  fan-out of every subcommand including `dod`, plus the cheat sheet.
- [`tests/test_dod.py`](../../tests/test_dod.py) — unit tests covering
  parser, every pattern, target parsing, narrator wiring, and the
  end-to-end `run()` exit codes.
- The real `demo-4o4` epic acceptance document this walkthrough is
  modeled on:
  [`docs/features/demo-4o4-acceptance.md`](https://github.com/smileynet/line-cook-web/blob/main/docs/features/demo-4o4-acceptance.md).
