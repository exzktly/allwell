"""A tab only offers the SEM/SD band control if its own draw path reads the flag.

``core.ui_helpers.make_band_controls`` builds the shared "Error Band: SEM/SD"
button. The button is **app-global**: it appends itself to ``ctx.runtime.sem``
and calls ``core.plot_toggles.toggle_sem``, which writes ``PlotState.use_sem`` —
the flag every aggregate plot path reads. So mounting it on a tab whose renderer
never reads that flag gives the user a control that silently retunes *other*
tabs while the figure under it does not change.

That is what the Distribution tab did. It draws per-group histograms, KDEs and
violins and never reduces to a mean, so there is no spread for a band to
describe, and ``use_sem`` appears nowhere in its package — but its card mounted
the shared control anyway, so clicking it moved Bar Plots, Line Graphs, the
Scatter aggregate, Batch Export and the CSV export between SEM and SD.

The rule: a module that mounts the control must sit in a package that reads
``use_sem``. Package scope rather than module scope because the tab builders
legitimately split the mount (``tab_view.py``) from the consumption
(``renderer.py`` / ``controller.py`` / ``collect.py``).
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import REPO_ROOT, python_sources, rel
from tests._repo_scan import parse_source

#: The helper's own module. It defines ``make_band_controls`` and embeds it from
#: the legacy ``attach_plot_toolbar`` bridge on behalf of whichever caller asked
#: for a toolbar, so the consumer is that caller's package, not this one.
#: Paired with :func:`test_the_exemption_still_describes_a_real_call` below, so
#: the entry cannot outlive the call it exempts.
EXEMPT = {"review/core/ui_helpers.py"}

_HELPER = "make_band_controls"
_FLAG = "use_sem"


def _local_names_for_helper(tree: ast.AST) -> set[str]:
    """Every local name bound to the helper, including import aliases."""
    names = {_HELPER}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == _HELPER and alias.asname:
                    names.add(alias.asname)
    return names


def _calls_helper(tree: ast.AST) -> bool:
    names = _local_names_for_helper(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in names:
            return True
        if isinstance(func, ast.Attribute) and func.attr == _HELPER:
            return True
    return False


def _reads_flag(tree: ast.AST) -> bool:
    """True for a real read/write of ``use_sem`` — not a docstring mention.

    ``barplot/state.py`` names the flag in its module docstring while the actual
    read lives in ``renderer.py``; a substring scan would count the prose and
    let a package that only *talks* about the flag satisfy the rule.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _FLAG:
            return True
        if isinstance(node, ast.Name) and node.id == _FLAG:
            return True
        if isinstance(node, ast.arg) and node.arg == _FLAG:
            return True
        if isinstance(node, ast.keyword) and node.arg == _FLAG:
            return True
    return False


def _package_of(path) -> str:
    """The directory whose sources count as one tab's implementation."""
    return rel(path.parent)


def find_bandless_consumers(mounts: dict[str, str],
                            readers: set[str]) -> list[str]:
    """The rule itself, over ``{module: package}`` mounts and reading packages.

    Split out from the repository scan so :func:`test_the_guard_bites` can hand
    it a case the repository does not currently contain.
    """
    return sorted(mod for mod, pkg in mounts.items() if pkg not in readers)


def _scan():
    mounts: dict[str, str] = {}
    readers: set[str] = set()
    for path in python_sources():
        try:
            tree = parse_source(path)
        except SyntaxError:  # pragma: no cover - a parse failure is another guard's job
            continue
        package = _package_of(path)
        if _reads_flag(tree):
            readers.add(package)
        if rel(path) in EXEMPT:
            continue
        if _calls_helper(tree):
            mounts[rel(path)] = package
    return mounts, readers


def test_every_band_control_has_a_consumer():
    mounts, readers = _scan()
    assert mounts, (
        "No module mounts make_band_controls at all. Either the helper was "
        "renamed and this guard is now measuring nothing, or the control was "
        "removed everywhere — update the guard in the same commit."
    )
    offenders = find_bandless_consumers(mounts, readers)
    assert not offenders, (
        "These modules mount the shared SEM/SD band control, but nothing in "
        "their package reads PlotState.use_sem, so the button retunes every "
        "other plot tab while their own figure is unchanged: "
        + ", ".join(offenders)
    )


def test_the_guard_bites():
    """The rule fires on a mount with no reader, and clears one with a reader."""
    mounts = {"review/features/example/tab_view.py": "review/features/example"}
    assert find_bandless_consumers(mounts, set()) == [
        "review/features/example/tab_view.py"
    ]
    assert find_bandless_consumers(mounts, {"review/features/example"}) == []


def test_an_import_alias_does_not_evade_the_guard():
    tree = ast.parse(
        "from review.core.ui_helpers import make_band_controls as _band\n"
        "def build(ctx, card):\n"
        "    card.setControlsWidget(_band(ctx, card))\n"
    )
    assert _calls_helper(tree)


def test_a_docstring_mention_is_not_a_read():
    assert not _reads_flag(ast.parse('"""Cross-tab options (use_sem, ...)."""\n'))
    assert _reads_flag(ast.parse("x = ctx.state.plot.use_sem\n"))


def test_the_exemption_still_describes_a_real_call():
    """An exempt file that stopped mounting the control is a stale entry."""
    for entry in sorted(EXEMPT):
        path = REPO_ROOT / entry
        assert path.is_file(), f"{entry} is exempt but does not exist."
        tree = parse_source(path)
        assert _calls_helper(tree), (
            f"{entry} is exempt from the band-control rule but no longer calls "
            f"{_HELPER}. Remove the exemption."
        )


def test_the_distribution_tab_does_not_mount_the_band_control():
    """The reported case, pinned by name.

    The generic rule above would also pass if someone taught the Distribution
    package to *mention* use_sem without drawing a band. This says the control
    is not on that tab.
    """
    path = REPO_ROOT / "review/features/distribution/tab_view.py"
    tree = parse_source(path)
    assert not _calls_helper(tree), (
        "The Distribution tab mounts the shared SEM/SD band control again. Its "
        "renderer draws per-group histograms / KDEs / violins and never reduces "
        "to a mean, so the button only retunes the other plot tabs."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
