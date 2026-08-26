"""The three harness rules that keep the fast lane from decaying again.

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

Each fix is a fixture or a helper somebody could remove without any test going
red, which is the state these tests exist to prevent. They assert the
*mechanism*, not the wall clock: a timing assertion on a shared CI runner is a
flake, and the thing worth pinning is that the widgets go, the trees are
shared, and the binaries are not spawned.
"""

from __future__ import annotations

import ast
import subprocess

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
        if (isinstance(first, ast.Call)
                and isinstance(first.func, ast.Attribute)
                and first.func.attr == "read_text"):
            out.append(node.lineno)
    return out


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
