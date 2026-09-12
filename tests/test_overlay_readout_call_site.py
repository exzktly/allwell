"""The cell-events call site must hand over a channel the loader can resolve.

``_install_cell_events`` takes both *chan* — what the cell is called, which for
an overlay cell is the ``OVERLAY_CHAN`` sentinel naming no image on disk — and
*readout_chan*, the channel whose pixels the hover readout actually reads. The
function's own fallback, ``readout = readout_chan or chan``, is pinned by
``tests/test_image_table_channel_identity.py``.

That test calls the function with ``readout_chan`` already supplied, so it
verifies the fallback and not the defect. The defect was at the **call site**:
the sentinel went out and nothing resolved it, so the readout was empty over the
whole overlay grid. Nulling the argument where the render loop passes it leaves
every one of those tests green — verification found exactly that, which is why
this file exists.

Driving the real render loop needs the full-app fixture, at roughly fourteen
seconds per construction. The property that matters is decidable from the syntax
tree instead: every call passes ``readout_chan``, and the one in the render loop
derives it from the overlay members rather than from the cell's own token.
"""

from __future__ import annotations

import ast

from tests._repo_scan import REPO_ROOT
from tests._repo_scan import parse_source

_MODULE = (REPO_ROOT / "review" / "features" / "image_browser"
           / "image_table" / "controller.py")


def _calls() -> list[ast.Call]:
    tree = parse_source(_MODULE)
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_install_cell_events"
    ]


def test_every_call_site_supplies_a_readout_channel():
    calls = _calls()
    assert calls, "no call site found — has the function been renamed?"
    missing = [
        c.lineno for c in calls
        if not any(kw.arg == "readout_chan" for kw in c.keywords)
    ]
    assert not missing, (
        "_install_cell_events called without readout_chan at line(s) "
        f"{missing}. An overlay cell then hands the loader the OVERLAY_CHAN "
        "sentinel, which resolves nothing, and the hover readout is silently "
        "empty across the whole overlay grid."
    )


def test_the_render_loop_derives_it_from_the_overlay_members():
    """Not merely present — derived from something that names a real image.

    ``readout_chan=cell["chan"]`` would satisfy the check above and reintroduce
    the defect exactly, since that is the sentinel the fallback already uses.
    """
    for call in _calls():
        for kw in call.keywords:
            if kw.arg != "readout_chan":
                continue
            src = ast.dump(kw.value)
            assert "overlay_chans" in src, (
                f"readout_chan at line {call.lineno} is not derived from the "
                "overlay members, so an overlay cell still resolves nothing"
            )
