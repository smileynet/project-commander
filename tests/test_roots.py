"""Tests for multi-root discovery and root-resolution policy."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from project_commander.cli import _DEFAULT_ROOT_NAMES, _default_roots
from project_commander.discovery import (
	discover_projects,
	discover_projects_in_roots,
)


def _mkproj(root: Path, name: str) -> Path:
	p = root / name
	p.mkdir(parents=True)
	return p


# ---------- discover_projects_in_roots ----------

def test_in_roots_walks_each(tmp_path: Path):
	a = tmp_path / "a"
	b = tmp_path / "b"
	a.mkdir()
	b.mkdir()
	_mkproj(a, "alpha")
	_mkproj(b, "beta")
	out = discover_projects_in_roots([a, b])
	assert sorted(p.name for p in out) == ["alpha", "beta"]


def test_in_roots_dedup_when_same_path(tmp_path: Path):
	# Same root listed twice (e.g. user passes --root twice or env+CLI overlap)
	a = tmp_path / "a"
	a.mkdir()
	_mkproj(a, "alpha")
	out = discover_projects_in_roots([a, a])
	assert [p.name for p in out] == ["alpha"]


def test_in_roots_missing_root_skipped(tmp_path: Path):
	a = tmp_path / "a"
	missing = tmp_path / "does-not-exist"
	a.mkdir()
	_mkproj(a, "alpha")
	out = discover_projects_in_roots([a, missing])
	assert [p.name for p in out] == ["alpha"]


def test_in_roots_empty_list(tmp_path: Path):
	assert discover_projects_in_roots([]) == []


def test_in_roots_first_wins_on_collision(tmp_path: Path):
	# Two roots, both happen to contain the same absolute path (via symlink).
	# The dedup keeps the first occurrence.
	a = tmp_path / "a"
	b = tmp_path / "b"
	a.mkdir()
	b.mkdir()
	_mkproj(a, "shared")
	# Symlink b/shared -> a/shared so resolve() yields the same absolute path
	(b / "shared").symlink_to(a / "shared")
	out = discover_projects_in_roots([a, b])
	assert len(out) == 1
	assert out[0] == (a / "shared").resolve()


# ---------- single-root discover_projects unchanged ----------

def test_single_root_skips_hidden(tmp_path: Path):
	r = tmp_path / "r"
	r.mkdir()
	_mkproj(r, "visible")
	_mkproj(r, ".hidden")
	out = discover_projects(r)
	assert [p.name for p in out] == ["visible"]


def test_single_root_returns_empty_when_missing(tmp_path: Path):
	assert discover_projects(tmp_path / "no-such") == []


# ---------- _default_roots policy ----------

def test_default_roots_env_var_overrides_autodetect(tmp_path: Path, monkeypatch):
	a = tmp_path / "a"
	b = tmp_path / "b"
	a.mkdir()
	b.mkdir()
	# Fake $HOME with a `code` dir that should be IGNORED when env var is set.
	(tmp_path / "code").mkdir()
	monkeypatch.setenv(
		"PROJECT_COMMANDER_ROOTS",
		os.pathsep.join([str(a), str(b)]),
	)
	roots = _default_roots(home=tmp_path)
	assert roots == [a.resolve(), b.resolve()]


def test_default_roots_env_var_skips_blanks(tmp_path: Path, monkeypatch):
	a = tmp_path / "a"
	a.mkdir()
	monkeypatch.setenv(
		"PROJECT_COMMANDER_ROOTS",
		os.pathsep.join(["", str(a), "  ", ""]),
	)
	roots = _default_roots(home=tmp_path)
	assert roots == [a.resolve()]


def test_default_roots_autodetect_finds_existing(tmp_path: Path, monkeypatch):
	monkeypatch.delenv("PROJECT_COMMANDER_ROOTS", raising=False)
	# Create two of the conventional names; expect both back, in declared order.
	(tmp_path / "code").mkdir()
	(tmp_path / "projects").mkdir()
	roots = _default_roots(home=tmp_path)
	# Order should follow _DEFAULT_ROOT_NAMES (code before projects)
	names = [r.name for r in roots]
	assert names == ["code", "projects"]


def test_default_roots_autodetect_returns_nothing_when_none_exist(tmp_path: Path, monkeypatch):
	monkeypatch.delenv("PROJECT_COMMANDER_ROOTS", raising=False)
	# tmp_path has no `code/projects/src/...` subdirs
	roots = _default_roots(home=tmp_path)
	assert roots == []


def test_default_roots_constants_are_distinct():
	# guard against accidental dup in the convention list
	assert len(_DEFAULT_ROOT_NAMES) == len(set(_DEFAULT_ROOT_NAMES))


@pytest.mark.parametrize("name", ["code", "projects", "src", "dev", "work", "repos", "git"])
def test_default_root_names_include_common_conventions(name):
	assert name in _DEFAULT_ROOT_NAMES
