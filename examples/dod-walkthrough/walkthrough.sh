#!/usr/bin/env bash
# Reproducible end-to-end demonstration of the `dod` subcommand.
#
# Builds three sandbox projects under a temp directory:
#   wordcount/     — walked from "mid-flight" to "shipped" across six
#                    transitions (T0 → T5)
#   api-rewrite/   — left intentionally OPEN to demonstrate the fleet
#                    roll-up
#   quiet-tool/    — has no DOD.md, demonstrates the "no DoD" baseline
#
# Each transition runs the dod subcommand and prints the headline
# (status, percent, next action). The full output is written to
# /tmp/dod-walkthrough-out.* for inspection.
#
# Usage:
#   ./walkthrough.sh              run, then delete the sandbox
#   ./walkthrough.sh --keep       run, leave the sandbox at $SANDBOX
#   PCMD=project-commander \
#     ./walkthrough.sh            override the binary used to invoke dod
#                                 (default: `python -m project_commander`)

set -euo pipefail

PCMD=${PCMD:-python -m project_commander}
SANDBOX=${SANDBOX:-$(mktemp -d -t dod-walkthrough.XXXXXX)}
KEEP=0
if [[ "${1:-}" == "--keep" ]]; then
	KEEP=1
fi

# Quiet git config inside the sandbox so the demo's commits don't depend
# on the user's global git identity.
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
	$PCMD dod --root "$SANDBOX/code" --no-llm "$@" >"$out" 2>&1 || true
	echo "$out"
}

headline() {
	local jsonfile=$1
	# Extract percent + next_action from a single-project JSON blob using
	# only POSIX tools so the script has no python/jq dependency to read
	# its own output.
	awk -F'"' '
		/"percent"/    { gsub(/[^0-9]/, "", $0); pct=$0 }
		/"is_complete"/{ comp=$4 }
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
mkdir -p "$SANDBOX/origin.git" "$SANDBOX/code/wordcount"
git -C "$SANDBOX/origin.git" init --bare -q -b main

WORDCOUNT=$SANDBOX/code/wordcount
git_init "$WORDCOUNT"
git -C "$WORDCOUNT" remote add origin "$SANDBOX/origin.git"
echo "# wordcount" > "$WORDCOUNT/README.md"
git -C "$WORDCOUNT" add README.md
git -C "$WORDCOUNT" commit -q -m "initial scaffold"
git -C "$WORDCOUNT" push -q -u origin main

cat > "$WORDCOUNT/DOD.md" <<'DOD'
# Definition of Done

Closure criteria for the `wordcount` feature. The agent should drive
this list to all-green before declaring shipping done.

## Mechanical
- [ ] Working tree clean
- [ ] Pushed to origin
- [ ] On main branch
- [ ] All plan phases complete
- [ ] Verify passes

## Conduct
- [ ] No orphan threads
- [ ] No plan-drift
- [ ] Substantive prompts

## Manual
- [ ] User has reviewed the README
- [ ] Smoke test runs end-to-end
DOD
git -C "$WORDCOUNT" add DOD.md
git -C "$WORDCOUNT" commit -q -m "Define DoD for wordcount feature"
git -C "$WORDCOUNT" push -q

# ──────────────────────────────────────────────────────────────────────
# T0 — baseline (DOD.md committed; no implementation work yet)
# ──────────────────────────────────────────────────────────────────────
step_header "T0 — baseline (clean repo, DoD just defined)"
T0_JSON=$(run_dod T0.json --project wordcount --format json)
T0_TXT=$(run_dod T0.txt --project wordcount)
echo "headline: $(headline "$T0_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T1 — mid-flight (dirty tree, plan written but open, ahead of origin)
# ──────────────────────────────────────────────────────────────────────
cat > "$WORDCOUNT/PLAN.md" <<'PLAN'
# Wordcount feature plan

## Phase 1: CLI scaffold
- [x] Parse argv
- [x] Read stdin
- [ ] Wire output formatter

## Phase 2: Counting
- [ ] Tokenize on whitespace
- [ ] Emit count with newline

## Phase 3: Polish
- [ ] Document usage in README
PLAN

cat > "$WORDCOUNT/wordcount.py" <<'PY'
"""wordcount: count words in stdin."""
import sys

def count(text: str) -> int:
	return len(text.split())

if __name__ == "__main__":
	print(count(sys.stdin.read()))
PY

git -C "$WORDCOUNT" add PLAN.md
git -C "$WORDCOUNT" commit -q -m "Sketch implementation plan"
git -C "$WORDCOUNT" add wordcount.py  # staged but not committed → dirty tree

step_header "T1 — mid-flight"
T1_JSON=$(run_dod T1.json --project wordcount --format json)
T1_TXT=$(run_dod T1.txt --project wordcount)
echo "headline: $(headline "$T1_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T2 — agent commits the staged file
# ──────────────────────────────────────────────────────────────────────
git -C "$WORDCOUNT" commit -q -m "Add wordcount.py CLI"

step_header "T2 — committed wordcount.py"
T2_JSON=$(run_dod T2.json --project wordcount --format json)
echo "headline: $(headline "$T2_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T3 — plan checkboxes ticked + README usage section
# ──────────────────────────────────────────────────────────────────────
cat > "$WORDCOUNT/PLAN.md" <<'PLAN'
# Wordcount feature plan

## Phase 1: CLI scaffold (complete)
- [x] Parse argv
- [x] Read stdin
- [x] Wire output formatter

## Phase 2: Counting (complete)
- [x] Tokenize on whitespace
- [x] Emit count with newline

## Phase 3: Polish (complete)
- [x] Document usage in README
PLAN
{
	echo
	echo "## Usage"
	echo
	echo "    echo 'one two three' | python wordcount.py"
} >> "$WORDCOUNT/README.md"
git -C "$WORDCOUNT" add PLAN.md README.md
git -C "$WORDCOUNT" commit -q -m "Mark plan complete; document usage"

step_header "T3 — plan complete"
T3_JSON=$(run_dod T3.json --project wordcount --format json)
echo "headline: $(headline "$T3_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T4 — push to origin
# ──────────────────────────────────────────────────────────────────────
git -C "$WORDCOUNT" push -q

step_header "T4 — pushed"
T4_JSON=$(run_dod T4.json --project wordcount --format json)
echo "headline: $(headline "$T4_JSON")"

# ──────────────────────────────────────────────────────────────────────
# T5 — user ticks the two MANUAL items by hand
# ──────────────────────────────────────────────────────────────────────
sed -i.bak \
	-e 's/^- \[ \] User has reviewed the README/- [x] User has reviewed the README/' \
	-e 's/^- \[ \] Smoke test runs end-to-end/- [x] Smoke test runs end-to-end/' \
	"$WORDCOUNT/DOD.md"
rm -f "$WORDCOUNT/DOD.md.bak"
git -C "$WORDCOUNT" add DOD.md
git -C "$WORDCOUNT" commit -q -m "Confirm DoD manual items; ship"
git -C "$WORDCOUNT" push -q

step_header "T5 — DONE"
T5_JSON=$(run_dod T5.json --project wordcount --format json)
T5_TXT=$(run_dod T5.txt --project wordcount)
T5_MD=$(run_dod T5.md   --project wordcount --format markdown)
echo "headline: $(headline "$T5_JSON")"

# Verify the exit code contract:
#   on T5, dod must exit 0 (definition met)
#   on any earlier T*, dod must exit non-zero
$PCMD dod --root "$SANDBOX/code" --project wordcount --no-llm --format json >/dev/null 2>&1
T5_RC=$?
if [[ $T5_RC -ne 0 ]]; then
	echo "  FAIL: T5 should exit 0, got $T5_RC" >&2
	exit 1
fi

# ──────────────────────────────────────────────────────────────────────
# Sibling projects to demonstrate the fleet roll-up
# ──────────────────────────────────────────────────────────────────────
APIREWRITE=$SANDBOX/code/api-rewrite
mkdir -p "$APIREWRITE"
git_init "$APIREWRITE"
echo "# api-rewrite" > "$APIREWRITE/README.md"
git -C "$APIREWRITE" add README.md
git -C "$APIREWRITE" commit -q -m "scaffold"
cat > "$APIREWRITE/DOD.md" <<'DOD'
# Definition of Done
- [ ] Working tree clean
- [ ] All plan phases complete
- [ ] Verify passes
- [ ] Customer signoff received
DOD
git -C "$APIREWRITE" add DOD.md
git -C "$APIREWRITE" commit -q -m "DoD"
echo "WIP" > "$APIREWRITE/scratch.txt"  # leaves a dirty tree

QUIET=$SANDBOX/code/quiet-tool
mkdir -p "$QUIET"
git_init "$QUIET"
echo "# quiet-tool" > "$QUIET/README.md"
git -C "$QUIET" add README.md
git -C "$QUIET" commit -q -m "scaffold"
# no DOD.md → quiet-tool sits in the "no DoD defined" bucket

step_header "fleet roll-up across the sandbox"
FLEET=$(run_dod fleet.txt)
cat "$FLEET"

# ──────────────────────────────────────────────────────────────────────
# Journey summary
# ──────────────────────────────────────────────────────────────────────
step_header "journey summary"
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
