"""The Image Table's hover MFI must describe the dataset that is open now.

The hover readout's per-well ``{(fov, tp): {nid: MFI}}`` index is memoized on
``ImageTableState.mfi_index`` so moving across cells reads nothing. The load's
swap path reset the Image Table's caches one field at a time and left that memo
out, so after opening another plate — or reloading this one after a re-run —
the readout went on quoting the previous data's MFI for whatever nucleus sat
under the cursor: a wrong number with nothing marking it as wrong. A lookup
queued before the swap could also land after it and write the old dataset's
index into the new dataset's memo.

These drive the app's own load (``app._load_path``), not ``clear_caches()``
directly: a test that calls the method the app forgot to call passes while
the app misbehaves.
"""

from __future__ import annotations

import csv

import numpy as np
import pytest

from review.features.image_browser.image_table import _hover_mfi
from review.features.image_browser.image_table import controller as it_controller
from tests.fixtures.mini_dataset import cell_gfp

pytestmark = pytest.mark.slow

_NID = 4


def _mask(*_a, **_k):
    m = np.zeros((4, 4), dtype=np.int32)
    m[1, 2] = _NID
    return m


def _hover(app, label) -> object:
    """One mouse move over nucleus ``_NID`` of A01 / GFP / 0h / FOV 1."""
    return it_controller._cell_mfi(app._ctx, label, "gfp", "A01", "0h", "1", 2, 1)


def _rewrite_gfp(dataset, delta: float) -> None:
    """What a re-run that changes the numbers leaves on disk."""
    path = dataset / "mini_A01.csv"
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["gfp_mean_intensity"] = str(float(row["gfp_mean_intensity"]) + delta)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


class _Label:
    """The attribute bag ``_cell_mfi`` keeps its per-cell record on."""


def test_a_reload_after_a_rerun_quotes_the_new_mfi(viewer_app, mini_dataset, monkeypatch):
    app = viewer_app
    app._load_path(mini_dataset)
    monkeypatch.setattr(_hover_mfi, "_load_mask_array", _mask)
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: job())
    before = cell_gfp("A01", 0.0, _NID)
    assert _hover(app, _Label()) == pytest.approx(before), "the fixture did not resolve the hovered nucleus"

    _rewrite_gfp(mini_dataset, 1000.0)
    app._load_path(mini_dataset)

    got = _hover(app, _Label())
    assert got == pytest.approx(before + 1000.0), (
        f"after a reload the hover quoted MFI {got}, the previous data's value "
        f"({before}), not this one's ({before + 1000.0})")


def test_a_lookup_queued_before_the_swap_cannot_write_after_it(viewer_app, mini_dataset, monkeypatch):
    app = viewer_app
    app._load_path(mini_dataset)
    monkeypatch.setattr(_hover_mfi, "_load_mask_array", _mask)
    queued: list = []
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: queued.append(job))
    old_label = _Label()
    assert _hover(app, old_label) is it_controller.MFI_PENDING
    assert len(queued) == 1

    _rewrite_gfp(mini_dataset, 1000.0)
    app._load_path(mini_dataset)
    queued.pop()()                                  # the worker gets to it only now

    assert app._ctx.state.image_table.mfi_index == {}, (
        "a lookup queued before the swap wrote into the new dataset's memo")
    assert _hover(app, old_label) is None, (
        "a cell rendered for the previous dataset still quotes an MFI")
    monkeypatch.setattr(_hover_mfi, "_submit_mfi", lambda job, **_k: job())
    assert _hover(app, _Label()) == pytest.approx(cell_gfp("A01", 0.0, _NID) + 1000.0)


def test_a_reload_drops_the_pooled_auto_lut_and_the_hover_array(viewer_app, mini_dataset, monkeypatch):
    """Two more per-dataset caches the field-by-field reset left out: the pooled
    Auto LUT window (pixels of the previous plate) and the one hover frame the
    readout holds (keyed by well/channel/tp/fov, not by dataset)."""
    app = viewer_app
    app._load_path(mini_dataset)
    st = app._ctx.state.image_table
    st.auto_lut_by_channel = {"gfp": (1.0, 2.0)}
    old, new = np.ones((2, 2)), np.zeros((2, 2))
    served = {"arr": old}
    monkeypatch.setattr(it_controller, "_HOVER_ARRAY", ((), None))
    monkeypatch.setattr(it_controller, "_load_array", lambda *a, **k: served["arr"])
    assert it_controller._hover_array(app._ctx, "A01", "gfp", "0h", "1", 0) is old

    app._load_path(mini_dataset)
    served["arr"] = new

    assert app._ctx.state.image_table.auto_lut_by_channel == {}, (
        "the previous dataset's pooled Auto LUT window survived the reload")
    got = it_controller._hover_array(app._ctx, "A01", "gfp", "0h", "1", 0)
    assert got is new, "the hover readout served the previous dataset's pixels"
