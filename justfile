# project-commander recipes
# https://github.com/smileynet/project-commander
#
# Run `just` to list available targets.

# Python interpreter to use.  Honors $PYTHON, then a local .venv, then python3.
python := env_var_or_default("PYTHON", if path_exists(".venv/bin/python") == "true" { justfile_directory() / ".venv/bin/python" } else { "python3" })
pc := if path_exists(".venv/bin/project-commander") == "true" { justfile_directory() / ".venv/bin/project-commander" } else { "project-commander" }

# Default: show available recipes.
default:
	@just --list --unsorted

# ---------- setup ----------

# Install in editable mode with dev dependencies.
install:
	{{python}} -m pip install -e ".[dev]"

# ---------- run ----------

# Quick fleet view, last 7 days, no color (good for piping/snapshots).
smoke:
	{{pc}} --since 7 --no-color | head -30

# Full fleet view to stdout (table).
fleet:
	{{pc}}

# Fleet view in markdown to stdout.
fleet-md:
	{{pc}} --format markdown

# Detail view for one project.  Usage:  just detail NAME=foo
detail NAME:
	{{pc}} --project {{NAME}}

# Detail view for one project as markdown to stdout.  Usage:  just detail-md NAME=foo
detail-md NAME:
	{{pc}} --project {{NAME}} --format markdown

# JSON dump of every project (full signals included).
fleet-json:
	{{pc}} --format json

# ---------- report files ----------

# Write fleet + last-7-days reports to reports/ (gitignored).
report:
	@mkdir -p reports
	{{pc}} --format markdown > reports/all-projects.md
	{{pc}} --since 7 --format markdown > reports/last-7-days.md
	@echo "wrote reports/all-projects.md  reports/last-7-days.md"

# Write a project detail report.  Usage:  just report-detail NAME=foo
report-detail NAME:
	@mkdir -p reports
	{{pc}} --project {{NAME}} --format markdown > reports/{{NAME}}.md
	@echo "wrote reports/{{NAME}}.md"

# Regenerate the full report set used in docs/screenshots.
publish:
	@mkdir -p reports
	{{pc}} --format markdown > reports/all-projects.md
	{{pc}} --since 7 --format markdown > reports/last-7-days.md
	@echo "wrote reports/"

# ---------- triage ----------

# Triage the fleet — runs the pc-triage skill script and prints an action list grouped by urgency.
triage:
	{{python}} skills/pc-triage/scripts/triage.py

# Same, but limited to recent activity.  Usage:  just triage-week
triage-week:
	{{python}} skills/pc-triage/scripts/triage.py --since 7

# ---------- quality gates ----------

# Run the test suite.
test:
	{{python}} -m pytest -q

# Run tests with verbose output and -x (stop on first failure).
test-v:
	{{python}} -m pytest -v -x

# ---------- housekeeping ----------

# Remove caches and build artifacts (keeps reports/ and .venv).
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "cleaned"
