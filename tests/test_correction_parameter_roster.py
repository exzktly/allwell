"""A run's receipt names the correction settings that shaped it, and only those.

Home's *Run parameters* card named the background-subtraction method and the
flat-field mode and stopped there, so a plate corrected with `robust_mesh` at
one mesh ratio and one corrected at another produced identical receipts, and an
`empty_well` correction never said which wells it measured. The settings are all
in `pipeline_info.json` already — `qc_options` carries the mesh values, the FFT
cutoffs and `background_well`, and `execution_options` carries the radii — so
this was a reading gap, not a recording one.

Which settings a method consults is one question with one answer, kept beside
the dispatch that defines it: `pipeline.background.BACKGROUND_METHOD_PARAMS` and
`pipeline.flatfield.FLATFIELD_MODE_PARAMS`. Listing a mesh ratio beside
`sliding_disk` — which never sees one — asserts an effect that did not happen,
which is the same kind of untruth as omitting the ones that did. These guards
hold the two rosters against the methods that exist and against the dispatch
signatures they claim to describe.
"""

from __future__ import annotations

import inspect

import pytest


def test_every_background_method_declares_its_parameters():
    from pipeline.background import BACKGROUND_METHOD_PARAMS, BACKGROUND_METHODS

    missing = sorted(set(BACKGROUND_METHODS) - set(BACKGROUND_METHOD_PARAMS))
    assert not missing, (
        f"background method(s) {missing} have no entry in "
        "BACKGROUND_METHOD_PARAMS, so a run using one shows its name on the "
        "Home receipt and none of the settings that shaped it"
    )
    stale = sorted(set(BACKGROUND_METHOD_PARAMS) - set(BACKGROUND_METHODS))
    assert not stale, (
        f"BACKGROUND_METHOD_PARAMS names {stale}, which is not a background "
        "method any more"
    )


def test_every_flatfield_mode_declares_its_parameters():
    from process_microscopy import FLATFIELD_MODES

    from pipeline.flatfield import FLATFIELD_MODE_PARAMS

    assert set(FLATFIELD_MODE_PARAMS) == set(FLATFIELD_MODES), (
        "the flat-field mode roster and its parameter map disagree: "
        f"{sorted(set(FLATFIELD_MODES) ^ set(FLATFIELD_MODE_PARAMS))}"
    )


def test_the_declared_parameters_are_ones_the_dispatch_takes():
    """The liveness half. An entry naming a parameter `subtract_background` does
    not accept describes a run setting that reaches nothing."""
    from pipeline.background import BACKGROUND_METHOD_PARAMS, subtract_background

    accepted = set(inspect.signature(subtract_background).parameters)
    # `background_well` is not an argument of the dispatch — it is what produced
    # the `bg_field` the dispatch is handed — but it is a setting a reader needs
    # to reproduce the run, so it is sanctioned by name rather than by signature.
    accepted.add("background_well")
    for method, params in BACKGROUND_METHOD_PARAMS.items():
        unknown = sorted(set(params) - accepted)
        assert not unknown, (
            f"{method!r} claims parameter(s) {unknown}, which "
            f"subtract_background does not take; it accepts {sorted(accepted)}"
        )


@pytest.mark.parametrize("method,expected", [
    ("sliding_disk", ("radius",)),
    ("robust_mesh", ("radius", "mesh_ratio", "mesh_sigma")),
    ("fft_bandpass", ("radius", "fft_small")),
    ("fov_median", ()),
    ("none", ()),
    ("empty_well", ("background_well",)),
])
def test_background_params_used_matches_the_dispatch(method, expected):
    """Pinned against what `subtract_background`'s own docstring describes, so a
    change to the dispatch that forgets this map is caught here."""
    from pipeline.background import background_params_used

    assert background_params_used(method) == expected


def test_an_unknown_method_claims_no_parameters():
    from pipeline.background import background_params_used
    from pipeline.flatfield import flatfield_params_used

    assert background_params_used("something_new") == ()
    assert flatfield_params_used("") == ()


def test_one_background_well_parser():
    """The Review-image side had a second copy that split on ';' where the
    shared one did not, and the shared one took a pre-split list where that one
    did not. The shared one takes both now; the copy is gone."""
    from util.well_token import split_background_wells

    assert split_background_wells("H12, G12 ,h12") == ["H12", "G12"]
    assert split_background_wells("H12;G12") == ["H12", "G12"]
    assert split_background_wells(["H12", " G12 "]) == ["H12", "G12"]
    assert split_background_wells("") == []

    from well_viewer.features.image_browser.review_image import collect as RC

    assert RC._split_background_wells is split_background_wells, (
        "the review-image side is not using the shared parser"
    )
