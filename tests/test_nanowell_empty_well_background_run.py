"""A nanowell's fluorescence measured above the named empty well's floor, through the real run.

``--nanowell_fluor_background empty_well`` takes every fluorescence number of a nanowell over the empty reference
well's floor level at the same timepoint, rather than over the bare floor of the measured well's own tile, which cells
crowding the nanowells can raise. Driven through ``process_microscopy.main`` on a rendered two-well plate whose two
wells have different GFP floors, so a background read from the measured well and one read from the empty well give
different numbers: the loaded well's GFP is 10 everywhere and the empty well's 30.
"""

from __future__ import annotations

import pandas as pd
import pytest

from pipeline import nanowell_reference as nref
from tests.test_nanowell_confluence_reference_run import DENSE, EMPTY, LOADED, _plate, _run, _well

pytestmark = pytest.mark.timeout(300)


def _all(column, value) -> bool:
    """Every value of *column* is *value* (a Series compared with ``pytest.approx`` is not elementwise)."""
    return column.tolist() == pytest.approx([value] * len(column))

#: GFP everywhere in each well: the two floors differ, so whose floor was subtracted shows in every number.
GFP = {LOADED: 10.0, EMPTY: 30.0}


def test_the_loaded_well_is_measured_above_the_empty_well_s_floor(tmp_path, monkeypatch):
    out, order = _run(monkeypatch, _plate(tmp_path, {LOADED: DENSE, EMPTY: 0}, gfp=GFP), "--nanowell_empty_well",
                      EMPTY, "--nanowell_fluor_background", "empty_well")
    assert order == [EMPTY, LOADED]
    assert nref.read_index(out)["timepoints"]["0h"]["fluorescence_floor"]["GFP"] == pytest.approx(30.0)
    loaded, block = _well(out, LOADED)
    empty, _block = _well(out, EMPTY)
    assert len(loaded) and _all(loaded["GFP_background"], 30.0)
    assert _all(loaded["GFP_mean_bg"], 10.0 - 30.0)       # its own floor would give 0
    assert _all(empty["GFP_background"], 30.0)
    assert _all(empty["GFP_mean_bg"], 0.0)
    assert not any(str(n).startswith(nref.FLUOR_NOTE_PREFIX) for n in block["notes"])


def test_the_default_measures_each_well_above_its_own_floor(tmp_path, monkeypatch):
    out, _order = _run(monkeypatch, _plate(tmp_path, {LOADED: DENSE, EMPTY: 0}, gfp=GFP), "--nanowell_empty_well",
                       EMPTY)
    loaded, _block = _well(out, LOADED)
    assert len(loaded) and _all(loaded["GFP_background"], 10.0)
    assert _all(loaded["GFP_mean_bg"], 0.0)


def test_a_reference_that_holds_cells_leaves_the_fluorescence_blank_with_the_reason(tmp_path, monkeypatch):
    out, _order = _run(monkeypatch, _plate(tmp_path, {LOADED: DENSE, EMPTY: 2}, gfp=GFP), "--nanowell_empty_well",
                       EMPTY, "--nanowell_fluor_background", "empty_well")
    assert nref.read_index(out)["timepoints"]["0h"]["status"] == nref.LOADED
    loaded, block = _well(out, LOADED)
    assert len(loaded)
    for column in ("GFP_background", "GFP_mean_bg", "GFP_integrated"):
        assert loaded[column].isna().all(), column                         # blank, never the own floor's numbers
    assert any(str(n).startswith(f"{nref.FLUOR_NOTE_PREFIX} GFP") and "not empty" in str(n) for n in block["notes"])
    assert pd.notna(loaded["n_cells"]).all()                                # the count does not depend on it
