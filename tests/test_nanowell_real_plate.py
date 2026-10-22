"""Section F of the nanowell tracker, on rendered tiles through ``locate_timepoint``'s real sequence.

The first real plate left most wells unaddressed and said nothing about the central square. These tests drive the
same chain the pipeline and Test detection run, with one input injected where a real plate differs from the
rendered one, and read what the run writes.
"""

from dataclasses import replace

import numpy as np

from pipeline import nanowell_detect as nd
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import PITCH, build_well

SETTINGS = nr.NanowellSettings("100um")


def _centre_off_site(monkeypatch):
    """The array's centre half a pitch from every site, as a fitted rim the tiles cut places it (D07: 47 %)."""
    real = nd.detect_sites

    def shifted(*args, **kwargs):
        found = real(*args, **kwargs)
        if not found.ok or found.boundary_centre_um is None:
            return found
        a1 = np.asarray(found.fit.a1, dtype=float)
        return replace(found, boundary_centre_um=tuple(np.asarray(found.boundary_centre_um) + a1 / 2))
    monkeypatch.setattr(nd, "detect_sites", shifted)


def test_a_well_with_no_square_says_why_the_square_was_refused(monkeypatch):
    rendered, record = build_well(square_centre=False)
    _centre_off_site(monkeypatch)
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.UNADDRESSED
    block = located.grid["fiducial"]
    assert block["best"] is not None and block["best"] < nd.MIN_SQUARENESS and block["scored"] >= 2
    sentence = (f"the central square was not found: the most square outline among the {block['scored']} sites "
                f"scored near the array centre reads {block['best']:.2f}, under the {nd.MIN_SQUARENESS:g} a square "
                "needs")
    assert block["reason"] == sentence
    assert located.reason.startswith("No accepted fiducial, array centre, or precise plate-rigid prediction; "
                                     + sentence + "; the array_centre lies ")
    assert located.grid["reason"] == located.reason and block["margin"] == nr.FIDUCIAL_MARGIN


def test_a_found_square_is_recorded_where_it_was_drawn():
    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert located.status == nr.OK and located.origin_method == "fiducial"
    block = located.grid["fiducial"]
    assert block["reason"] == "" and block["best"] >= nd.MIN_SQUARENESS
    assert block["best"] - block["runner_up"] > nr.FIDUCIAL_MARGIN
    assert np.hypot(*(np.asarray(block["position_um"]) - rendered.array.origin)) < PITCH / 10


def test_the_block_is_written_to_the_grid_record_as_plain_numbers():
    import json

    rendered, record = build_well()
    located = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    again = json.loads(json.dumps(located.grid["fiducial"]))
    assert again == located.grid["fiducial"]
