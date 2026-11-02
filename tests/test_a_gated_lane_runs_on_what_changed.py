"""A gated lane runs when a change can reach one of its tests, and not otherwise.

``scripts/lane_needed.py`` follows a changed definition through every module that imports it, by the names each
importer actually takes from it, until it reaches a roster file, a file a roster file names by path, or nothing.
These tests pin each part of that on small synthetic trees, plus the facts about the real rosters the rule relies
on. Each was written against a defect the rule it replaced had:

* a test helper (``tests/_fake_remote.py``, nine remote test files) or another test module borrowed by a roster
  file could change without starting the lane, because every ``tests.`` import was skipped;
* a module two imports away from a lane test could change without starting it;
* unanchored words matched unrelated files (``startup/mpl_bootstrap.py`` started the remote lane on ``bootstrap``);
* a path pattern won before the narrowing, so any edit under ``pipeline/`` started the nanowell lanes;
* the scripts that decide which lanes run started every lane, though no lane runs them.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def lane():
    spec = importlib.util.spec_from_file_location("lane_needed_under_test", ROOT / "scripts" / "lane_needed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── what changed inside one module ───────────────────────────────────────────

BEFORE = '''"""A module."""
import os

LIMIT = 3


class Colors:
    """Tokens."""
    run_done = "#4ADE80"
    run_pending = "#5F6B7C"
    accent = "#3B6FE8"


def over(n):
    """Whether n is over the limit."""
    return n > LIMIT


def unrelated():
    return os.sep


if __name__ == "__main__":
    print(over(1))
'''


def test_a_changed_class_member_names_only_that_member(lane):
    after = BEFORE.replace('run_done = "#4ADE80"', 'run_done = "#10B981"')
    assert lane.affected_names(BEFORE, after) == {"run_done"}


def test_a_definition_that_uses_a_changed_one_is_affected_too(lane):
    """``over`` reads ``LIMIT``, and the script block calls ``over``: both see the new limit."""
    after = BEFORE.replace("LIMIT = 3", "LIMIT = 4")
    assert lane.affected_names(BEFORE, after) == {"LIMIT", "over", "<main>"}


def test_docstrings_and_comments_change_nothing(lane):
    after = BEFORE.replace('"""Tokens."""', '"""The tokens."""').replace(
        '    return os.sep', '    # the separator\n    return os.sep')
    assert lane.affected_names(BEFORE, after) == set()


def test_an_added_import_names_only_what_it_binds(lane):
    """An import is a definition of the name it binds: adding one changes nothing that does not use the name, so
    a module gaining an import is not, on that account, a change to everything in it."""
    after = BEFORE.replace("import os", "import os\nimport sys")
    assert lane.affected_names(BEFORE, after) == {"sys"}


def test_a_script_block_is_its_own_definition(lane):
    after = BEFORE.replace("print(over(1))", "print(over(2))")
    assert lane.affected_names(BEFORE, after) == {"<main>"}


@pytest.mark.parametrize("after", [
    BEFORE.replace("LIMIT = 3", "LIMIT = 3\nif os.sep:\n    LIMIT = 5"),  # a module statement that defines nothing
    BEFORE.replace("class Colors:", "class Colors:\n    if True:\n        x = 1\n"),  # a non-definition in a class
    BEFORE + "\ndef broken(:\n",                                        # does not parse
    None,                                                               # deleted
], ids=["module-statement", "class-statement", "unparseable", "deleted"])
def test_what_cannot_be_narrowed_is_undecidable(lane, after):
    assert lane.affected_names(BEFORE, after) is None


def test_a_new_file_is_undecidable(lane):
    assert lane.affected_names(None, BEFORE) is None


# ── following a change through the tree ──────────────────────────────────────

def _tree(lane, monkeypatch, tmp_path: Path, files: dict[str, str], roster=("test_lane.py",)):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    monkeypatch.setattr(lane, "GATES", {"g": (({name: "a lane test" for name in roster},), ())})
    return lane.Tree(tmp_path, sorted(files))


APP = {
    "pkg/__init__.py": "",
    "pkg/core.py": """
        def used():
            return 1

        def unused():
            return 2
        """,
    "pkg/middle.py": """
        from pkg import core

        def wraps():
            return core.used()

        def plain():
            return 0
        """,
}


def _reach(lane, tree, path, names, named=None):
    found = lane.code_reaches("g", {path: names}, tree, named)
    return found[1] if found else ""


def test_a_change_reaches_a_lane_test_only_through_a_name_it_takes(lane, monkeypatch, tmp_path):
    tree = _tree(lane, monkeypatch, tmp_path, {**APP, "tests/test_lane.py": """
        from pkg.core import used

        def test_it():
            assert used() == 1
        """})
    assert "uses used" in _reach(lane, tree, "pkg/core.py", {"used"})
    assert _reach(lane, tree, "pkg/core.py", {"unused"}) == ""


def test_a_change_two_imports_away_reaches_the_lane_test(lane, monkeypatch, tmp_path):
    """The defect the direct-import rule could not see: the test imports ``middle``, the change is in ``core``."""
    tree = _tree(lane, monkeypatch, tmp_path, {**APP, "tests/test_lane.py": """
        import pkg.middle as m

        def test_it():
            assert m.wraps() == 1
        """})
    why = _reach(lane, tree, "pkg/core.py", {"used"})
    assert "tests/test_lane.py uses wraps" in why and "pkg/core.py → pkg/middle.py" in why, why
    assert _reach(lane, tree, "pkg/core.py", {"unused"}) == ""


def test_a_shared_attribute_name_on_another_object_is_not_a_use(lane, monkeypatch, tmp_path):
    """Matching bare words would take ``other.used()`` for a use of ``pkg.core.used``. Only names reached through
    the module itself count."""
    tree = _tree(lane, monkeypatch, tmp_path, {**APP, "tests/test_lane.py": """
        from pkg.core import unused

        def test_it(other):
            assert other.used() and unused() == 2
        """})
    assert _reach(lane, tree, "pkg/core.py", {"used"}) == ""
    assert "uses unused" in _reach(lane, tree, "pkg/core.py", {"unused"})


def test_every_reached_lane_test_is_reported_and_no_other(lane, monkeypatch, tmp_path):
    """Selection runs exactly the files reached: one that uses the change, one that reaches it through another
    module, and not the one that takes only an unchanged name."""
    tree = _tree(lane, monkeypatch, tmp_path, {
        **APP,
        "tests/test_direct.py": "from pkg.core import used\n\ndef test_it():\n    assert used()\n",
        "tests/test_through.py": "from pkg import middle\n\ndef test_it():\n    assert middle.wraps()\n",
        "tests/test_other.py": "from pkg.core import unused\n\ndef test_it():\n    assert unused()\n",
    }, roster=("test_direct.py", "test_through.py", "test_other.py"))
    found = lane.reach_all("g", {"pkg/core.py": {"used"}}, tree)
    assert set(found) == {"tests/test_direct.py", "tests/test_through.py"}, found
    assert set(lane.reach_all("g", {"pkg/core.py": {"unused"}}, tree)) == {"tests/test_other.py"}


def test_an_autouse_fixture_or_hook_runs_the_whole_lane(lane, monkeypatch, tmp_path):
    tree = _tree(lane, monkeypatch, tmp_path, {
        "tests/conftest.py": "import pytest\n\n@pytest.fixture(autouse=True)\ndef _guard():\n    yield\n\n"
                             "def pytest_collection_modifyitems(items):\n    pass\n",
        "tests/test_lane.py": "def test_it():\n    assert True\n",
    })
    assert set(lane.reach_all("g", {"tests/conftest.py": {"_guard"}}, tree)) == {lane.ALL}
    assert set(lane.reach_all("g", {"tests/conftest.py": {"pytest_collection_modifyitems"}}, tree)) == {lane.ALL}


@pytest.mark.parametrize("helper", ["tests/_fake.py", "tests/test_borrowed.py"])
def test_a_test_helper_or_a_borrowed_test_module_reaches_the_lane_test(lane, monkeypatch, tmp_path, helper):
    """The recall gap the rework was for: ``tests/_fake_remote.py`` is imported by nine remote test files and
    changing it started nothing, because every ``tests.`` import was skipped."""
    module = Path(helper).stem
    tree = _tree(lane, monkeypatch, tmp_path, {
        helper: "class Fake:\n    def run(self):\n        return 1\n",
        "tests/__init__.py": "",
        "tests/test_lane.py": f"from tests.{module} import Fake\n\ndef test_it():\n    assert Fake().run()\n",
    })
    assert "uses Fake" in _reach(lane, tree, helper, {"run"})


def test_an_import_kept_only_for_its_side_effects_is_reached_by_an_undecidable_change(lane, monkeypatch,
                                                                                       tmp_path):
    tree = _tree(lane, monkeypatch, tmp_path, {**APP, "tests/test_lane.py": """
        import pkg.core  # noqa: F401  registers something

        def test_it():
            assert True
        """})
    assert "imports it whole" in _reach(lane, tree, "pkg/core.py", None)
    assert _reach(lane, tree, "pkg/core.py", {"unused"}) == ""


def test_a_module_named_in_a_string_is_reached_through_the_name_it_asks_for(lane, monkeypatch, tmp_path):
    """The feature registry imports its features by a string, with the attribute as another string."""
    tree = _tree(lane, monkeypatch, tmp_path, {**APP, "pkg/registry.py": """
        import importlib

        def build():
            return getattr(importlib.import_module("pkg.core"), "used")()
        """, "tests/test_lane.py": """
        from pkg.registry import build

        def test_it():
            assert build() == 1
        """})
    assert "uses build" in _reach(lane, tree, "pkg/core.py", {"used"})
    assert _reach(lane, tree, "pkg/core.py", {"unused"}) == ""


def test_a_file_a_lane_test_names_by_path_runs_the_lane_when_anything_reaches_it(lane, monkeypatch, tmp_path):
    """The stand-in cluster executes ``process_microscopy.py``; no test imports the names that matter."""
    files = {**APP, "process_microscopy.py": """
        from pkg import middle

        def main():
            return middle.wraps()
        """, "tests/test_lane.py": """
        from pathlib import Path

        ROOT = Path(__file__).parents[1]

        def test_it(runner):
            runner.start(ROOT / "process_microscopy.py")
        """}
    tree = _tree(lane, monkeypatch, tmp_path, files)
    assert lane.named_files("g", sorted(files), tmp_path) == {"process_microscopy.py": ("tests/test_lane.py", True)}
    named = lane.namers("g", sorted(files), tmp_path)
    why = _reach(lane, tree, "pkg/core.py", {"used"}, named)
    assert "process_microscopy.py, which the lane test tests/test_lane.py runs" in why, why
    assert _reach(lane, tree, "pkg/core.py", {"unused"}, named) == ""


def test_a_file_a_lane_test_only_reads_is_reached_only_by_changing_it(lane, monkeypatch, tmp_path):
    """The bootstrap test reads the launcher's source to check its routing. A version bump reaches the launcher
    through ``allwell_report.py``, but the text the test read is the same, so the lane has nothing to run for."""
    files = {**APP, "launcher.py": """
        from pkg import middle

        def main():
            return middle.wraps()
        """, "tests/test_lane.py": """
        from pathlib import Path

        ROOT = Path(__file__).parents[1]

        def test_it():
            assert "main" in (ROOT / "launcher.py").read_text()
        """}
    tree = _tree(lane, monkeypatch, tmp_path, files)
    assert lane.named_files("g", sorted(files), tmp_path) == {"launcher.py": ("tests/test_lane.py", False)}
    named = lane.namers("g", sorted(files), tmp_path)
    assert _reach(lane, tree, "pkg/core.py", {"used"}, named) == ""


def test_a_conftest_fixture_reaches_only_the_tests_that_ask_for_it(lane, monkeypatch, tmp_path):
    tree = _tree(lane, monkeypatch, tmp_path, {
        "tests/conftest.py": """
            import pytest

            @pytest.fixture()
            def transport():
                return 1

            @pytest.fixture()
            def other():
                return 2

            @pytest.fixture(autouse=True)
            def _guard():
                yield
            """,
        "tests/test_lane.py": "def test_it(transport):\n    assert transport\n",
    })
    assert "asks for the conftest fixture transport" in _reach(lane, tree, "tests/conftest.py", {"transport"})
    assert _reach(lane, tree, "tests/conftest.py", {"other"}) == ""
    assert "which every test receives" in _reach(lane, tree, "tests/conftest.py", {"_guard"})
    assert "cannot be narrowed" in _reach(lane, tree, "tests/conftest.py", None)


# ── what is matched by name ──────────────────────────────────────────────────

def test_no_gate_pattern_is_an_unanchored_word(lane):
    """``bootstrap`` matched ``startup/mpl_bootstrap.py`` (matplotlib's fonts) and started the remote lane."""
    for gate, (_, patterns) in lane.GATES.items():
        loose = [p for p in patterns if not (p.startswith("^") and p.endswith("$"))]
        assert not loose, f"{gate}: {loose}"
        assert not any(p and __import__("re").search(p, "startup/mpl_bootstrap.py") for p in patterns)


def test_the_real_rosters_name_the_files_they_run_or_read(lane):
    """What ``named_files`` must find in the real roster files, or a change to these files would reach no lane."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    nanowell = {**lane.named_files("nanowell-e2e", tracked), **lane.named_files("nanowell-features", tracked)}
    remote = lane.named_files("remote", tracked)
    assert {"process_microscopy.py", "all_well_launcher.py"} <= nanowell.keys(), nanowell
    assert {"process_microscopy.py", "all_well_launcher.py", "requirements-remote.txt",
            "requirements-remote-cellpose.txt", "services/remote_bootstrap.py"} <= remote.keys(), remote
    # The pipeline is run; the launcher's source is only read.
    assert nanowell["process_microscopy.py"][1] and remote["process_microscopy.py"][1]
    assert not nanowell["all_well_launcher.py"][1] and not remote["all_well_launcher.py"][1]


def test_the_rosters_import_their_test_helpers(lane):
    assert "tests/_fake_remote.py" in lane.roster_imports("remote")
    assert "tests/_hold_cluster.py" in lane.roster_imports("remote")
    assert "tests/_nanowell_synth.py" in lane.roster_imports("nanowell-e2e")


def test_a_lane_test_borrowing_another_test_module_keeps_it_in_the_tree(lane):
    """``_relevant_files`` drops test files nothing gated imports; one a roster file borrows must stay."""
    paths = ["tests/test_nanowell_final_pass.py", "tests/test_batch_driver.py", "tests/test_bar_geometry.py",
             "pkg/core.py"]
    kept = lane._relevant_files("nanowell-features", paths, ROOT)
    assert "tests/test_batch_driver.py" in kept and "pkg/core.py" in kept
    assert "tests/test_bar_geometry.py" not in kept


# ── the harness files ────────────────────────────────────────────────────────

def _lanes_source() -> str:
    return (ROOT / "tests" / "_lanes.py").read_text(encoding="utf-8")


def test_a_ui_roster_row_reaches_neither_gate(lane):
    src = _lanes_source()
    after = src.replace("UI_LANE_FILES: dict[str, str] = {",
                        'UI_LANE_FILES: dict[str, str] = {\n    "test_zz_new_ui.py": "a new UI test",', 1)
    assert after != src
    for gate in lane.GATES:
        assert lane.lanes_change_reaches(gate, src, after) == "", gate
        assert lane.lanes_change_selects(gate, src, after) == {}, gate


def test_a_remote_roster_row_reaches_the_remote_gate_only(lane):
    src = _lanes_source()
    after = src.replace("REMOTE_FILES: dict[str, str] = {",
                        'REMOTE_FILES: dict[str, str] = {\n    "test_zz_new_remote.py": "a new remote test",', 1)
    assert after != src
    assert "changes this lane's rosters" in lane.lanes_change_reaches("remote", src, after)
    assert lane.lanes_change_selects("remote", src, after) == {
        "tests/test_zz_new_remote.py": "tests/_lanes.py adds it to this lane"}
    for gate in ("nanowell-e2e", "nanowell-features"):
        assert lane.lanes_change_reaches(gate, src, after) == ""
        assert lane.lanes_change_selects(gate, src, after) == {}


def test_a_changed_lane_trigger_runs_the_whole_lane(lane):
    src = _lanes_source()
    after = src.replace('r"^scripts/test\\.sh$",', 'r"^scripts/test\\.sh$",\n    r"^scripts/other\\.sh$",', 1)
    assert after != src
    for gate in lane.GATES:
        assert set(lane.lanes_change_selects(gate, src, after)) == {lane.ALL}, gate


def test_a_lanes_file_that_does_not_load_runs_the_lane(lane):
    src = _lanes_source()
    assert "cannot be compared" in lane.lanes_change_reaches("remote", src, "GATES = {")
    assert "cannot be compared" in lane.lanes_change_reaches("remote", None, src)
    assert set(lane.lanes_change_selects("remote", src, "GATES = {")) == {lane.ALL}


def test_only_the_pytest_table_of_pyproject_reaches_a_gate(lane):
    src = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ruff_only = src.replace("[tool.ruff]", "[tool.ruff]\n# a lint-only note\nfix = false", 1)
    assert ruff_only != src
    assert lane.pytest_config_change(src, ruff_only) == ""
    pytest_change = src.replace("[tool.pytest.ini_options]", "[tool.pytest.ini_options]\nxfail_strict = true", 1)
    assert pytest_change != src
    assert "pytest configuration" in lane.pytest_config_change(src, pytest_change)


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True)


@pytest.mark.parametrize("script", ["scripts/lane_needed.py", "scripts/change_scope.py"])
def test_the_scripts_that_choose_the_lanes_start_none_of_them(tmp_path, script):
    """Driven through the real command in a scratch repository: they decide which lanes run, and no lane runs
    them, so editing one is a fast-lane change (its own tests are there)."""
    root = tmp_path / "repo"
    for rel in ("scripts/lane_needed.py", "scripts/change_scope.py", "tests/_lanes.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    with (root / script).open("a", encoding="utf-8") as fh:
        fh.write("\n# a change\nCHANGED = True\n")
    for gate in ("nanowell-e2e", "nanowell-features", "remote"):
        done = subprocess.run(["python", "scripts/lane_needed.py", gate, "--base", "main"], cwd=root,
                              capture_output=True, text=True)
        assert done.returncode == 1, (gate, done.stdout, done.stderr)
        assert "not needed" in done.stdout


# ── selection through the real commands ──────────────────────────────────────

_SCRATCH_LANES = """
REMOTE_FILES = {"test_a.py": "uses app.a", "test_b.py": "uses app.b"}
GATES = {"remote": ((REMOTE_FILES,), (r"^scripts/test\\.sh$",))}
"""

_SCRATCH_CONFTEST = """
import pytest
from tests._lanes import REMOTE_FILES


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.fspath.basename in REMOTE_FILES:
            item.add_marker(pytest.mark.remote_lane)
"""


def _scratch_lane_repo(tmp_path: Path) -> Path:
    """A repository with two remote-lane tests, each importing its own module; ``test_b`` fails if it runs, so a
    run that should have left it out says so."""
    root = tmp_path / "repo"
    for rel in ("scripts/test.sh", "scripts/lane_needed.py", "scripts/change_scope.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    files = {
        "tests/__init__.py": "",
        "tests/_lanes.py": _SCRATCH_LANES,
        "tests/conftest.py": _SCRATCH_CONFTEST,
        "app/__init__.py": "",
        "app/a.py": "def value():\n    return 1\n",
        "app/b.py": "def value():\n    return 2\n",
        "tests/test_a.py": "from app.a import value\n\ndef test_a():\n    assert value() == 1\n",
        "tests/test_b.py": "from app.b import value\n\ndef test_b():\n    assert False, 'test_b ran'\n",
        "pyproject.toml": "[tool.pytest.ini_options]\nmarkers = ['remote_lane: x', 'slow: x']\n",
        ".gitignore": "__pycache__/\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "work")
    return root


def _run_lane(root: Path, **env: str) -> subprocess.CompletedProcess:
    full = {**os.environ, "ALLWELL_SCOPE_BASE": "main", "ALLWELL_TEST_WORKERS": "0", **env}
    full.pop("ALLWELL_FULL_LANE", None) if "ALLWELL_FULL_LANE" not in env else None
    return subprocess.run(["bash", "scripts/test.sh", "--remote", "-p", "no:cacheprovider"], cwd=root,
                          capture_output=True, text=True, env=full)


def test_the_remote_lane_runs_only_the_test_a_change_reaches(tmp_path):
    root = _scratch_lane_repo(tmp_path)
    (root / "app/a.py").write_text("def value():\n    return 2 - 1\n", encoding="utf-8")
    _git(root, "commit", "-qam", "change a")
    done = _run_lane(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout and "test_b ran" not in done.stdout, done.stdout
    assert "running the 1 remote test files this change can reach" in done.stderr


def test_a_change_no_lane_test_reaches_runs_nothing(tmp_path):
    root = _scratch_lane_repo(tmp_path)
    (root / "app/c.py").write_text("X = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "unrelated")
    done = _run_lane(root)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "no tests to run" in done.stderr and "passed" not in done.stdout


@pytest.mark.parametrize("how", ["full-lane", "harness"])
def test_the_whole_lane_runs_when_asked_or_when_the_harness_changes(tmp_path, how):
    root = _scratch_lane_repo(tmp_path)
    (root / "app/c.py").write_text("X = 1\n", encoding="utf-8")
    if how == "harness":
        with (root / "scripts/test.sh").open("a", encoding="utf-8") as fh:
            fh.write("\n# a harness change\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "change")
    done = _run_lane(root, **({"ALLWELL_FULL_LANE": "1"} if how == "full-lane" else {}))
    assert "test_b ran" in done.stdout, done.stdout + done.stderr
