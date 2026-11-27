"""No popover is built.

``widgets.popover.Popover`` is a panel that floats over the page from the control
that opened it. Every job it did — a colour picker, a display range, a
colour-map list — is a page of the inspector, so nothing opens on top of
anything else. Every construction outside ``widgets/popover.py`` is on the
roster below, under whatever name the class was imported.
"""

from __future__ import annotations

from tests import _surface_guards as scan

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
POPOVERS: dict[tuple[str, str], str] = {
}


def unrostered_popovers(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.popovers(trees), POPOVERS if roster is None else roster)


def test_no_new_popovers():
    found = unrostered_popovers()
    assert not found, (
        'a Popover floats over the page: make it a page of the inspector, or roster it with a reason'
        ":\n  " + "\n  ".join(found))


def test_popovers_describes_only_real_sites():
    gone = scan.stale(scan.popovers(scan.app_trees()), POPOVERS)
    assert not gone, (
        "these POPOVERS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))
