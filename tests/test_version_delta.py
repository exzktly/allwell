"""`scripts/preflight.sh` fails a branch whose version moved by anything but one increment.

CLAUDE.md §6: every pull request bumps the version, exactly once. The check
used to enforce only the upper half — more than one increment failed, none at
all printed "unchanged" and passed — and two versions on ``main`` each shipped
two pull requests' contents as a result. The verdict lives in
``scripts/version_delta.py``; the last test here runs the real preflight
script (``--quick``, with ``ruff`` stubbed) in a throwaway repository, because
a correct verdict that the shell script then prints and ignores is exactly the
defect being fixed.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("version_delta", _REPO / "scripts" / "version_delta.py")
version_delta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_delta)


@pytest.mark.parametrize("base, head, verdict", [
    ("9.13.12", "9.13.13", "ONE"),
    ("9.13.12", "9.14.0", "ONE"),
    ("9.13.12", "10.0.0", "ONE"),
    ("9.13.12", "9.13.12", "SAME"),
    ("9.13.12", "9.13.14", "MANY"),
    ("9.13.12", "9.14.1", "MANY"),
    ("9.13.12", "9.13.11", "MANY"),
    ("9.13.12", "9.13", "UNREADABLE"),
    ("9.13.12", "9.13.x", "UNREADABLE"),
])
def test_classify(base, head, verdict):
    assert version_delta.classify(base, head) == verdict


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _preflight(tmp_path: Path, head_version: str) -> subprocess.CompletedProcess:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("preflight.sh", "version_delta.py"):
        shutil.copy2(_REPO / "scripts" / name, repo / "scripts" / name)
    (repo / "version.py").write_text('__version__ = "9.13.12"\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    if head_version != "9.13.12":
        (repo / "version.py").write_text(f'__version__ = "{head_version}"\n')
        _git(repo, "commit", "-qam", "bump")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "ruff").write_text("#!/bin/sh\nexit 0\n")
    (stub / "ruff").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(["bash", "scripts/preflight.sh", "--quick"], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=60)


def test_preflight_fails_a_branch_with_no_bump(tmp_path):
    run = _preflight(tmp_path, "9.13.12")
    assert run.returncode != 0, (
        "preflight passed a branch whose version.py is unchanged from origin/main:\n" + run.stdout + run.stderr)
    assert "unchanged" in run.stderr + run.stdout


def test_preflight_passes_one_bump(tmp_path):
    run = _preflight(tmp_path, "9.13.13")
    assert run.returncode == 0, run.stdout + run.stderr


# ── the CI half: a sibling that took the same number first (tracker P1) ──────

def _ci_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/version_bump_ci.sh"], cwd=repo, capture_output=True, text=True,
                          timeout=60)


def test_ci_fails_the_second_of_two_pull_requests_that_bumped_to_the_same_number(tmp_path):
    """Seven pull requests in three days merged with no increment of their own: each bumped 9.x.y to the same next
    number as a sibling open beside it, each passed the author's preflight against a stale origin/main, and the
    second merged cleanly. Measured on the merge itself, the first passes and the second does not."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("version_bump_ci.sh", "version_delta.py"):
        shutil.copy2(_REPO / "scripts" / name, repo / "scripts" / name)
    (repo / "version.py").write_text('__version__ = "9.13.12"\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for branch, other in (("a", "a.txt"), ("b", "b.txt")):
        _git(repo, "checkout", "-q", "-b", branch, "main")
        (repo / other).write_text(branch)
        (repo / "version.py").write_text('__version__ = "9.13.13"\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"work on {branch}")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "a", "-m", "merge a")
    first = _ci_check(repo)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "9.13.12 -> 9.13.13, one increment" in first.stdout
    _git(repo, "merge", "-q", "--no-ff", "b", "-m", "merge b")
    second = _ci_check(repo)
    assert second.returncode == 1, second.stdout + second.stderr
    assert "no increment of its own" in second.stdout


def test_ci_runs_the_check_on_every_pull_request_and_every_push_to_main():
    import yaml

    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "ci.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))                # YAML 1.1 reads a bare `on` as True
    assert "pull_request" in triggers and triggers["push"]["branches"] == ["main"]
    job = workflow["jobs"]["version"]
    assert "if" not in job                                           # never skipped for a path filter
    assert any(step.get("with", {}).get("fetch-depth") == 2 for step in job["steps"])
    assert [s["run"] for s in job["steps"] if "run" in s] == ["scripts/version_bump_ci.sh"]
