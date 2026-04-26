# Skills

Skill bundles for use with OMP- or cavekit-style agent harnesses.
Each skill is a self-contained directory the harness can load and
the agent can run without further context.

## Installed skills

### pc-triage
Triage report for `~/code` projects — runs `project-commander`,
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

## Candidate skills (sketched, not yet implemented)

These would be useful additions; designs are noted so anyone (or
some future self) can pick them up:

### pc-resume
Given a project name, produce a session-resumption brief: what the
project is for, where you left off, what's flagged, and a proposed
next action. Replaces the manual ritual of opening the detail view +
reading three plan docs + grepping git log before you can write a
line of code.

### pc-weekly
Weekly review: runs `--since 7 --format markdown`, prompts the user
for a one-line reflection per project that moved, outputs a journal
entry suitable for end-of-week wrap-ups or sharing.

### pc-handoff
Given a project name, produce a handoff document for a teammate (or
future self): purpose, current state, recent work, and in-flight
todos extracted from the most recent agent prompts.

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
