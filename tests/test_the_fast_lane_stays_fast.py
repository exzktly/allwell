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


# ── the lanes beside the fast lane ──────────────────────────────────────────
#
# tests/_lanes.py holds four rosters, each a lane of its own: the nanowell
# end-to-end runs, the other nanowell tests, the remote / HPC tests (all three
# gated on whether a change can reach them) and the GUI layout tests (local
# fast lane only, never CI). Every way this split can break fails silently: a
# renamed file drops out of its roster back into the fast lane; a CI job that
# stops running a lane leaves hundreds of tests run nowhere while every check
# stays green; a gate that stops seeing what its tests import skips the lane on
# exactly the change that breaks it; and a behaviour test that joins the UI
# roster stops being run by CI at all.

_ROSTER_MARKS = {
    "NANOWELL_E2E_FILES": ("nanowell_e2e", "--nanowell"),
    "NANOWELL_FEATURE_FILES": ("nanowell_feature", "--nanowell-features"),
    "REMOTE_FILES": ("remote_lane", "--remote"),
    "UI_LANE_FILES": ("ui_lane", "--ui"),
}


def _rosters():
    import tests._lanes as lanes

    return {name: getattr(lanes, name) for name in _ROSTER_MARKS}


def _lane_needed_module():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("lane_needed", root / "scripts" / "lane_needed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _marks_for(path):
    from types import SimpleNamespace

    import tests.conftest as harness

    class _Item(SimpleNamespace):
        def add_marker(self, mark):
            self.marks.append(mark.name)

    item = _Item(fspath=path, fixturenames=(), marks=[])
    harness.pytest_collection_modifyitems(None, [item])
    return item.marks


def test_the_conftest_still_exposes_the_nanowell_roster():
    from tests._lanes import NANOWELL_E2E_FILES as from_lanes
    from tests.conftest import NANOWELL_E2E_FILES as from_conftest

    assert from_conftest is from_lanes


def test_every_roster_file_exists_and_is_in_one_roster_only():
    here = Path(__file__).resolve().parent
    seen: dict[str, str] = {}
    for roster_name, roster in _rosters().items():
        missing = [name for name in roster if not (here / name).is_file()]
        assert not missing, f"{roster_name} names files that do not exist: {missing}"
        unexplained = [name for name, why in roster.items() if not why.strip()]
        assert not unexplained, f"{roster_name} entries with no description: {unexplained}"
        for name in roster:
            assert name not in seen, f"{name} is in both {seen[name]} and {roster_name}"
            seen[name] = roster_name


def test_a_nanowell_test_file_joins_a_nanowell_roster():
    """A new ``test_nanowell_*.py`` left off both rosters falls back into the fast
    lane, which is how the fast lane came to spend a third of its time on them."""
    from tests._lanes import NANOWELL_E2E_FILES, NANOWELL_FEATURE_FILES, UI_LANE_FILES

    import re

    here = Path(__file__).resolve().parent
    rostered = set(NANOWELL_E2E_FILES) | set(NANOWELL_FEATURE_FILES) | set(UI_LANE_FILES)
    # Exempt by what they are, not by name: a full-app file is ``slow`` and in no
    # fast lane, and a whole-tree guard runs on every push.
    exempt = re.compile(r"\b(viewer_app|loaded_app|loaded_app_shape|image_table_app"
                        r"|python_sources|parsed_sources|root_modules)\b")
    loose = sorted(p.name for p in here.glob("test_nanowell*.py")
                   if p.name not in rostered and not exempt.search(p.read_text(encoding="utf-8")))
    assert not loose, f"nanowell test files in no roster (tests/_lanes.py): {loose}"


def test_no_guard_is_in_a_lane():
    """A guard reads the whole tree, so any change can break it: it runs on every push."""
    import re

    here = Path(__file__).resolve().parent
    whole_tree = re.compile(r"\b(python_sources|parsed_sources|root_modules)\b")
    offenders = [name for roster in _rosters().values() for name in roster
                 if whole_tree.search((here / name).read_text(encoding="utf-8"))]
    assert not offenders, f"guards in a gated or local-only lane: {offenders}"


def test_no_full_app_file_is_in_the_ui_lane():
    """A full-app test there is ``slow`` already; listing it here says nothing true."""
    import re

    from tests._lanes import UI_LANE_FILES

    here = Path(__file__).resolve().parent
    full_app = re.compile(r"\b(viewer_app|loaded_app|loaded_app_shape|image_table_app)\b")
    offenders = [name for name in UI_LANE_FILES
                 if full_app.search((here / name).read_text(encoding="utf-8"))]
    assert not offenders, f"full-app files in the UI roster: {offenders}"


@pytest.mark.parametrize("roster_name", sorted(_ROSTER_MARKS))
def test_the_hook_marks_each_roster_with_its_own_mark(roster_name):
    """Asked of the hook directly: a lane run deselects the other rosters' tests,
    so looking for them among the session's items would find none."""
    mark = _ROSTER_MARKS[roster_name][0]
    first = next(iter(_rosters()[roster_name]))
    assert _marks_for(Path(__file__).parent / first) == [mark]
    assert _marks_for(Path(__file__)) == []


def test_the_script_runs_each_roster_in_its_own_lane_only():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "test.sh").read_text()
    fast = script.split('FAST="', 1)[1].split('"', 1)[0]
    for mark in ("nanowell_e2e", "nanowell_feature", "remote_lane"):
        assert f"not {mark}" in fast, f"the fast lane runs the {mark} tests"
    assert "ui_lane" not in fast, "the local fast lane no longer runs the UI tests"
    assert 'MARK="$FAST"' in script
    assert 'MARK="$FAST and not ui_lane"' in script, "--no-ui no longer leaves the UI tests out"
    for mark, flag in _ROSTER_MARKS.values():
        assert f'{flag})' in script and f'MARK="{mark} and not slow"' in script, flag


def test_ci_runs_every_gated_lane_behind_its_gate_and_never_the_ui_tests():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "scripts/test.sh --no-ui --junitxml=" in workflow, "CI's fast lane runs the UI tests"
    assert "scripts/test.sh --ui" not in workflow, "a CI job runs the UI tests"
    for flag in ("--nanowell", "--nanowell-features", "--remote"):
        assert f"scripts/test.sh {flag} --junitxml=" in workflow, f"CI no longer runs {flag}"
    for gate in ("nanowell-e2e", "nanowell-features", "remote"):
        assert f"python scripts/lane_needed.py {gate} --base" in workflow, f"CI skips the {gate} gate"
    assert workflow.count("full-ci") >= 4, "a job no longer runs whole under the full-ci label"
    assert workflow.count("python scripts/junit_gate.py") >= 4, \
        "a CI test run is not gated on its JUnit report"


def test_preflight_runs_every_gated_lane_behind_its_gate():
    import re

    root = Path(__file__).resolve().parents[1]
    preflight = (root / "scripts" / "preflight.sh").read_text()
    for flag, gate in (("--nanowell", "nanowell-e2e"), ("--nanowell-features", "nanowell-features"),
                       ("--remote", "remote")):
        assert re.search(rf'gated_lane "[^"]+" {flag} {gate}\n', preflight), f"preflight skips {flag}"


def test_each_gate_sees_its_tests_helpers_and_the_files_its_tests_name():
    """What each gate starts from: its roster files, the modules and test helpers they import (a helper used to
    be skipped, so ``tests/_fake_remote.py`` reached no lane), and the files they name by path. Python beyond
    that is followed by ``code_reaches`` (``test_a_gated_lane_runs_on_what_changed.py``)."""
    import subprocess

    lane = _lane_needed_module()
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                             check=True).stdout.split()
    nanowell = lane.roster_imports("nanowell-e2e") | lane.roster_imports("nanowell-features")
    for path in ("analyze/tab.py", "review/features/nanowell/controller.py", "tests/_nanowell_synth.py"):
        assert path in nanowell, path
    remote = lane.roster_imports("remote")
    for path in ("services/remote_runner.py", "tests/_fake_remote.py", "tests/_hold_cluster.py"):
        assert path in remote, path
    assert lane.path_reason("nanowell-features", "tests/test_nanowell_form.py") == "a test file in this lane"
    assert lane.path_reason("nanowell-e2e", "tests/test_nanowell_form.py") == ""
    assert lane.path_reason("remote", "tests/test_nanowell_form.py") == ""
    for gate in ("nanowell-e2e", "nanowell-features", "remote"):
        assert lane.path_reason(gate, "scripts/test.sh").startswith("lane surface"), gate
        assert "process_microscopy.py" in lane.named_files(gate, tracked), gate
        for path in ("startup/mpl_bootstrap.py", "services/acquisition_transport.py", "pipeline/czi_read.py",
                     "scripts/lane_needed.py", "scripts/change_scope.py"):
            assert lane.path_reason(gate, path) == "", (gate, path)


def test_an_unanswerable_diff_runs_the_lane():
    """No base to compare with must mean "run it", never a silent skip."""
    lane = _lane_needed_module()
    assert lane.main(["remote", "--base", "no-such-ref-anywhere"]) == 0
