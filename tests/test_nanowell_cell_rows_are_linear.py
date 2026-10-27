"""A nanowell well's per-cell rows cost the same per cell at a million cells as at ten.

``cell_records`` builds the rows of ``<prefix>_<well>.csv`` after the well's last step. It used to look each
fluorescence column up once per cell with ``cells.get(name, [None] * n)``, and Python builds that default list on
every call, whether or not the column exists — so each cell paid for a list as long as the whole well. Reported: a
remote run said "step 16/16 done" and then sat silent for one to two hours before the CSV was compressed and
fetched. Measured at the time, 50,000 cells took 10.5 s on that code and 0.2 s after the fix, and the cost grew as
the square of the cell count: an hour at about 0.9 million cells.
"""

from __future__ import annotations

from pipeline import nanowell_run as nr
from pipeline.nanowell_task import cell_records


class _Columns(dict):
    """The well's cell table, counting how often a fluorescence column is asked for."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mean_lookups = 0

    def get(self, key, default=None):
        if str(key).startswith("mean_"):
            self.mean_lookups += 1
        return super().get(key, default)

    def __getitem__(self, key):
        if str(key).startswith("mean_"):
            self.mean_lookups += 1
        return super().__getitem__(key)


def _outcome(n: int) -> nr.WellOutcome:
    outcome = nr.WellOutcome("B03")
    outcome.cells = _Columns({
        "label": list(range(1, n + 1)), "fov": ["f1"] * n, "timepoint": ["T00", "T01"] * (n // 2),
        "area_um2": [4.0] * n, "mean_GFP": [float(k) for k in range(n)],
        "nw_q": [1] * n, "nw_r": [2] * n, "nw_ring": [0] * n, "nw_label": [3] * n,
        "well_x_um": [10.0] * n, "well_y_um": [20.0] * n})
    return outcome


def test_each_fluorescence_column_is_looked_up_once_per_well_not_per_cell():
    outcome = _outcome(1000)
    cell_records(outcome, "B03", ["GFP", "RFP"], {"T00": 2.0, "T01": 2.0})
    lookups = outcome.cells.mean_lookups
    assert lookups == 2, (
        f"{lookups} lookups of a fluorescence column for 1000 cells and 2 channels: a "
        "per-cell lookup is what made a large well's rows take hours")


def test_the_rows_carry_each_cell_s_own_values():
    rows = cell_records(_outcome(4), "B03", ["GFP", "RFP"], {"T00": 2.0, "T01": 0.5})
    assert [r["GFP_mean_intensity"] for r in rows] == [0.0, 1.0, 2.0, 3.0]
    assert [r["RFP_mean_intensity"] for r in rows] == [None] * 4, "a channel with no column is blank, not zero"
    assert [r["area_px"] for r in rows] == [1.0, 16.0, 1.0, 16.0]
    assert [r["timepoint_hours"] for r in rows] == [0.0, 1.0, 0.0, 1.0]
    assert rows[2] == {"well": "B03", "fov": "f1", "timepoint": "T00", "timepoint_hours": 0.0, "nucleus_id": 3,
                       "area_px": 1.0, "GFP_mean_intensity": 2.0, "RFP_mean_intensity": None,
                       "nw_q": 1, "nw_r": 2, "nw_ring": 0, "nw_label": 3, "well_x_um": 10.0, "well_y_um": 20.0}
