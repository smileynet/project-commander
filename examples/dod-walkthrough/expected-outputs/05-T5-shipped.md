## line-cook-web — Definition of Done

**Status:** DONE · 16/16 (100%) · source: `DOD.md`
**Target:** When this epic is done, an operator monitoring a Line Cook session sees the active workflow phase (PREP → COOK → SERVE → TIDY) at a glance on the dashboard, with live updates as phases transition. The command center shows the current phase per project. Demo mode supports exploration without a real session, and dashboard restart preserves phase state.


| Status | Criterion | Detail |
|---|---|---|
| `PASS` | Operators see workflow phase progression on the dashboard (internal/web/templates/workflow_progress.templ exists) | found at internal/web/templates/workflow_progress.templ |
| `PASS` | The dashboard knows when phases transition (internal/workflow/detect.go exists) | found at internal/workflow/detect.go |
| `PASS` | Phase transitions broadcast live to the dashboard (internal/workflow/tracker.go exists) | found at internal/workflow/tracker.go |
| `PASS` | The command center shows current phase per project (internal/web/templates/command_center.templ exists) | found at internal/web/templates/command_center.templ |
| `PASS` | Phase state survives a dashboard restart (internal/db/session_phases.go exists) | found at internal/db/session_phases.go |
| `PASS` | Operators can explore the dashboard without a live session (internal/demo/workflow.go exists) | found at internal/demo/workflow.go |
| `PASS` | BDD coverage proves the operator-facing behavior (tests/e2e/workflow_progress_test.go exists) | found at tests/e2e/workflow_progress_test.go |
| `PASS` | The epic is signed off and documented (docs/features/demo-4o4-acceptance.md exists) | found at docs/features/demo-4o4-acceptance.md |
| `PASS` | Working tree clean |  |
| `PASS` | Pushed to origin | at parity with origin/main |
| `PASS` | All plan phases complete | all checkboxes ticked, all phases complete |
| `PASS` | Verify passes | 5 of 5 checks passed (rest skipped) |
| `DONE` | Maitre BDD review APPROVED | user-confirmed |
| `DONE` | Sous-chef code quality review APPROVED | user-confirmed |
| `DONE` | Critic E2E coverage review PASS | user-confirmed |
| `DONE` | All 7 ACs verified end-to-end in browser | user-confirmed |
