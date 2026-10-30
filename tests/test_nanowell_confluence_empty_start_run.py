"""A well's confluence is undefined unless its first image was empty, through the real run.

``nanowell_run.require_empty_start`` decides it from the reference timepoint's counts; this drives
``process_microscopy.main`` on a rendered well, as ``test_a_nanowell_well_is_read_in_place`` does, so the call in
``nanowell_task._finish_well`` and what reaches the sites CSV and the grid record are what is tested. Reported: a
plate imaged with its cells already loaded read 0-3 % confluent at every timepoint.
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd

from tests._nanowell_run_fixture import build_well, paint_cell
from tests.test_nanowell_registration_reuse import WELL, _run, _write_plate


def _outputs(tmp_path, monkeypatch, loaded):
    """The run's sites table and grid block for one rendered timepoint; *loaded* paints two cells into every
    nanowell (the renderer's own cells fall anywhere on the array, mostly on the lattice between nanowells)."""
    rendered, record = build_well(timepoint="0h")
    if loaded:
        # Each nanowell's cells at their own places: cells at one offset in every nanowell are the modal
        # template's floor there, and divide out of every one of them.
        rng = np.random.default_rng(5)
        for site in rendered.sites_um:
            for _ in range(2):
                angle, reach = rng.uniform(0, 2 * np.pi), rng.uniform(0, 28.0)
                paint_cell(rendered, site, dx_um=reach * np.cos(angle), dy_um=reach * np.sin(angle))
    out = _run(monkeypatch, _write_plate(tmp_path, rendered, record), "out")
    sites = pd.read_csv(out / "nanowells" / f"{WELL}.csv")
    grid = json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())
    return sites, grid["timepoints"]["0h"]


def test_a_well_loaded_at_its_first_image_has_no_confluence(tmp_path, monkeypatch):
    sites, block = _outputs(tmp_path, monkeypatch, loaded=True)
    seen = sites[sites["state"].isin(["measured", "empty"])]
    assert len(seen) and (seen["n_cells"] > 0).mean() > 0.05          # the fixture is loaded, by the run's count
    assert sites["confluence"].isna().all()                             # blank in the CSV, never 0
    assert block["confluence_reference"]["status"] == "loaded"
    assert any(n.startswith("Confluence is undefined for this well") for n in block["notes"])


def test_a_well_empty_at_its_first_image_keeps_its_confluence(tmp_path, monkeypatch):
    sites, block = _outputs(tmp_path, monkeypatch, loaded=False)
    seen = sites[sites["state"].isin(["measured", "empty"])]
    assert block["confluence_reference"]["status"] == "empty"
    assert len(seen) and seen["confluence"].notna().all()
    # Measured, and near zero: half the nanowells read exactly 0 and the few above it sit at the array's edge.
    assert seen["confluence"].median() == 0.0 and seen["confluence"].mean() < 0.02
