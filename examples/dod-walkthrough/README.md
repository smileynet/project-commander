# `dod` walkthrough — replaying a real epic

Reproducible end-to-end demonstration of the `dod` subcommand, replaying
the actual arc of [`line-cook-web`](https://github.com/smileynet/line-cook)'s
`demo-4o4` epic — *Phase 4: Workflow Progress Indicators*. The
acceptance document ([`docs/features/demo-4o4-acceptance.md`](https://github.com/smileynet/line-cook-web/blob/main/docs/features/demo-4o4-acceptance.md)
in that repo) lists the seven explicit acceptance criteria the project's
author defined before the work landed; this walkthrough turns them into
the closure criteria of a `DOD.md`, then walks the project from
"plan committed" to "epic plated" across six milestones, running `dod`
at each one.

The criteria are concrete and feature-specific — names of files,
named ACs, sign-off roles. The mechanical hygiene checks
(`Working tree clean`, `Pushed to origin`, `Verify passes`) ride
alongside as backdrop, not the main story.

## Run it

From the repo root:

```sh
PCMD=".venv/bin/python -m project_commander" \
  examples/dod-walkthrough/walkthrough.sh
```

Or, if `project-commander` is installed on `PATH`:

```sh
examples/dod-walkthrough/walkthrough.sh
```

The script builds a sandbox under `$(mktemp -d)`, runs the journey, and
deletes the sandbox at the end. Pass `--keep` to leave it. Pass
`--no-llm` to force deterministic output (no LLM observations). When
an LLM provider is auto-detected (`ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `OLLAMA_HOST`), each milestone also prints a
2-3 sentence observation comparing the current state to the DoD.

Captured per-step outputs are written under `/tmp/dod-walkthrough-out.*`
regardless of mode. The deterministic versions of those outputs are
tracked in this directory under [`expected-outputs/`](expected-outputs/);
LLM-narrated samples live there too, with the model's observation
appended below the mechanical status table.

## The Definition of Done

The walkthrough commits this `DOD.md` at the top of the journey:

```markdown
# Definition of Done — Phase 4: Workflow Progress Indicators

## Acceptance criteria (7 ACs from the spec)

- [ ] AC1 stepper component: file internal/web/templates/workflow_progress.templ exists
- [ ] AC2/AC3 phase detection logic: file internal/workflow/detect.go exists
- [ ] AC3 SSE broadcast tracker: file internal/workflow/tracker.go exists
- [ ] AC4 command center badge: file internal/web/templates/command_center.templ exists
- [ ] AC6 persistence layer: file internal/db/session_phases.go exists
- [ ] AC7 demo mode data: file internal/demo/workflow.go exists
- [ ] BDD test coverage: file tests/e2e/workflow_progress_test.go exists
- [ ] Acceptance doc published at docs/features/demo-4o4-acceptance.md

## Mechanical hygiene

- [ ] Working tree clean
- [ ] Pushed to origin
- [ ] All plan phases complete
- [ ] Verify passes

## Manual sign-off (kitchen staff review)

- [ ] Maitre BDD review APPROVED
- [ ] Sous-chef code quality review APPROVED
- [ ] Critic E2E coverage review PASS
- [ ] All 7 ACs verified end-to-end in browser
```

Eight criteria are anchored to file paths (`file_exists` auto-check
resolves them as the implementation lands). Four are mechanical
hygiene (already-shipping checks). Four are user-attestation reviews
that stay `MANUAL` until the user ticks `[x]`. Sixteen total.

## The journey

The script advances the project through six commit milestones that
mirror the real history in `line-cook-web`:

| step | what changed                                            | mirrors commit | progress  | percent |
|------|---------------------------------------------------------|----------------|-----------|---------|
| T0   | plan landed; nothing implemented                        | `8f2d28c`      | 3/16      | 19%     |
| T1   | persistence layer landed (`session_phases.go`)          | `17ce368`      | 4/16      | 25%     |
| T2   | UI component landed (`workflow_progress.templ`)         | `41d87d0`      | 5/16      | 31%     |
| T3   | detection + tracker + BDD tests landed                  | `4c5fc17` + `6f08a02` | 9/16 | 56%   |
| T4   | demo mode data landed                                   | `de25494`      | 10/16     | 62%     |
| T5   | acceptance doc + manual sign-offs                       | `47a6582`      | 16/16     | 100%    |

Each transition runs `dod` and the next-action pivots to the next
specific blocker by name:

```
T0    Address `AC1 stepper component: ...workflow_progress.templ exists`: not found ... (19%)
T1    Address `AC1 stepper component: ...workflow_progress.templ exists`: not found ... (25%)
T2    Address `AC2/AC3 phase detection logic: ...detect.go exists`: not found ...        (31%)
T3    Address `AC7 demo mode data: ...workflow.go exists`: not found ...                 (56%)
T4    Address `Acceptance doc published at docs/features/...`: not found ...             (62%)
T5    Definition of Done met.                                                            (100%)
```

## T1 — mid-flight (mechanical-only)

After `session_phases.go` lands. One AC verified, seven still failing.
Source: [`expected-outputs/01-T1-mid-flight.txt`](expected-outputs/01-T1-mid-flight.txt).

```
line-cook-web  DoD OPEN  DOD.md

  [██████░░░░░░░░░░░░░░░░░░]  4/16  (25%)

  [  FAIL]  AC1 stepper component: file internal/web/templates/workflow_progress.templ exists
            not found: internal/web/templates/workflow_progress.templ
  [  FAIL]  AC2/AC3 phase detection logic: file internal/workflow/detect.go exists
            not found: internal/workflow/detect.go
  [  FAIL]  AC3 SSE broadcast tracker: file internal/workflow/tracker.go exists
            not found: internal/workflow/tracker.go
  [  FAIL]  AC4 command center badge: file internal/web/templates/command_center.templ exists
            not found: internal/web/templates/command_center.templ
  [  PASS]  AC6 persistence layer: file internal/db/session_phases.go exists
            found at internal/db/session_phases.go
  [  FAIL]  AC7 demo mode data: file internal/demo/workflow.go exists
            not found: internal/demo/workflow.go
  [  FAIL]  BDD test coverage: file tests/e2e/workflow_progress_test.go exists
            not found: tests/e2e/workflow_progress_test.go
  [  FAIL]  Acceptance doc published at docs/features/demo-4o4-acceptance.md
            not found: docs/features/demo-4o4-acceptance.md
  [  PASS]  Working tree clean
  [  PASS]  Pushed to origin           at parity with origin/main
  [  FAIL]  All plan phases complete   8 unchecked item(s), 5 open phase(s)
  [  PASS]  Verify passes              5 of 5 checks passed (rest skipped)
  [MANUAL]  Maitre BDD review APPROVED
  [MANUAL]  Sous-chef code quality review APPROVED
  [MANUAL]  Critic E2E coverage review PASS
  [MANUAL]  All 7 ACs verified end-to-end in browser

  Next: Address `AC1 stepper component: ...workflow_progress.templ exists`: not found ...
```

The `next_action` names the specific AC and the specific file. The
report is no longer telling the user "make the working tree clean";
it's telling them which acceptance criterion ships next.

## With agent-generated observation

When an LLM provider is configured, every milestone gets a 2-3 sentence
observation appended below the mechanical table. The observation reads
the same `DOD.md` criteria the user wrote and grounds its prose in the
file paths and AC names from the criteria, plus the recent commits.

The samples below are real LLM output captured during a walkthrough
run with Ollama serving `qwen2.5:3b`. Source files:
[`07-T1-with-llm-observation.txt`](expected-outputs/07-T1-with-llm-observation.txt) ·
[`08-T3-with-llm-observation.txt`](expected-outputs/08-T3-with-llm-observation.txt) ·
[`09-T5-with-llm-observation.txt`](expected-outputs/09-T5-with-llm-observation.txt).

> **T1** (25% — persistence layer just landed)
>
> AC7 demo mode data and BDD test coverage are the only new passed
> criteria since last check, but AC1, AC3, and AC4 are still not met.
> The missing files detail suggests these components need to be added
> or updated.

> **T3** (56% — detection + tracker + BDD tests landed; demo mode + doc still missing)
>
> AC7 demo mode data is missing as `internal/demo/workflow.go` was not
> found, blocking further progress.

> **T5** (100% — DoD met)
>
> The project has completed all defined criteria, including the
> persistence layer and acceptance documentation. The most recent
> commit added workflow progress indicators in the demo phase.

A few properties worth naming:

- **The observation is grounded in the criteria the user wrote.** It
  references AC1, AC7, `internal/demo/workflow.go`, "the persistence
  layer" — language that came from the `DOD.md` and the recent commit
  subjects. It is not free-form commentary about the project.
- **The mechanical status table is still the source of truth.** The
  observation sits below it. If the LLM call fails (no provider,
  timeout, malformed JSON, too-short response), the rest of the report
  renders unchanged — the deterministic core is untouched. Pass
  `--no-llm` to force deterministic output.
- **The observation is content-cached.** Re-running with the same DoD
  state and the same model returns the cached observation byte-for-byte
  from `$XDG_CACHE_HOME/project-commander/narrative/`. Any change to
  the DoD or the project state changes the cache key.
- **The model size matters.** With `qwen2.5:3b` (a small local model)
  the observations are useful but occasionally imprecise — the T1
  example incorrectly states AC7 passed. Larger models
  (`claude-3-5-haiku-latest`, `gpt-4o-mini`) follow the system prompt
  more strictly. The mechanical status doesn't depend on either.

## T5 — DONE

After the acceptance doc lands and the four manual reviews are ticked.
Source: [`expected-outputs/04-T5-shipped.txt`](expected-outputs/04-T5-shipped.txt).

```
line-cook-web  DoD DONE  DOD.md

  [████████████████████████]  16/16  (100%)

  [  PASS]  AC1 stepper component: ...workflow_progress.templ exists
  [  PASS]  AC2/AC3 phase detection logic: internal/workflow/detect.go exists
  [  PASS]  AC3 SSE broadcast tracker: internal/workflow/tracker.go exists
  [  PASS]  AC4 command center badge: ...command_center.templ exists
  [  PASS]  AC6 persistence layer: internal/db/session_phases.go exists
  [  PASS]  AC7 demo mode data: internal/demo/workflow.go exists
  [  PASS]  BDD test coverage: tests/e2e/workflow_progress_test.go exists
  [  PASS]  Acceptance doc published at docs/features/demo-4o4-acceptance.md
  [  PASS]  Working tree clean
  [  PASS]  Pushed to origin           at parity with origin/main
  [  PASS]  All plan phases complete   all checkboxes ticked, all phases complete
  [  PASS]  Verify passes              5 of 5 checks passed (rest skipped)
  [  DONE]  Maitre BDD review APPROVED        user-confirmed
  [  DONE]  Sous-chef code quality review APPROVED   user-confirmed
  [  DONE]  Critic E2E coverage review PASS   user-confirmed
  [  DONE]  All 7 ACs verified end-to-end in browser  user-confirmed
```

`DoD DONE` in the header. Exit code is 0. The eight `PASS` rows are
mechanically verified: each file exists at its declared path, the tree
is clean, the branch is at parity, the plan is complete, the tool's
own `verify` checks all pass. The four `DONE` rows are
user-attestation: `[x]` ticked in the source `DOD.md`. The walkthrough
asserts this exit-code contract internally — if a future change to
`dod` ever stopped honoring "all PASS + DONE → exit 0", the script
fails loudly.

## Fleet roll-up across multiple projects

The walkthrough adds two sibling projects to demonstrate the fleet
shape: `api-rewrite` (its own concrete DoD, intentionally OPEN with a
dirty tree) and `quiet-tool` (no `DOD.md` at all). Source:
[`expected-outputs/06-fleet-rollup.txt`](expected-outputs/06-fleet-rollup.txt).

```
  OPEN  [░░░░░░░░░░░░]  0/4   api-rewrite     Address `Schema introspection at api/schema.graphql exists`: ...
  DONE  [████████████]  16/16 line-cook-web   Definition of Done met.

1 of 2 project(s) with DOD.md are complete.
1 project(s) have no DOD.md: quiet-tool
```

Sort order: incomplete projects first (sorted descending by percent),
complete projects after, projects with no `DOD.md` summarized at the
bottom.

## The agent driver loop

The JSON form is the contract surface for chaining. A driver loop:

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
		print("Definition of Done met. Plate the epic.")
		break

	# Log the agent-generated observation alongside the percent and the
	# mechanical next_action — three different views of the same state.
	if state.get("observation"):
		print(f"[{state['percent']}%] {state['observation']}")
	print(f"  next: {state['next_action']}")

	fail = next((c for c in state["criteria"] if c["status"] == "FAIL"), None)
	if fail is not None:
		# fail['detail'] carries the concrete hint:
		#   "not found: internal/demo/workflow.go"
		#   "1 verify check(s) failed: branch_in_sync"
		handle_fail(fail)
		continue

	manual = next((c for c in state["criteria"] if c["status"] == "MANUAL"), None)
	if manual is not None:
		# Only the user can resolve the kitchen-staff sign-offs.
		prompt_user(manual["text"])
		break
```

Three properties make this work:

1. **Exit code is the loop condition** (`rc == 0` ⇒ done). No JSON
   parsing required just to stop.
2. **`next_action` is one criterion-specific string**, not a list — the
   agent doesn't have to pick a strategy. It always names the next
   blocker by AC and file.
3. **The `criteria` array is the structured backup** when the
   one-string `next_action` is too coarse. Each entry carries
   `status`, `detail`, `auto_check`, `user_checked`. The agent can
   reason at whatever granularity it needs.

The `observation` field is **advisory**, never load-bearing — the loop
above uses it as a status print, not as a decision input. The
mechanical `criteria` array drives every branch.

## What the demonstration shows about the design

Five properties that fall out of the walkthrough:

1. **The DoD names the work, not just the hygiene.** Six of the eight
   AC criteria are concrete file paths from the actual acceptance
   document. The progress percent moves because *features land*, not
   because the working tree got cleaner.

2. **`file_exists` is the smallest auto-check that buys the most
   ground.** Most of a feature's "did it ship?" question reduces to
   "does the artifact file exist?" — the templ component, the Go
   handler, the SQL migration, the test file, the acceptance doc. The
   tool resolves those automatically; everything else stays MANUAL,
   which is correct.

3. **The relative diff is criterion-by-criterion.** Between any two
   runs the agent gets per-criterion deltas (FAIL → PASS by AC name)
   and a single concrete `next_action`. T0→T5 here is 6 transitions
   each pivoting `next_action` to the next specific blocker.

4. **The agent observation grounds its prose in the user's criteria.**
   It does not say "the project has made progress on the dashboard."
   It says "AC7 demo mode data is missing as `internal/demo/workflow.go`
   was not found." The observation is built from the same data the
   mechanical table renders; it is a different *projection* of one
   shared state, not a parallel narrative.

5. **The mechanical core is the source of truth.** The observation is
   advisory. `--no-llm` produces a fully reproducible report. If the
   LLM provider fails, the report still renders. The exit code never
   depends on prose. Closure is mechanical.

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
    ├── 07-T1-with-llm-observation.txt T1 + LLM observation (qwen2.5:3b sample)
    ├── 08-T3-with-llm-observation.txt T3 + LLM observation
    └── 09-T5-with-llm-observation.txt T5 + LLM observation
```

The `01-` through `06-` files are deterministic — re-running
`walkthrough.sh --no-llm` reproduces them byte-for-byte. The `07-`
through `09-` files are real LLM-narrated outputs captured from one
walkthrough run; rerunning with a different model (or no model)
produces different observations, by design.

## See also

- [`AGENTS.md` § Definition-of-Done module](../../AGENTS.md) — file
  contract, status states, the auto-check registry (now including
  `file_exists`), narrator wiring, and the `[x]`-is-authoritative
  principle.
- [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) — user-facing
  fan-out of every subcommand including `dod`, plus the cheat sheet.
- [`tests/test_dod.py`](../../tests/test_dod.py) — unit tests covering
  the parser, every pattern (including `file_exists`), every status
  path, the narrator wiring, the aggregation arithmetic, and the
  end-to-end `run()` exit codes.
- The real `demo-4o4` epic acceptance document this walkthrough is
  modeled on:
  [`docs/features/demo-4o4-acceptance.md`](https://github.com/smileynet/line-cook-web/blob/main/docs/features/demo-4o4-acceptance.md).
