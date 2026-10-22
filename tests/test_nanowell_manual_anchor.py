"""Anchors set by hand for nanowell wells whose central square the run could not find (``util.nanowell_anchors``).

Reported: nanowell runs often fail to find the fiducial, and the timepoint is then left unaddressed. A person can
now click the square, and the click is the first source ``resolve_origin`` takes. These are the pieces tested
without rendering a plate: the file, the resolution, and the reuse keys that keep a timepoint computed under one
anchor from standing for another. ``test_nanowell_manual_anchor_run.py`` runs it end to end.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from pipeline.nanowell_lattice import FiducialCandidate, fit_lattice, resolve_origin
from tests._nanowell_synth import make_array
from util import nanowell_anchors as na


# ── the file ─────────────────────────────────────────────────────────────────
def test_an_anchor_is_set_replaced_and_cleared_and_no_anchors_leaves_no_file(tmp_path):
    path = na.anchors_path(tmp_path)
    assert path.name == "nanowell_anchors.json" and na.read_anchors(path) == {}
    na.set_anchor(path, "a1", "0h", (10.0, 20.0))
    na.set_anchor(path, "B02", "0h", (1.0, 2.0))
    na.set_anchor(path, "A01", "0h", (11.5, 21.5))                    # the same well, spelled the canonical way
    anchors = na.read_anchors(path)
    assert anchors == {("A01", "0h"): na.Anchor("A01", "0h", 11.5, 21.5),
                       ("B02", "0h"): na.Anchor("B02", "0h", 1.0, 2.0)}
    assert na.well_anchors(anchors, "A1") == {"0h": (11.5, 21.5)}
    assert na.clear_anchor(path, "A01", "0h") and not na.clear_anchor(path, "A01", "0h")
    assert na.clear_anchor(path, "B02", "0h") and not path.exists()


@pytest.mark.parametrize("payload, says", [
    ({"version": 2, "anchors": []}, "not a version-1 anchors file"),
    ({"version": 1, "anchors": [{"well": "Q99", "timepoint": "0h", "x_um": 1, "y_um": 2}]}, "names no valid well"),
    ({"version": 1, "anchors": [{"well": "A01", "x_um": 1, "y_um": 2}]}, "names no timepoint"),
    ({"version": 1, "anchors": [{"well": "A01", "timepoint": "0h", "x_um": "nan", "y_um": 2}]}, "not a number"),
    ({"version": 1, "anchors": [{"well": "A01", "timepoint": "0h", "x_um": 1, "y_um": 2},
                                {"well": "A1", "timepoint": "0h", "x_um": 5, "y_um": 6}]}, "A01 at 0h is anchored twice"),
])
def test_a_file_that_cannot_be_used_fails_the_well_and_says_why(tmp_path, payload, says):
    na.anchors_path(tmp_path).write_text(json.dumps(payload))
    with pytest.raises(nt.NanowellInputError) as exc:
        nt._well_manual_anchors(tmp_path, "A01")
    assert says in str(exc.value) and "nanowell_anchors.json" in str(exc.value)


def test_a_file_that_is_not_json_fails_the_well_too(tmp_path):
    na.anchors_path(tmp_path).write_text("{not json")
    with pytest.raises(nt.NanowellInputError, match="could not be read"):
        nt._well_manual_anchors(tmp_path, "A01")


# ── resolving the origin ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def fit():
    return fit_lattice(make_array(radius=1000, pitch=100, diameter=50).points)


def test_an_anchor_near_a_site_is_snapped_to_it_and_comes_before_every_other_source(fit):
    resolved = resolve_origin(fit, manual=(-12.0, 9.0), fiducial_margin=0.2)
    assert (resolved.status, resolved.method, resolved.agreement) == ("anchored", "manual", "single_source")
    np.testing.assert_allclose(resolved.origin_xy, (0, 0), atol=1e-9)
    # A square found one site over loses to the click, and the record says the two disagreed.
    square = FiducialCandidate((100.0, 0.0), 0.9, 0.3)
    over = resolve_origin(fit, manual=(3.0, -2.0), fiducial=square, array_centre=(100.0, 0.0), fiducial_margin=0.2)
    assert (over.method, over.agreement) == ("manual", "conflict")
    np.testing.assert_allclose(over.origin_xy, (0, 0), atol=1e-9)


def test_an_anchor_between_sites_leaves_the_timepoint_unaddressed_with_the_distance_rather_than_using_another(fit):
    square = FiducialCandidate((0.0, 0.0), 0.9, 0.3)
    refused = resolve_origin(fit, manual=(50.0, 0.0), fiducial=square, fiducial_margin=0.2)
    assert refused.status == "unaddressed" and refused.origin_xy is None
    assert refused.reason.startswith("The anchor set by hand lies 50.0 um from the nearest nanowell (50 % of a pitch)")


def test_a_click_off_the_array_is_refused_though_the_lattice_runs_under_it_and_an_undetected_square_is_not(fit):
    """Review finding: the fitted lattice is infinite, so a click 4 mm outside the array snapped to a site and was
    anchored with wrong addresses. The site must be near a nanowell found — but the square itself may be the one
    nanowell detection missed, and a click on it must still be taken."""
    points = make_array(radius=1000, pitch=100, diameter=50).points
    far = resolve_origin(fit, manual=(5003.0, 2.0), fiducial_margin=0.2, detected=points)
    assert far.status == "unaddressed" and far.reason.startswith(
        "The anchor set by hand is 4000 um (40.0 pitches) from the nearest nanowell found")
    without_square = points[np.hypot(points[:, 0], points[:, 1]) > 1.0]
    square = resolve_origin(fit, manual=(4.0, -3.0), fiducial_margin=0.2, detected=without_square)
    assert (square.status, square.method) == ("anchored", "manual")
    np.testing.assert_allclose(square.origin_xy, (0, 0), atol=1e-9)


def test_the_origin_is_described_as_set_by_hand():
    assert na.origin_phrase("manual") == "the anchor set by hand"
    assert na.origin_phrase("array_centre") == "the array's centre"


# ── reuse is keyed on the anchor ─────────────────────────────────────────────
def test_a_checkpoint_is_keyed_on_the_anchor_it_was_located_with():
    from types import SimpleNamespace

    outcome = SimpleNamespace(timepoints={"0h": {"record_digest": "d"}}, radius={})
    def key(anchor):
        located = SimpleNamespace(status="ok", origin_method="manual", manual_anchor_um=anchor)
        return nr._checkpoint_key(outcome, "0h", located, 100.0, None, nr.NanowellSettings("100um"), {})
    assert key((1.0, 2.0)) != key((101.0, 2.0)) and key((1.0, 2.0))["manual_anchor_um"] == [1.0, 2.0]
    assert "manual_anchor_um" not in key(None)                     # a well never clicked keeps its checkpoints


def test_an_incremental_run_recomputes_a_timepoint_whose_anchor_was_set_or_moved(tmp_path, monkeypatch):
    """0h is anchored by a click; 24h was carried from it by the end-of-run pass (``plate_rigid``); 48h found its
    own square. Moving or clearing the click must recompute 0h *and* 24h — the first version kept 24h, whose
    addresses came from the old click, and this test asserted that (review finding); 48h depends on no click."""
    import pipeline.settings_record as settings_record
    from pipeline import nanowell_measure as nm

    monkeypatch.setattr(settings_record, "well_settings_allow_reuse", lambda *a: True)
    monkeypatch.setattr(nr, "record_digest", lambda record, well, tp: "d")
    (tmp_path / nt.NANOWELL_DIR).mkdir()
    grid = {"scheme_version": nt.SCHEME_VERSION, "measure_version": nm.MEASURE_VERSION, "timepoints": {
        "0h": {"status": "ok", "record_digest": "d", "origin_method": "manual", "manual_anchor_um": [1.0, 2.0],
               "anchors_digest": "then"},
        "24h": {"status": "ok", "record_digest": "d", "origin_method": "plate_rigid", "anchors_digest": "then"},
        "48h": {"status": "ok", "record_digest": "d", "origin_method": "fiducial", "anchors_digest": "then"}}}
    (tmp_path / nt.NANOWELL_DIR / "A01_grid.json").write_text(json.dumps(grid))

    def kept(anchors, digest):
        prior = nt._prior_state(tmp_path, "A01", "m", "k", ["0h", "24h", "48h"], {}, anchors, digest)
        return None if prior is None else prior["kept"]
    assert kept({"0h": (1.0, 2.0)}, "then") == {"0h", "24h", "48h"}
    assert kept({"0h": (101.0, 2.0)}, "now") == {"48h"}                 # moved: 0h and what was carried from it
    assert kept({}, "") == {"48h"}                                       # cleared: the same
    assert kept({"0h": (1.0, 2.0), "48h": (5.0, 5.0)}, "now") == {"0h"}  # set where the fiducial had anchored it


def test_the_anchors_digest_is_the_plate_s_and_moves_with_any_well_s_click(tmp_path):
    na.set_anchor(na.anchors_path(tmp_path), "A01", "0h", (1.0, 2.0))
    _found, first = nt._well_manual_anchors(tmp_path, "B02")             # B02 has no click of its own
    na.set_anchor(na.anchors_path(tmp_path), "A01", "0h", (101.0, 2.0))
    found, second = nt._well_manual_anchors(tmp_path, "B02")
    assert found == {} and first and second and first != second
    na.clear_anchor(na.anchors_path(tmp_path), "A01", "0h")
    assert nt._well_manual_anchors(tmp_path, "B02") == ({}, "")


def test_a_manual_anchor_is_evidence_the_plate_re_anchoring_can_carry():
    from tests.test_nanowell_reanchor import _plate_grids

    grids, truth = _plate_grids(target_ref_method="manual")
    predictions, reasons = nt.plan_reanchor(grids)
    assert reasons == {} and "24h" in predictions["B02"]
