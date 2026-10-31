"""The change-scope classifier, and the fast lane it narrows.

``scripts/change_scope.py`` decides whether a change is version-only (no tests,
no lint), docs-only (the docs lane only) or code (everything); ``scripts/test.sh``,
``scripts/preflight.sh`` and CI act on the answer. A wrong answer in the narrow
direction skips tests a change could break, and nothing goes red to say so,
which is why the cases below pin exact answers rather than properties, and the
last two drive the real ``scripts/test.sh`` through the sequence a developer
runs: change a file, then run the lane.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import change_scope as cs  # noqa: E402


@pytest.mark.parametrize(("paths", "expected"), [
    (["version.py"], cs.VERSION_ONLY),
    (["_Docs/Markdowns/ARCHITECTURE.md"], cs.DOCS_ONLY),
    (["version.py", "_Docs/Markdowns/README_WellViewer.md", "CLAUDE.md"], cs.DOCS_ONLY),
    (["tests/FAST_LANE.md"], cs.DOCS_ONLY),
    # Code beside docs is code, whichever comes first.
    (["_Docs/Markdowns/ARCHITECTURE.md", "ui/help_topics.py"], cs.CODE),
    (["version.py", "scripts/test.sh"], cs.CODE),
    # Under _Docs but not prose the app or a build ignores: the spec and build scripts, design notes.
    (["_Docs/_Installation/all_well.spec"], cs.CODE),
    (["_Docs/design/notes.md"], cs.CODE),
    (["_Docs/Markdowns/sub/ARCHITECTURE.md"], cs.CODE),
    (["_Docs/Markdowns/ARCHITECTURE.md.orig"], cs.CODE),
    (["docs/CLAUDE.md"], cs.CODE),
    # Nothing to decide from: run everything.
    ([], cs.CODE),
    (None, cs.CODE),
])
def test_each_change_gets_its_scope(paths, expected):
    assert cs.classify(paths) == expected


def test_a_version_file_that_changes_more_than_the_number_is_code():
    assert cs.classify(["version.py"], version_lines_only=False) == cs.CODE
    assert cs.classify(["version.py", "CLAUDE.md"], version_lines_only=False) == cs.CODE


@pytest.mark.parametrize(("lines", "expected"), [
    (['__version__ = "9.20.14"', '__version__ = "9.20.15"'], True),
    (['__version__ = "9.20.14"', '__version__ = "9.20.15"', "import os"], False),
    (['__version__ = "9.20.15"  # bumped'], False),
    (["", '__version__ = "9.20.15"'], False),
    ([], True),
    (None, False),
])
def test_only_the_assignment_line_counts_as_a_version_change(lines, expected):
    assert cs.only_version_assignment(lines) is expected


# ── the docs lane ───────────────────────────────────────────────────────────


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_docs_lane_holds_readers_and_only_readers(tmp_path):
    _write(tmp_path, "_Docs/Markdowns/README_WellViewer.md", "# Manual\n")
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/manual.py", 'MANUAL = ("_Docs", "Markdowns", "README_WellViewer.md")\n')
    _write(tmp_path, "pkg/other.py", '"""See README_WellViewer.md for the prose."""\nX = 1\n')
    _write(tmp_path, "tests/test_reads.py", 'P = "README_WellViewer.md"\n')
    _write(tmp_path, "tests/test_via_import.py", "from pkg import manual\n")
    _write(tmp_path, "tests/test_prose_only.py", '"""Cites README_WellViewer.md in prose."""\n')
    _write(tmp_path, "tests/test_imports_prose.py", "import pkg.other\n")
    _write(tmp_path, "tests/test_plain.py", "def test_x():\n    assert True\n")
    assert cs.docs_lane(tmp_path) == ["tests/test_reads.py", "tests/test_via_import.py"]


def test_the_real_docs_lane_finds_the_known_readers():
    lane = {Path(p).name for p in cs.docs_lane()}
    # Direct readers of the docs, CLAUDE.md, and the manual through ui/help_topics.py.
    for reader in ("test_doc_version_sync.py", "test_manual_render.py", "test_one_word_per_concept.py",
                   "test_focus_claims_stay_measured.py", "test_profile_build.py", "test_help_topics.py",
                   "test_guards_bite.py"):
        assert reader in lane, reader
    # Cites ARCHITECTURE.md only in its docstring, and reads no document.
    assert "test_hot_paths_stay_linear.py" not in lane
    assert "test_version_delta.py" not in lane


def test_every_docs_only_pattern_matches_a_tracked_file():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    for pattern in cs.DOCS_ONLY_PATTERNS:
        assert any(re.search(pattern, p) for p in tracked), f"{pattern} matches no file"


# ── the real sequence: change a file, then ask / run the lane ─────────────────


def _git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    """A git repository holding the scope machinery, a manual, one test that reads it and one that fails."""
    root = tmp_path / "repo"
    for rel in ("scripts/test.sh", "scripts/change_scope.py", "scripts/lane_needed.py", "tests/_lanes.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    _write(root, ".gitignore", "__pycache__/\n")
    _write(root, "version.py", '__version__ = "1.0.0"\n')
    _write(root, "_Docs/Markdowns/README_WellViewer.md", "# Manual\n")
    _write(root, "CLAUDE.md", "notes\n")
    _write(root, "tests/FAST_LANE.md", "notes\n")
    _write(root, "app.py", "X = 1\n")
    _write(root, "tests/test_reads_manual.py",
           "from pathlib import Path\n\ndef test_manual():\n"
           "    assert Path('_Docs/Markdowns/README_WellViewer.md').read_text().startswith('# Manual')\n")
    _write(root, "tests/test_unrelated.py", "def test_fails():\n    assert False, 'outside the docs lane'\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "work")
    return root


@pytest.mark.parametrize(("edit", "expected"), [
    ({"version.py": '__version__ = "1.0.1"\n'}, cs.VERSION_ONLY),
    ({"version.py": '__version__ = "1.0.1"\n', "_Docs/Markdowns/README_WellViewer.md": "# Manual\nmore\n"},
     cs.DOCS_ONLY),
    ({"version.py": '__version__ = "1.0.1"\nimport os\n'}, cs.CODE),
    ({"_Docs/Markdowns/README_WellViewer.md": "# Manual\n", "app.py": "X = 2\n"}, cs.CODE),
])
def test_a_committed_change_is_classified_against_its_base(tmp_path, edit, expected):
    root = _repo(tmp_path)
    for rel, text in edit.items():
        _write(root, rel, text)
    _git(root, "commit", "-qam", "change")
    assert cs.scope("main", root)[0] == expected


def test_an_uncommitted_change_counts_and_an_unknown_base_runs_everything(tmp_path):
    root = _repo(tmp_path)
    _write(root, "app.py", "X = 3\n")
    assert cs.scope("main", root)[0] == cs.CODE
    assert cs.scope("no-such-ref", root)[0] == cs.CODE
    assert cs.scope("main", root.parent)[0] == cs.CODE  # not a repository


def _run_lane(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ALLWELL_SCOPE_BASE": "main", "ALLWELL_TEST_WORKERS": "0"}
    env.pop("ALLWELL_FULL_LANE", None)
    return subprocess.run(["bash", "scripts/test.sh", *args, "-p", "no:cacheprovider"], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_fast_lane_runs_nothing_for_a_version_only_change(tmp_path):
    root = _repo(tmp_path)
    _write(root, "version.py", '__version__ = "1.0.1"\n')
    run = _run_lane(root)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "only the version number changed" in run.stderr
    assert "passed" not in run.stdout and "failed" not in run.stdout


def test_the_fast_lane_runs_only_the_docs_lane_for_a_docs_change(tmp_path):
    root = _repo(tmp_path)
    _write(root, "_Docs/Markdowns/README_WellViewer.md", "# Manual\nedited\n")
    run = _run_lane(root, "--no-ui")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout, run.stdout
    # The failing test outside the lane runs once the change is code, which is what proves it was left out.
    _write(root, "app.py", "X = 4\n")
    run = _run_lane(root, "--no-ui")
    assert run.returncode != 0 and "outside the docs lane" in run.stdout, run.stdout


def test_full_lane_and_named_files_are_never_narrowed(tmp_path):
    root = _repo(tmp_path)
    _write(root, "version.py", '__version__ = "1.0.1"\n')
    run = subprocess.run(["bash", "scripts/test.sh", "-p", "no:cacheprovider"], cwd=root,
                         env={**os.environ, "ALLWELL_SCOPE_BASE": "main", "ALLWELL_TEST_WORKERS": "0",
                              "ALLWELL_FULL_LANE": "1"},
                         capture_output=True, text=True, timeout=120)
    assert "outside the docs lane" in run.stdout, run.stdout
    run = _run_lane(root, "tests/test_unrelated.py")
    assert "outside the docs lane" in run.stdout, run.stdout
