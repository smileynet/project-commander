#!/usr/bin/env bash
# Reproducible demonstration of the `dod` subcommand against a concrete
# feature definition.
#
# Replays the real arc of `line-cook-web`'s `demo-4o4` epic — Phase 4:
# Workflow Progress Indicators — using the project's actual acceptance
# document (docs/features/demo-4o4-acceptance.md in that repo) as the
# source for the closure criteria. The DoD checked against here is the
# same shape the project's author would have committed *before* doing
# the work: seven file-anchored AC checks, four mechanical checks, and
# four manual sign-offs.
#
# The sandbox is a fresh git repo with a local bare remote, walked
# through six milestones that mirror the real commit arc:
#
#   T0  plan landed; nothing else                       (matches 8f2d28c)
#   T1  session_phases.go added                         (matches 17ce368)
#   T2  workflow_progress.templ added                   (matches 41d87d0)
#   T3  detect.go + tracker.go + BDD tests added        (matches 4c5fc17 + 6f08a02)
#   T4  demo mode workflow data added                   (matches de25494)
#   T5  acceptance doc published; manual sign-offs in   (matches 47a6582)
#
# Usage:
#   ./walkthrough.sh                run; delete sandbox at the end
#   ./walkthrough.sh --keep         leave the sandbox at $SANDBOX
#
#   PCMD=project-commander \
#     ./walkthrough.sh              override the binary used to invoke dod
#                                   (default: `python -m project_commander`)
#
# When an LLM provider is auto-detected (ANTHROPIC_API_KEY / OPENAI_API_KEY
# / OLLAMA_HOST), each milestone also prints a 2-3 sentence observation
# comparing the current state to the DoD. Pass `--no-llm` (set NO_LLM=1)
# to force deterministic mechanical-only output.

set -euo pipefail

PCMD=${PCMD:-python -m project_commander}
SANDBOX=${SANDBOX:-$(mktemp -d -t dod-walkthrough.XXXXXX)}
KEEP=0
NO_LLM_FLAG=""

for arg in "$@"; do
	case "$arg" in
		--keep)   KEEP=1 ;;
		--no-llm) NO_LLM_FLAG="--no-llm" ;;
	esac
done
if [[ "${NO_LLM:-0}" == "1" ]]; then
	NO_LLM_FLAG="--no-llm"
fi

git_init() {
	local dir=$1
	git -C "$dir" init -q -b main
	git -C "$dir" config user.email "demo@example.com"
	git -C "$dir" config user.name  "Demo"
}

run_dod() {
	# Single argument is a label used for the captured output filename.
	local label=$1
	shift
	local out=/tmp/dod-walkthrough-out.$label
	# shellcheck disable=SC2086
	$PCMD dod --root "$SANDBOX/code" $NO_LLM_FLAG "$@" >"$out" 2>&1 || true
	echo "$out"
}

headline() {
	local jsonfile=$1
	awk -F'"' '
		/"percent"/    { gsub(/[^0-9]/, "", $0); pct=$0 }
		/"next_action"/{ print $4 " (" pct "%)"; exit }
	' "$jsonfile"
}

step_header() {
	echo
	echo "=================================================================="
	echo " $1"
	echo "=================================================================="
}

cleanup() {
	if [[ $KEEP -eq 0 ]]; then
		rm -rf "$SANDBOX"
		echo
		echo "(sandbox cleaned up; pass --keep to retain it)"
	else
		echo
		echo "sandbox left at: $SANDBOX"
	fi
}
trap cleanup EXIT

echo "sandbox: $SANDBOX"
echo "pcmd:    $PCMD"

# ──────────────────────────────────────────────────────────────────────
# Sandbox layout
# ──────────────────────────────────────────────────────────────────────
mkdir -p "$SANDBOX/origin.git" "$SANDBOX/code/line-cook-web"
git -C "$SANDBOX/origin.git" init --bare -q -b main

PROJECT=$SANDBOX/code/line-cook-web
git_init "$PROJECT"
git -C "$PROJECT" remote add origin "$SANDBOX/origin.git"

cat > "$PROJECT/README.md" <<'README'
# line-cook-web

Real-time web dashboard for monitoring Line Cook autonomous coding
workflows. Operators view current workflow phase (PREP → COOK →
SERVE → TIDY) per project, live-updated via SSE.
README

git -C "$PROJECT" add README.md
git -C "$PROJECT" commit -q -m "initial scaffold"
git -C "$PROJECT" push -q -u origin main

# Definition of Done, written up front. The `## Target` section names the
# user-observable outcome the criteria support; each criterion is phrased
# in outcome-first language with a parenthetical file-anchor so the
# `file_exists` auto-check still resolves it. Mechanical hygiene and
# human sign-offs follow as backdrop.
cat > "$PROJECT/DOD.md" <<'DOD'
# Definition of Done — demo-4o4

## Target

When this epic is done, an operator monitoring a Line Cook session sees
the active workflow phase (PREP → COOK → SERVE → TIDY) at a glance on
the dashboard, with live updates as phases transition. The command
center shows the current phase per project. Demo mode supports
exploration without a real session, and dashboard restart preserves
phase state.

## What ships when this is done

- [ ] Operators see workflow phase progression on the dashboard (internal/web/templates/workflow_progress.templ exists)
- [ ] The dashboard knows when phases transition (internal/workflow/detect.go exists)
- [ ] Phase transitions broadcast live to the dashboard (internal/workflow/tracker.go exists)
- [ ] The command center shows current phase per project (internal/web/templates/command_center.templ exists)
- [ ] Phase state survives a dashboard restart (internal/db/session_phases.go exists)
- [ ] Operators can explore the dashboard without a live session (internal/demo/workflow.go exists)
- [ ] BDD coverage proves the operator-facing behavior (tests/e2e/workflow_progress_test.go exists)
- [ ] The epic is signed off and documented (docs/features/demo-4o4-acceptance.md exists)

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
DOD

git -C "$PROJECT" add DOD.md
git -C "$PROJECT" commit -q -m "Define DoD for demo-4o4 (Phase 4)"
git -C "$PROJECT" push -q

# Helpers for laying down stub files at each milestone. The point of
# the walkthrough is the DoD progression, not the contents of the
# implementation files; each stub is a one-line placeholder that lets
# `file_exists` resolve PASS.
stub() {
	local relpath=$1
	local body=${2:-"// placeholder for demo-4o4 walkthrough"}
	mkdir -p "$PROJECT/$(dirname "$relpath")"
	echo "$body" > "$PROJECT/$relpath"
}

# ──────────────────────────────────────────────────────────────────────
# T0 — plan committed; nothing implemented
# ──────────────────────────────────────────────────────────────────────
cat > "$PROJECT/PLAN.md" <<'PLAN'
# demo-4o4: Workflow Progress Indicators

## Phase 1: Persistence
- [ ] session_phases table + queries

## Phase 2: Detection + tracking
- [ ] Phase detection from hook events
- [ ] In-memory tracker with DB persistence

## Phase 3: UI
- [ ] Workflow progress stepper component
- [ ] Command center phase badge

## Phase 4: Coverage
- [ ] BDD tests for all 7 ACs

## Phase 5: Demo + acceptance
- [ ] Demo mode sample data
- [ ] Plate epic with acceptance doc
PLAN
git -C "$PROJECT" add PLAN.md
git -C "$PROJECT" commit -q -m "plan: Phase 4 — Workflow Progress Indicators"
git -C "$PROJECT" push -q

step_header "T0 — plan landed; nothing implemented (mirrors 8f2d28c)"
T0_JSON=$(run_dod T0.json --project line-cook-web --format json)
T0_TXT=$(run_dod T0.txt  --project line-cook-web)
echo "headline: $(headline "$T0_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T1 — session_phases.go (matches 17ce368: feat: add session_phases ...)
# ──────────────────────────────────────────────────────────────────────
stub internal/db/session_phases.go 'package db
// session_phases stores per-session workflow phase state.'
git -C "$PROJECT" add internal/db/session_phases.go
git -C "$PROJECT" commit -q -m "feat: add session_phases table and queries for workflow tracking"
git -C "$PROJECT" push -q

step_header "T1 — persistence layer landed (mirrors 17ce368)"
T1_JSON=$(run_dod T1.json --project line-cook-web --format json)
T1_TXT=$(run_dod T1.txt  --project line-cook-web)
echo "headline: $(headline "$T1_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T2 — workflow_progress.templ (matches 41d87d0: feat: add workflow progress stepper component)
# ──────────────────────────────────────────────────────────────────────
stub internal/web/templates/workflow_progress.templ '// Templ component: PREP → COOK → SERVE → TIDY stepper'
git -C "$PROJECT" add internal/web/templates/workflow_progress.templ
git -C "$PROJECT" commit -q -m "feat: add workflow progress stepper component"
git -C "$PROJECT" push -q

step_header "T2 — UI component landed (mirrors 41d87d0)"
T2_JSON=$(run_dod T2.json --project line-cook-web --format json)
echo "headline: $(headline "$T2_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T3 — detect.go + tracker.go + command_center.templ + BDD tests
#      (matches 4c5fc17 + 6f08a02: BDD tests + feat: complete Phase 4)
# ──────────────────────────────────────────────────────────────────────
stub internal/workflow/detect.go 'package workflow
// DetectPhase derives a phase from hook event payloads.'
stub internal/workflow/tracker.go 'package workflow
// Tracker persists phase transitions and broadcasts SSE updates.'
stub internal/web/templates/command_center.templ '// Command center card with phase badge'
stub tests/e2e/workflow_progress_test.go 'package e2e
// TestFeature_WorkflowProgress: AC1..AC7 BDD coverage'

# Mark Phase 1-4 of PLAN.md complete (Phase 5 still open at this point).
cat > "$PROJECT/PLAN.md" <<'PLAN'
# demo-4o4: Workflow Progress Indicators

## Phase 1: Persistence (complete)
- [x] session_phases table + queries

## Phase 2: Detection + tracking (complete)
- [x] Phase detection from hook events
- [x] In-memory tracker with DB persistence

## Phase 3: UI (complete)
- [x] Workflow progress stepper component
- [x] Command center phase badge

## Phase 4: Coverage (complete)
- [x] BDD tests for all 7 ACs

## Phase 5: Demo + acceptance
- [ ] Demo mode sample data
- [ ] Plate epic with acceptance doc
PLAN

git -C "$PROJECT" add internal/workflow/ internal/web/templates/command_center.templ tests/ PLAN.md
git -C "$PROJECT" commit -q -m "feat: complete Phase 4: Workflow Progress Indicators (demo-4o4)"
git -C "$PROJECT" push -q

step_header "T3 — detection, tracker, BDD tests landed (mirrors 4c5fc17 + 6f08a02)"
T3_JSON=$(run_dod T3.json --project line-cook-web --format json)
T3_TXT=$(run_dod T3.txt  --project line-cook-web)
echo "headline: $(headline "$T3_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T4 — demo mode (matches de25494: demo-4o4.1.5: Add demo workflow progress data)
# ──────────────────────────────────────────────────────────────────────
stub internal/demo/workflow.go 'package demo
// Sample workflow progress data for ?demo=1.'
git -C "$PROJECT" add internal/demo/workflow.go
git -C "$PROJECT" commit -q -m "demo-4o4.1.5: Add demo workflow progress data"
git -C "$PROJECT" push -q

step_header "T4 — demo mode landed (mirrors de25494)"
T4_JSON=$(run_dod T4.json --project line-cook-web --format json)
echo "headline: $(headline "$T4_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T5 — acceptance doc + manual reviews ticked
#      (matches 47a6582: plate: Feature demo-4o4.1 acceptance documentation)
# ──────────────────────────────────────────────────────────────────────
mkdir -p "$PROJECT/docs/features"
cat > "$PROJECT/docs/features/demo-4o4-acceptance.md" <<'ACC'
# Epic Acceptance: Phase 4 — Workflow Progress Indicators

**Epic ID:** demo-4o4
**Status:** Accepted

All 7 ACs verified end-to-end. Maitre, sous-chef, and critic reviews
all signed off. See parent project acceptance pipeline for full
audit trail.
ACC

# Tick the four MANUAL items (kitchen staff sign-offs).
sed -i.bak \
	-e 's/^- \[ \] Maitre BDD review APPROVED/- [x] Maitre BDD review APPROVED/' \
	-e 's/^- \[ \] Sous-chef code quality review APPROVED/- [x] Sous-chef code quality review APPROVED/' \
	-e 's/^- \[ \] Critic E2E coverage review PASS/- [x] Critic E2E coverage review PASS/' \
	-e 's/^- \[ \] All 7 ACs verified end-to-end in browser/- [x] All 7 ACs verified end-to-end in browser/' \
	"$PROJECT/DOD.md"
rm -f "$PROJECT/DOD.md.bak"

# Close out PLAN.md Phase 5.
cat > "$PROJECT/PLAN.md" <<'PLAN'
# demo-4o4: Workflow Progress Indicators

## Phase 1: Persistence (complete)
- [x] session_phases table + queries

## Phase 2: Detection + tracking (complete)
- [x] Phase detection from hook events
- [x] In-memory tracker with DB persistence

## Phase 3: UI (complete)
- [x] Workflow progress stepper component
- [x] Command center phase badge

## Phase 4: Coverage (complete)
- [x] BDD tests for all 7 ACs

## Phase 5: Demo + acceptance (complete)
- [x] Demo mode sample data
- [x] Plate epic with acceptance doc
PLAN

git -C "$PROJECT" add docs/features/demo-4o4-acceptance.md DOD.md PLAN.md
git -C "$PROJECT" commit -q -m "plate: Feature demo-4o4.1 acceptance documentation"
git -C "$PROJECT" push -q

step_header "T5 — acceptance doc + sign-offs (mirrors 47a6582) — DoD DONE"
T5_JSON=$(run_dod T5.json --project line-cook-web --format json)
T5_TXT=$(run_dod T5.txt  --project line-cook-web)
T5_MD=$(run_dod  T5.md   --project line-cook-web --format markdown)
echo "headline: $(headline "$T5_JSON")"

# Verify the exit-code contract: T5 must exit 0.
$PCMD dod --root "$SANDBOX/code" --project line-cook-web $NO_LLM_FLAG --format json >/dev/null 2>&1
T5_RC=$?
if [[ $T5_RC -ne 0 ]]; then
	echo "  FAIL: T5 should exit 0, got $T5_RC" >&2
	exit 1
fi

# ──────────────────────────────────────────────────────────────────────
# Sibling project to demonstrate the fleet roll-up: a still-OPEN epic
# (api-rewrite, fictional, with its own concrete DoD).
# ──────────────────────────────────────────────────────────────────────
SIBLING=$SANDBOX/code/api-rewrite
mkdir -p "$SIBLING"
git_init "$SIBLING"
echo "# api-rewrite" > "$SIBLING/README.md"
git -C "$SIBLING" add README.md && git -C "$SIBLING" commit -q -m "scaffold"
cat > "$SIBLING/DOD.md" <<'DOD'
# Definition of Done — REST→GraphQL migration

- [ ] Schema introspection at api/schema.graphql exists
- [ ] Resolver layer at internal/graphql/resolvers.go exists
- [ ] All plan phases complete
- [ ] Working tree clean
- [ ] Customer signoff documented at docs/handoff/api-rewrite-signoff.md
DOD
git -C "$SIBLING" add DOD.md && git -C "$SIBLING" commit -q -m "DoD"
echo "WIP" > "$SIBLING/scratch.txt"  # leaves a dirty tree

# A third project with no DOD.md at all.
QUIET=$SANDBOX/code/quiet-tool
mkdir -p "$QUIET"
git_init "$QUIET"
echo "# quiet-tool" > "$QUIET/README.md"
git -C "$QUIET" add README.md && git -C "$QUIET" commit -q -m "scaffold"

step_header "fleet roll-up across the sandbox"
FLEET=$(run_dod fleet.txt)
cat "$FLEET"

# ──────────────────────────────────────────────────────────────────────
# Journey summary
# ──────────────────────────────────────────────────────────────────────
step_header "journey summary — demo-4o4 progress against its DoD"
printf '%-4s  %s\n' "step" "next action (current percent)"
printf '%-4s  %s\n' "----" "-----------------------------"
for label in T0 T1 T2 T3 T4 T5; do
	json=/tmp/dod-walkthrough-out.$label.json
	[[ -f $json ]] || continue
	printf '%-4s  %s\n' "$label" "$(headline "$json")"
done

echo
echo "captured outputs:"
ls /tmp/dod-walkthrough-out.* | sed 's/^/  /'
