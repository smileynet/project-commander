---
name: pc-triage
description: Triage report for ~/code projects — surfaces what needs attention, grouped by urgency.
---

# pc-triage

Use this skill when the user asks for a triage report, asks "what
projects need my attention", asks for a Monday-morning kickoff, or
otherwise wants a prioritized list of which `~/code` projects to
deal with.

It runs `project-commander` with JSON output, applies a fixed set of
heuristics, and returns a markdown action list grouped by urgency.

## When to use

Trigger on requests like:

- "Run pc-triage"
- "What projects need my attention?"
- "Give me the Monday morning kickoff"
- "Triage my fleet"
- "What's flagged across `~/code`?"

Do **not** trigger on requests for a general fleet view — for that,
just run `project-commander` directly. This skill is specifically for
*deciding what to act on next*.

## How

Run the bundled script:

```sh
python skills/pc-triage/scripts/triage.py [--since N] [--root /path]
```

Or, if `just` is available in the repo:

```sh
just triage          # full triage
just triage-week     # restrict to last 7 days
```

Optional arguments:

- `--since N` — restrict to projects active in the last N days
- `--root /path` — scan somewhere other than `~/code`

Both pass through to `project-commander`. The script assumes
`project-commander` is on `$PATH`.

## Output

Markdown with up to four sections, each emitted only if it has
items:

| Section | Triggers |
|---|---|
| **Urgent** | `prompt-injection-detected` flag |
| **This week** | `plan-drift` flag; or `dirty-tree` flag plus last_active > 7 days |
| **When you have time** | `Dormant` progress; `Stub` progress |
| **For your records** | `tool-cluster` flag; `Drifting` progress; `Hot` progress (informational) |

If nothing trips any rule, the output is `Nothing to triage. Clean
fleet.`.

## Returning to the user

The user wants the action list, not your interpretation of it.

1. Summarize counts in one sentence: *"3 urgent, 5 to do this week,
   2 to revisit when you have time."*
2. Quote the script's markdown output verbatim.
3. Optionally offer to drill into one item with `--project NAME`.

Do not paraphrase, re-rank, or invent recommendations beyond what
the script emitted. The whole point of the skill is that the
heuristics are auditable and stable.
