"""Every debug flag ships off, and every flag is reachable from one place.

ARCHITECTURE §10.2 has always said the debug toggles are off by default. Two
were not: ``REVIEW_IMAGE_CHANNEL_SWITCH_DEBUG`` carried the comment "kept ON for
targeted diagnosis" and ``CELL_GATING_DEBUG`` was simply True, so every user's
log carried traces nobody had asked for.

They stayed on partly because they could not be turned off from the place the
documentation points at: ``allwell.main`` copied four named constants onto the
module and left the other six alone, so editing the top of ``allwell.py`` did
nothing for them. The wiring now walks ``debug_flags.FLAG_NAMES``; these tests
hold the roster, the wiring and the defaults to the same set.
"""

from __future__ import annotations

import ast
import pathlib

from well_viewer.core import debug_flags  # noqa: E402
from tests._repo_scan import parse_source

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _module_bools(path: pathlib.Path) -> dict[str, bool]:
    tree = parse_source(path)
    out: dict[str, bool] = {}
    for node in tree.body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for t in targets:
            if (isinstance(t, ast.Name) and t.id.isupper()
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, bool)):
                out[t.id] = value.value
    return out


def test_every_flag_ships_off():
    declared = _module_bools(_ROOT / "well_viewer" / "core" / "debug_flags.py")
    on = sorted(name for name, value in declared.items() if value)
    assert not on, (
        f"debug flag(s) shipping ON: {on}. ARCHITECTURE 10.2 says the toggles "
        "are off by default, and a flag left on after a diagnosis puts its "
        "trace in every user's log."
    )


def test_the_roster_names_every_flag():
    declared = set(_module_bools(_ROOT / "well_viewer" / "core" / "debug_flags.py"))
    assert set(debug_flags.FLAG_NAMES) == declared, (
        "FLAG_NAMES and the module's own booleans have diverged: "
        f"{sorted(set(debug_flags.FLAG_NAMES) ^ declared)}"
    )


def test_the_entry_point_can_set_every_flag():
    """A flag ``allwell.py`` does not define is a flag its constants cannot turn
    off, whatever the documentation says."""
    top = _module_bools(_ROOT / "allwell.py")
    missing = sorted(set(debug_flags.FLAG_NAMES) - set(top))
    assert not missing, (
        f"allwell.py defines no constant for {missing}, so editing the top of "
        "that file cannot change them"
    )


def test_the_entry_point_wires_them_by_roster():
    src = (_ROOT / "allwell.py").read_text()
    assert "_debug_flags.FLAG_NAMES" in src, (
        "allwell.main is back to naming individual flags, which is how six of "
        "them came to be unreachable"
    )
