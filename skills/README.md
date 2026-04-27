# Skills

Skill bundles for use with OMP- or cavekit-style agent harnesses.
Each skill is a self-contained directory the harness can load and
the agent can run without further context.

## Installed skills

### pc-triage
Triage report for your projects — runs `project-commander`,
applies a fixed set of heuristics, and returns an action list
grouped by urgency (urgent / this week / when-you-have-time /
for-your-records). See [`pc-triage/SKILL.md`](pc-triage/SKILL.md).

The script under `pc-triage/scripts/triage.py` runs standalone too —
no agent harness required:

```sh
python skills/pc-triage/scripts/triage.py
python skills/pc-triage/scripts/triage.py --since 7
```

`just triage` and `just triage-week` are aliases.

## Now in core

Two skills sketched in earlier drafts have since been implemented as
subcommands and no longer need separate skill bundles:

- **`pc-resume`** → `project-commander report --project NAME` opens the
  4-question briefing card (with optional LLM-synthesized prose for the
  identity / activity / plan sections). Run that directly when you want a
  session-resumption brief.
- **`pc-weekly`** → `project-commander report --since 7` opens with a
  cross-project narrative recap (LLM-synthesized when a provider is
  available) followed by the deterministic triage table. Use
  `--format markdown` for a shareable journal entry.

## Candidate skills (sketched, not yet implemented)

These would be useful additions on top of the core; designs are noted
so anyone (or some future self) can pick them up:

### pc-handoff
Given a project name, produce a handoff document for a teammate (or
future self): purpose, current state, recent work, and in-flight
todos extracted from the most recent agent prompts. Largely subsumed
by the briefing card already, but a focused "prepare this for someone
else" framing would still be useful.

## Installing into your harness

Skill bundles are self-contained directories.

**OMP** — copy or symlink under `~/.omp/agent/skills/`:

```sh
ln -s "$(pwd)/skills/pc-triage" ~/.omp/agent/skills/pc-triage
```

**cavekit / other harnesses** — follow the harness's own skill-install
convention; the layout (`SKILL.md` + `scripts/`) is intended to be
portable.

The scripts assume `project-commander` is on your `PATH`. If you
installed the package with `pip install -e .` from this repo, that's
already the case.
