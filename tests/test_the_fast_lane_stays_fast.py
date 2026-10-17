"""The harness rules that keep the fast lane from decaying again.

The lane took 10 minutes 47 seconds to run 6,840 tests. Three costs accounted
for roughly two thirds of that, and none of them was in any test's own logic:

* **215 s** re-polishing leaked widgets. Nothing reclaimed the widgets a test
  built, so the process carried 93,446 of them by the end of the forty
  ``test_a*.py`` files alone, and each of the five files that installs the
  application style sheet paid ``QApplication.setStyleSheet``'s walk over all
  of them — 73 s, 63 s, 35 s, 35 s and 9 s of fixture setup, for fixtures that
  cost between 0.03 s and 0.93 s when their files run on their own.
* **~150 s** re-parsing the source tree. Parsing it costs 2.8 s and about sixty
  guards each wanted the whole of it.
* **56 s** in ``time.sleep``. Four tests spawned a real ``rsync`` at a host
  alias that exists nowhere, and ``_rsync_with_retry`` reads that failure as a
  flaky network: three attempts, 2 + 4 + 8 s apart.

A fourth arrived later: **316 s** of fixture setup, because three autouse
fixtures each made a numbered temp directory per test, and pytest numbers one by
listing every directory the session has already made — so the cost per test grew
with the test count, and the total with its square.

Each fix is a fixture or a helper somebody could remove without any test going
red, which is the state these tests exist to prevent. They assert the
*mechanism*, not the wall clock: a timing assertion on a shared CI runner is a
flake, and the thing worth pinning is that the widgets go, the trees are
shared, and the binaries are not spawned.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from tests._repo_scan import REPO_ROOT, parse_source, python_sources, rel

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget  # noqa: E402


# ── The widgets a test builds do not outlive it ──────────────────────────────
#
# Two tests in sequence, because the behaviour under test *is* a sequence: the
# fixture's teardown is what the second one measures. They read the live count
# from the QApplication rather than a count of their own, so the assertion is
# about the population the style sheet has to walk.

#: The widgets the first test builds. A module-level list, because the point is
#: that a *live Python reference* is not what keeps them alive or lets them go:
#: an unreferenced ``QWidget`` is collected by Python anyway, so a test holding
#: nothing would pass with the reaper removed. These are still referenced when
#: the second test runs, and it is Qt's side of them that has to be gone.
_BUILT: list = []
_MARKER_NAME = "ReapedByTheHarness"


def test_a_test_may_build_as_many_top_level_widgets_as_it_likes(qapp):
    for _ in range(20):
        widget = QWidget()
        widget.setObjectName(_MARKER_NAME)
        widget.show()
        _BUILT.append(widget)
    live = [w for w in qapp.topLevelWidgets() if w.objectName() == _MARKER_NAME]
    assert len(live) == 20, "the widgets this test built are not there"


def test_and_the_next_test_does_not_inherit_them(qapp):
    """The teardown of ``_reap_widgets_the_test_created`` ran in between.

    Delete that fixture's body and this is the test that goes red — the twenty
    widgets above are still registered with the application, still matched
    against every rule of the style sheet, and still there at the end of the
    run along with everything else the lane ever built.
    """
    import shiboken6

    assert _BUILT, "the previous test did not run"
    survivors = [w for w in _BUILT if shiboken6.isValid(w)]
    assert survivors == [], (
        f"{len(survivors)} widgets from the previous test are still alive. "
        "Whatever the lane builds it now keeps, and "
        "QApplication.setStyleSheet walks all of it: that is the 215 s this "
        "fixture removed "
        "(tests/conftest.py::_reap_widgets_the_test_created).")
    assert not [w for w in qapp.topLevelWidgets()
                if w.objectName() == _MARKER_NAME]


@pytest.fixture(scope="module")
def _module_scoped_widget(qapp) -> QWidget:
    widget = QWidget()
    widget.setObjectName("OwnedByAModuleFixture")
    return widget


def test_a_module_scoped_widget_survives_its_first_test(_module_scoped_widget):
    _module_scoped_widget.show()
    assert _module_scoped_widget.isVisible()


def test_a_module_scoped_widget_survives_into_the_second(_module_scoped_widget):
    """The naive version of the reaper deleted these, and the failure read
    ``RuntimeError: Internal C++ object (QScrollArea) already deleted`` from a
    line that had never touched the widget.

    It survives because the snapshot is taken when the reaping fixture is set
    up, and pytest instantiates higher-scoped fixtures first — so anything a
    module-, class- or session-scoped fixture built is already in the snapshot
    and is never a candidate.
    """
    assert _module_scoped_widget.isVisible(), (
        "a module-scoped widget was reaped after the first test that used it")


# ── The source tree is parsed once ───────────────────────────────────────────


def test_two_guards_asking_for_the_same_file_get_the_same_tree():
    """Not merely an equal tree — the *same object*.

    A second parse is 2.8 s across the tree, and about sixty guards want it.
    If this ever returns two objects, every one of them is paying again.
    """
    path = python_sources()[0]
    assert parse_source(path) is parse_source(path)


def test_rewriting_a_file_is_not_answered_from_the_cache(tmp_path):
    """The cache key is the source text, so a file rewritten in place — which
    is what ``test_guards_bite.py`` does between cases — is re-parsed.

    Same path, same length, written back to back: a path-and-stat key answers
    the second call from the first parse whenever the filesystem's timestamp
    granularity is coarser than the gap between the writes, and the guard then
    measures a file it was handed one case ago.
    """
    path = tmp_path / "subject.py"
    path.write_text("x = 1\n")
    first = ast.dump(parse_source(path))
    path.write_text("x = 2\n")
    assert ast.dump(parse_source(path)) != first


def _parses_a_file_directly(tree: ast.Module) -> list[int]:
    """Lines calling ``ast.parse`` on the text of a file it just read.

    ``ast.parse(some_string)`` is fine and stays — a guard that builds a
    snippet to check its own rule has nothing to share. What this finds is
    ``ast.parse(path.read_text())``, which re-parses a file some other guard
    has already parsed this session.
    """
    def _reads_a_file(node) -> bool:
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "read_text")

    # The same read in two statements — ``source = path.read_text()`` then ``ast.parse(source)`` — inside a loop:
    # a sweep over the tree, which is what the shared parse exists for. Two guards spelled it that way and cost
    # six seconds a lane between them while this passed. A single file parsed once, outside a loop, costs
    # nothing worth sharing and is left alone.
    read_into: set = set()
    in_loop: set = set()
    for loop in ast.walk(tree):
        if isinstance(loop, (ast.For, ast.AsyncFor)):
            for node in ast.walk(loop):
                if isinstance(node, ast.Assign) and _reads_a_file(node.value):
                    read_into.update(t.id for t in node.targets if isinstance(t, ast.Name))
                in_loop.add(id(node))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "parse"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id.endswith("ast")):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _reads_a_file(first) or (id(node) in in_loop and isinstance(first, ast.Name) and first.id in read_into):
            out.append(node.lineno)
    return out


@pytest.mark.parametrize("source, lines", [
    ("import ast\ntree = ast.parse(path.read_text())\n", [2]),
    ("import ast\nfor path in files:\n    source = path.read_text()\n    tree = ast.parse(source, filename=str(path))\n",
     [4]),
    ("import ast\nsource = path.read_text()\ntree = ast.parse(source)\n", []),     # one file, once: nothing to share
    ("import ast\ntree = ast.parse('x = 1')\n", []),                     # a snippet of its own: fine
])
def test_the_re_parse_guard_sees_both_spellings_of_a_read(source, lines):
    assert _parses_a_file_directly(ast.parse(source)) == lines


def test_no_guard_parses_a_source_file_for_itself():
    """One parse of the tree per process, shared, or the cache buys nothing.

    Eighty-nine sites across fifty-seven files each read and parsed their own
    copy. The tree is 479 files and 767k nodes; parsing it is 2.8 s, and
    roughly sixty guards in the lane want all of it.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        for lineno in _parses_a_file_directly(parse_source(path)):
            offenders.append(f"{rel(path)}:{lineno}")
    assert not offenders, (
        "these parse a file they read themselves, instead of going through "
        "`tests._repo_scan.parse_source`, which parses each file once per "
        "process and hands every guard the same tree:\n  "
        + "\n  ".join(offenders))


# ── No test reaches the network ──────────────────────────────────────────────


def test_spawning_rsync_is_refused():
    """Sixteen fast-lane tests spawned ``ssh`` or ``rsync`` at host aliases
    that exist nowhere, and the four that reached ``rsync`` paid its
    2 + 4 + 8 s retry backoff for it. On a machine where the binary is present
    they are tests going to the network."""
    from tests.conftest import NetworkToolInTest

    with pytest.raises(NetworkToolInTest):
        subprocess.run(["rsync", "-a", "nowhere:/x", "/tmp/x"])


def test_spawning_ssh_is_refused_through_popen_too():
    """``run``, ``check_output`` and ``check_call`` all build a ``Popen``, and
    a module that did ``from subprocess import run`` would slip past a patch on
    the attribute — so the hook is on ``Popen.__init__``."""
    from tests.conftest import NetworkToolInTest

    with pytest.raises(NetworkToolInTest):
        subprocess.Popen(["ssh", "somewhere", "true"])


def test_the_rsync_version_probe_is_allowed():
    """``resolve_rsync_binary`` finds a usable binary this way. It is local, it
    is instant, and it reaches nobody — refusing it would break the tests that
    assert which flags the runner passes on which rsync version."""
    if not _have("rsync"):
        pytest.skip("no rsync on this machine")
    assert subprocess.run(["rsync", "--version"],
                          capture_output=True).returncode == 0


def test_an_ordinary_subprocess_is_untouched():
    """The guard names four binaries. Spawning the interpreter — which several
    tests do, to watch a worker outlive its timeout — is not one of them."""
    import sys

    assert subprocess.run([sys.executable, "-c", "print(1)"],
                          capture_output=True).returncode == 0


def _have(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


# ── An autouse fixture makes no numbered temp directory ──────────────────────
#
# ``tmp_path`` and ``tmp_path_factory.mktemp`` pick each new directory's number
# by listing every entry already in the session's base temp directory, so one
# call per test costs time proportional to the tests that ran before it. Three
# autouse fixtures did that, and the lane's fixture setup came to 316 s against
# 259 s of test calls — enough to push the CI job past its 15-minute cap on
# every branch at once. ``conftest._autouse_scratch_root`` is the replacement.

def _autouse_fixtures_that_make_numbered_dirs(tree: ast.Module) -> list[str]:
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        autouse = any(
            isinstance(d, ast.Call)
            and any(k.arg == "autouse"
                    and isinstance(k.value, ast.Constant) and k.value.value
                    for k in d.keywords)
            for d in node.decorator_list)
        if not autouse:
            continue
        params = {a.arg for a in node.args.args}
        if "tmp_path" in params or "tmp_path_factory" in params:
            offenders.append(f"{node.name} takes a tmp_path fixture")
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "mktemp"):
                offenders.append(f"{node.name} calls .mktemp()")
    return offenders


def test_no_autouse_fixture_makes_a_numbered_temp_directory():
    # Every conftest in the repository, found from the root: the scan roster
    # in _repo_scan covers the application's packages, not tests/.
    conftests = sorted(p for p in REPO_ROOT.rglob("conftest.py")
                       if ".git" not in p.parts)
    assert conftests, "found no conftest.py — this guard would measure nothing"
    offenders = [
        f"{rel(path)}: {o}"
        for path in conftests
        for o in _autouse_fixtures_that_make_numbered_dirs(parse_source(path))
    ]
    assert not offenders, (
        "An autouse fixture makes a numbered temp directory per test, which "
        "makes the lane's setup time quadratic in its test count. Use "
        "conftest._scratch_dir(_autouse_scratch_root, ...) instead:\n  "
        + "\n  ".join(offenders))


def test_the_numbered_dir_guard_bites():
    bad = ast.parse(
        "import pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def a(tmp_path): pass\n"
        "@pytest.fixture(autouse=True)\n"
        "def b(tmp_path_factory): tmp_path_factory.mktemp('x')\n"
        "@pytest.fixture\n"
        "def not_autouse(tmp_path): pass\n")
    assert _autouse_fixtures_that_make_numbered_dirs(bad) == [
        "a takes a tmp_path fixture",
        "b takes a tmp_path fixture",
        "b calls .mktemp()",
    ]


@pytest.fixture(scope="module")
def _scratch_seen_by_a_module_fixture():
    """What a module-scoped fixture that runs the pipeline would extract into: it is set up before any test's own
    fixtures, so only a session-scoped one can have pointed it somewhere."""
    return os.environ.get("ALLWELL_SCRATCH_DIR")


def test_a_module_fixture_extracts_into_this_worker_s_own_scratch(_scratch_seen_by_a_module_fixture,
                                                                 tmp_path_factory):
    """Without it, two xdist workers each building a nanowell run in a module fixture shared ``~/.allwell/scratch``
    and deleted each other's ``_tmp_extract_A01`` mid-run."""
    from pathlib import Path

    seen = _scratch_seen_by_a_module_fixture
    assert seen, "a module-scoped fixture would extract into ~/.allwell/scratch, shared by every worker"
    assert Path(seen).resolve().is_relative_to(tmp_path_factory.getbasetemp().resolve())


# ── the nanowell end-to-end lane ────────────────────────────────────────────
#
# About 1,075 s of rendered-tile nanowell tests took the fast lane from ~2.5 to
# ~9.5 min and CI to within three minutes of its cap. They run in a lane of
# their own; these keep that split from quietly undoing itself. Either way it
# fails silently: a renamed file drops out of the roster back into the fast
# lane, and a CI job that stopped running the lane would leave 250 tests run
# nowhere while every check stays green.

def test_every_nanowell_lane_file_exists():
    from tests.conftest import NANOWELL_E2E_FILES

    here = Path(__file__).resolve().parent
    missing = [name for name in NANOWELL_E2E_FILES if not (here / name).is_file()]
    assert not missing, f"roster names files that do not exist: {missing}"


def test_the_fast_lane_leaves_the_nanowell_lane_out_and_ci_runs_it():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "test.sh").read_text()
    assert 'MARK="not slow and not nanowell_e2e"' in script
    assert 'MARK="nanowell_e2e and not slow"' in script
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "scripts/test.sh --nanowell --junitxml=.pytest-report.xml" in workflow, \
        "CI no longer runs the nanowell lane"
    assert workflow.count("python scripts/junit_gate.py .pytest-report.xml") >= 2, \
        "a CI test job is not gated on its JUnit report"


def test_the_roster_marks_its_tests():
    """The collection hook marks a roster file's tests, and only those.

    Asked of the hook directly: in a fast-lane run the roster's tests are
    deselected, so looking for them among the session's items would find none
    and pass without measuring anything.
    """
    from types import SimpleNamespace

    import tests.conftest as harness

    class _Item(SimpleNamespace):
        def add_marker(self, mark):
            self.marks.append(mark.name)

    inside = _Item(fspath=Path(__file__).parent / next(iter(harness.NANOWELL_E2E_FILES)),
                   fixturenames=(), marks=[])
    outside = _Item(fspath=Path(__file__), fixturenames=(), marks=[])
    harness.pytest_collection_modifyitems(None, [inside, outside])
    assert inside.marks == ["nanowell_e2e"] and outside.marks == []
