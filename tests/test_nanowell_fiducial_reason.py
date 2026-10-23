"""An unaddressed timepoint says why the central square was refused (tracker F1).

Pure functions on hand-made scores: which of the square search's tests failed, in the sentence the run writes, and
the numbers a grid block keeps so the thresholds can later be set from real plates.
"""

import pytest

from pipeline.nanowell_detect import MIN_SQUARENESS, SquareScore, describe_fiducial
from pipeline.nanowell_lattice import FiducialCandidate, fit_lattice, resolve_origin
from tests._nanowell_synth import make_array


def _scores(*values):
    return [SquareScore((float(i), 0.0), v, 0.0) for i, v in enumerate(values)]


@pytest.fixture(scope="module")
def fit():
    return fit_lattice(make_array(radius=1000, pitch=100, diameter=50).points)


def test_no_scorable_site_is_named():
    block = describe_fiducial(None, [])
    assert block["reason"] == ("the central square was not found: no site near the array centre could be scored "
                               "for squareness")
    assert block["scored"] == 0 and block["best"] is None and block["runner_up"] is None


def test_one_scorable_site_is_named():
    block = describe_fiducial(None, _scores(0.9))
    assert block["reason"] == ("the central square was not found: only one site near the array centre could be "
                               "scored, and a lead over nothing is not a lead")
    assert block["best"] == 0.9 and block["runner_up"] is None


def test_a_square_under_the_floor_is_named_with_its_score_and_the_floor():
    # Ordered against the ranking, so a block that took the first score rather than the best is caught.
    block = describe_fiducial(None, _scores(0.12, 0.21, 0.2))
    assert block["reason"] == ("the central square was not found: the most square outline among the 3 sites scored "
                               f"near the array centre reads 0.21, under the {MIN_SQUARENESS:g} a square needs")
    assert (block["scored"], block["best"], block["runner_up"]) == (3, 0.21, 0.2)
    assert block["min_squareness"] == MIN_SQUARENESS


def test_a_search_with_no_centre_says_it_searched_the_montage():
    assert "sites scored in the montage reads" in describe_fiducial(None, _scores(0.1, 0.2), near_centre=False)["reason"]


def test_a_candidate_has_no_reason_and_keeps_its_place():
    block = describe_fiducial(FiducialCandidate((12.5, 40.0), 0.8, 0.1), _scores(0.1, 0.8))
    assert block["reason"] == "" and block["position_um"] == [12.5, 40.0]
    assert (block["best"], block["runner_up"]) == (0.8, 0.1)


def test_a_square_short_of_the_margin_is_named_with_both_scores(fit):
    resolved = resolve_origin(fit, fiducial=FiducialCandidate((0, 0), 0.52, 0.31), fiducial_margin=0.3)
    assert resolved.status == "unaddressed"
    assert resolved.reason == ("No accepted fiducial, array centre, or precise plate-rigid prediction; the most square "
                               "outline reads 0.52 and the next 0.31, a lead of 0.21 under the 0.3 a central square "
                               "needs")


def test_the_caller_s_refusal_joins_the_array_centre_s(fit):
    refusal = describe_fiducial(None, _scores(0.12, 0.21))["reason"]
    resolved = resolve_origin(fit, array_centre=(50, 0), fiducial_margin=0.3, fiducial_refusal=refusal)
    assert resolved.status == "unaddressed"
    assert resolved.reason.startswith("No accepted fiducial, array centre, or precise plate-rigid prediction; the "
                                      "central square was not found: the most square outline among the 2 sites")
    assert "; the array_centre lies 50.0 from the nearest site (50 % of a pitch)" in resolved.reason


def test_a_square_that_leads_but_is_not_far_enough_above_the_next_is_named(fit):
    resolved = resolve_origin(fit, fiducial=FiducialCandidate((0, 0), 0.45, 0.2), fiducial_margin=0.15,
                              fiducial_ratio=3.0)
    assert resolved.status == "unaddressed"
    assert resolved.reason.endswith("; the most square outline reads 0.45 and the next 0.20, 2.2 times it where a "
                                    "central square needs 3")


@pytest.mark.parametrize("best, runner_up, accepted", [
    (0.389, 0.025, True),           # the review's defocused square, refused by the old 0.4 floor
    (0.344, 0.024, True),           # its rounded, haloed, defocused, noisy one
    (0.40, 0.14, False),            # leads by 0.26 but is under three times the next
    (0.30, 0.16, False),            # three times? no; leads? no
    (0.45, 0.15, True),             # exactly three times
])
def test_the_square_is_judged_by_how_far_it_stands_above_the_next(fit, best, runner_up, accepted):
    from pipeline.nanowell_run import FIDUCIAL_MARGIN, FIDUCIAL_RATIO

    resolved = resolve_origin(fit, fiducial=FiducialCandidate((0, 0), best, runner_up),
                              fiducial_margin=FIDUCIAL_MARGIN, fiducial_ratio=FIDUCIAL_RATIO)
    assert (resolved.method == "fiducial") is accepted


def test_a_refusal_is_ignored_when_a_candidate_was_offered(fit):
    resolved = resolve_origin(fit, fiducial=FiducialCandidate((0, 0), 0.9, 0.1), fiducial_margin=0.3,
                              fiducial_refusal="never shown")
    assert resolved.status == "anchored" and resolved.method == "fiducial" and "never shown" not in resolved.reason
