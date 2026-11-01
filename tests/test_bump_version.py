"""The version is bumped on ``main`` after each merge, never inside a pull request.

``scripts/bump_version.py`` decides the increment and rewrites ``version.py``;
``.github/workflows/version-bump.yml`` runs it once per push to ``main``. Pull
requests used to carry their own bump, so any two open at once edited the same
line and the second to merge either conflicted or landed with no increment.
These tests pin the script's arithmetic, the workflow's shape, the workflow's
"this push already set the version" step run for real in a scratch repository,
and the absence of the per-pull-request checks that the workflow replaced.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("bump_version", _REPO / "scripts" / "bump_version.py")
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)

_WORKFLOW = _REPO / ".github" / "workflows" / "version-bump.yml"


@pytest.mark.parametrize("labels, part", [
    ([], "patch"),
    (["docs", "bug"], "patch"),
    (["minor"], "minor"),
    (["Minor"], "minor"),
    (["major"], "major"),
    (["minor", "major"], "major"),
    (["minor-ish"], "patch"),
])
def test_part_for_labels(labels, part):
    assert bump_version.part_for_labels(labels) == part


@pytest.mark.parametrize("part, expected", [
    ("patch", (9, 21, 2)),
    ("minor", (9, 22, 0)),
    ("major", (10, 0, 0)),
])
def test_bumped_resets_the_lower_parts(part, expected):
    assert bump_version.bumped((9, 21, 1), part) == expected


def test_rewrite_changes_only_the_version_line():
    text = '"""Doc mentioning __version__ = "1.2.3" inline."""\n\n__version__ = "9.21.9"\n'
    new_text, new = bump_version.rewrite(text, "patch")
    assert new == "9.21.10"
    assert new_text == '"""Doc mentioning __version__ = "1.2.3" inline."""\n\n__version__ = "9.21.10"\n'


@pytest.mark.parametrize("text", [
    "nothing here\n",
    '__version__ = "9.21"\n',
    '__version__ = "9.21.1"\n__version__ = "9.21.2"\n',
])
def test_rewrite_refuses_a_file_it_cannot_read_unambiguously(text):
    with pytest.raises(ValueError):
        bump_version.rewrite(text, "patch")


def test_the_real_version_file_is_bumpable():
    _, new = bump_version.rewrite((_REPO / "version.py").read_text(encoding="utf-8"), "patch")
    assert new.count(".") == 2


def test_cli_rewrites_the_file_and_prints_the_new_version(tmp_path, capsys):
    target = tmp_path / "version.py"
    target.write_text('"""x"""\n\n__version__ = "9.21.1"\n', encoding="utf-8")
    assert bump_version.main(["--labels", "docs,minor", "--file", str(target)]) == 0
    assert capsys.readouterr().out == "9.22.0\n"
    assert target.read_text(encoding="utf-8") == '"""x"""\n\n__version__ = "9.22.0"\n'


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    target = tmp_path / "version.py"
    target.write_text('__version__ = "9.21.1"\n', encoding="utf-8")
    assert bump_version.main(["--file", str(target), "--dry-run"]) == 0
    assert capsys.readouterr().out == "9.21.2\n"
    assert target.read_text(encoding="utf-8") == '__version__ = "9.21.1"\n'


# ── the workflow ─────────────────────────────────────────────────────────────

def _workflow() -> dict:
    import yaml
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(workflow: dict, step_id: str) -> dict:
    steps = workflow["jobs"]["bump"]["steps"]
    return next(s for s in steps if s.get("id") == step_id)


def test_the_workflow_bumps_every_push_to_main_one_at_a_time():
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))                # YAML 1.1 reads a bare `on` as True
    assert triggers == {"push": {"branches": ["main"]}}
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["concurrency"] == {"group": "version-bump", "cancel-in-progress": False}
    bump = workflow["jobs"]["bump"]["steps"][-1]
    assert 'python scripts/bump_version.py --labels "$LABELS"' in bump["run"]
    assert "git push origin HEAD:main" in bump["run"]
    assert bump["if"] == "steps.manual.outputs.skip != 'true'"
    # Labels reach the shell through env, never interpolated into the script (a label is user-controlled text).
    assert "${{" not in bump["run"] and "${{" not in _step(workflow, "pr")["run"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


@pytest.mark.parametrize("push_touches_version, skip", [(False, "false"), (True, "true")])
def test_a_push_that_set_the_version_itself_is_not_bumped_again(tmp_path, push_touches_version, skip):
    """The workflow's own ``manual`` step, run in a scratch repository: a merge whose diff moved version.py (a
    deliberate hand-set version) is skipped, and an ordinary merge is not."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "version.py").write_text('__version__ = "9.21.1"\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "work.txt").write_text("work")
    if push_touches_version:
        (repo / "version.py").write_text('__version__ = "10.0.0"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "merge")
    after = _git(repo, "rev-parse", "HEAD")

    output = tmp_path / "github_output"
    output.write_text("")
    run = subprocess.run(["bash", "-e", "-c", _step(_workflow(), "manual")["run"]], cwd=repo, capture_output=True,
                         text=True, timeout=60,
                         env={**os.environ, "BEFORE": before, "AFTER": after, "GITHUB_OUTPUT": str(output)})
    assert run.returncode == 0, run.stdout + run.stderr
    assert output.read_text() == f"skip={skip}\n"


# ── the per-pull-request checks it replaced stay gone ────────────────────────

def test_pull_requests_are_not_asked_to_bump():
    import yaml

    ci = yaml.safe_load((_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert "version" not in ci["jobs"]
    preflight = (_REPO / "scripts" / "preflight.sh").read_text(encoding="utf-8")
    assert 'step "version bump"' not in preflight
    assert not (_REPO / "scripts" / "version_bump_ci.sh").exists()
    assert not (_REPO / "scripts" / "version_delta.py").exists()
