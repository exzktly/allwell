"""Home's Run parameters card names the correction settings, not just the methods.

Reported: the card said what the background-subtraction method and the
flat-field mode were, and nothing about their parameters or the wells they
measured. Two plates corrected with `robust_mesh` at different mesh ratios
therefore produced identical receipts, and an `empty_well` correction never said
which wells it used — the one setting somebody needs to reproduce it.

Everything was already in `pipeline_info.json`: `qc_options` carries the mesh
values, the FFT cutoffs and `background_well`, and `execution_options` carries
the radii. This was a reading gap.

The settings shown are the ones the chosen method actually consults
(`pipeline.background.BACKGROUND_METHOD_PARAMS` /
`pipeline.flatfield.FLATFIELD_MODE_PARAMS`, read off the dispatch). Listing a
mesh ratio beside `sliding_disk`, which never sees one, would assert an effect
that did not happen — so the second half of these tests is what must *not*
appear.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from well_viewer.features.home import collect as hc


def _ctx(*, qc=None, execution=None):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        dataset=types.SimpleNamespace(
            data_dir=Path("/data/plate7"),
            pipeline_info={"qc_options": dict(qc or {}),
                           "execution_options": dict(execution or {})},
            well_paths={"A01": "x"},
            well_labels={},
        ),
        selection=types.SimpleNamespace(selections=[], plot_groups=[],
                                        notes_text=""),
    ))


def _rows(**kw):
    return {r["label"]: r for r in hc.run_parameters(_ctx(**kw))}


def _row(rows: dict, label: str) -> dict:
    """The receipt row called *label*, or a failure that says it is missing.

    A bare ``rows[label]`` raises ``KeyError``, which is what an absent row
    produces — and a ``KeyError`` from a guard measures nothing (it looks
    identical to the test itself being broken). Assert first.
    """
    assert label in rows, (
        f"the Run parameters card has no {label!r} row. It lists: "
        f"{sorted(rows)}"
    )
    return rows[label]


# ── the parameters a method consults are shown ──────────────────────────────

def test_a_morphological_method_shows_its_radius():
    rows = _rows(qc={"background": "sliding_disk"},
                 execution={"tophat_radius_nir": 100, "tophat_radius_fluor": 100})
    assert _row(rows, "Background subtraction")["value"] == "sliding disk"
    assert _row(rows, "Radius")["value"] == "100 px", (
        "the radius is the setting that decides what sliding_disk flattens, and "
        "the receipt did not carry it"
    )


def test_two_radii_are_both_named_when_they_differ():
    """One value would describe a run that did not happen."""
    rows = _rows(qc={"background": "top_hat"},
                 execution={"tophat_radius_nir": 60, "tophat_radius_fluor": 120})
    assert _row(rows, "Radius")["value"] == "60 px (nuclear), 120 px (fluorescence)"


def test_robust_mesh_shows_the_mesh_settings_it_is_built_on():
    rows = _rows(qc={"background": "robust_mesh", "mesh_ratio": 3.0,
                     "mesh_sigma": 2.5},
                 execution={"tophat_radius_nir": 80, "tophat_radius_fluor": 80})
    assert _row(rows, "Radius")["value"] == "80 px"
    assert _row(rows, "Mesh ratio")["value"] == "3"
    assert _row(rows, "Mesh sigma")["value"] == "2.5"


def test_fft_bandpass_shows_its_small_structure_cutoff():
    rows = _rows(qc={"background": "fft_bandpass", "fft_small_nir": 4,
                     "fft_small_fluor": 4},
                 execution={"tophat_radius_nir": 100, "tophat_radius_fluor": 100})
    assert _row(rows, "Small-structure cutoff")["value"] == "4 px"


def test_an_empty_well_background_names_the_wells_it_measured():
    rows = _rows(qc={"background": "empty_well", "background_well": "H12, G12"})
    assert _row(rows, "Empty well(s)")["value"] == "H12, G12"


def test_an_empty_well_flatfield_names_its_reference_wells():
    rows = _rows(qc={"flatfield": "empty_well", "background": "none",
                     "background_well": "H12"})
    assert _row(rows, "Reference well(s)")["value"] == "H12"


# ── and only those ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("absent", ["Mesh ratio", "Mesh sigma",
                                    "Small-structure cutoff", "Empty well(s)"])
def test_a_disk_run_does_not_claim_settings_it_never_read(absent):
    """`sliding_disk` sees a radius and nothing else. A receipt listing a mesh
    ratio beside it states that the number had an effect it did not have."""
    rows = _rows(qc={"background": "sliding_disk", "mesh_ratio": 3.0,
                     "mesh_sigma": 3.0, "fft_small_nir": 4, "fft_small_fluor": 4,
                     "background_well": "H12"},
                 execution={"tophat_radius_nir": 100, "tophat_radius_fluor": 100})
    assert absent not in rows


def test_fov_median_shows_no_parameters_at_all():
    """It subtracts a scalar — no radius, no reference well."""
    rows = _rows(qc={"background": "fov_median", "background_well": "H12"},
                 execution={"tophat_radius_nir": 100, "tophat_radius_fluor": 100})
    assert _row(rows, "Background subtraction")["value"] == "fov median"
    assert "Radius" not in rows and "Empty well(s)" not in rows


def test_gaussian_flatfield_shows_no_wells():
    """It estimates its field from the frame in front of it."""
    rows = _rows(qc={"flatfield": "gaussian", "background": "none",
                     "background_well": "H12"})
    assert "Reference well(s)" not in rows


# ── a correction that could not run says so ─────────────────────────────────

def test_an_empty_well_run_with_no_well_named_is_marked_a_warning():
    """No well means no field was built, so every frame passed through
    untouched and the output is bit-for-bit identical to a corrected one. "—"
    would read as a formatting gap rather than as the run not happening."""
    rows = _rows(qc={"background": "empty_well", "background_well": ""})
    row = _row(rows, "Empty well(s)")
    assert row["severity"] == "warn"
    assert "did not run" in row["value"]


# ── the debris row was reading keys the sidecar does not write ──────────────

def test_debris_gating_appears_for_a_run_that_used_it():
    """`write_pipeline_info` writes `remove_debris` / `min_area` / `max_area`
    into `qc_options`; the card read `qc_remove_debris` / `qc_min_area` /
    `qc_max_area`, which are the *argparse* names and are only in
    `execution_options`. The row could therefore never render."""
    rows = _rows(qc={"background": "none", "remove_debris": True,
                     "min_area": 50.0, "max_area": 5000.0})
    assert _row(rows, "Debris gating")["value"] == "area 50 – 5000 px²"


def test_debris_gating_stays_absent_for_a_run_that_did_not_use_it():
    rows = _rows(qc={"background": "none", "remove_debris": False,
                     "min_area": 50.0, "max_area": 5000.0})
    assert "Debris gating" not in rows
