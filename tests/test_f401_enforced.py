"""Audit G5 — unused-import (F401) is enforced and the tree is clean.

F401 was previously in ruff's ignore list, letting dead imports accumulate.
The 56 genuinely-unused imports were dropped and F401 re-enabled; intentional
side-effect / re-export imports carry an explicit ``# noqa: F401``. This test
guards against both re-ignoring the rule and re-introducing unused imports.
"""
from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_f401_not_in_ignore_list():
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    ignore = cfg["tool"]["ruff"]["lint"]["ignore"]
    assert "F401" not in ignore, "F401 must stay enforced (audit G5)"


def test_tree_has_no_unused_imports():
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not installed in this environment")
    proc = subprocess.run(
        [ruff, "check", "--select", "F401", "--no-cache", "."],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "ruff reported F401 (unused-import) violations:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
