"""Each blank corrects its own block, and pooling is still there beside it.

The defect this feature exists for: a compound that fluoresces puts real signal
into its own block's blank, so pooling every blank into one plate-wide reference
corrects every well by a mixture that contains other treatments' autofluorescence.
Nothing in the output says so — the numbers look exactly like corrected ones.

The fixtures below are therefore built so that pooled and paired *cannot* agree:
the two blanks differ by a large, flat offset, and every assertion is on the
field's own values rather than on "the two differ", which the broken version
satisfies too.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import numpy as np
import pytest
import tifffile

from pipeline.empty_well import (
    BlankGroups,
    build_background_models,
    build_grouped_models,
    models_for_well,
    prepare_models,
    refuse_uncovered_wells,
    shared_kwargs_for_well,
)

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
SEP = "_"
#: The two blanks' constant levels. Far apart, so a field built from one cannot
#: be mistaken for a field built from the other or from their mean.
BRIGHT, DIM = 900.0, 100.0


def _write_plate(root, levels):
    """A per-well-zip plate where each well's frames sit at a constant level."""
    root.mkdir(parents=True, exist_ok=True)
    for well, level in levels.items():
        with zipfile.ZipFile(root / f"{well}.zip", "w") as zf:
            for fov in (1, 2):
                buf = BytesIO()
                tifffile.imwrite(buf, np.full((8, 8), level, np.float32))
                zf.writestr(f"exp_GFP_{well}_f{fov}_T01.tif", buf.getvalue())
    return root


@pytest.fixture()
def plate(tmp_path):
    """Two blanks at opposite levels, and one sample well in each block."""
    return _write_plate(tmp_path / "in", {
        "H12": BRIGHT, "A01": BRIGHT,      # block one, and its own bright blank
        "G12": DIM, "E01": DIM,            # block two, and its own dim blank
    })


def _levels(plate, *, background_well="", background_well_map=""):
    """The additive field each well would be corrected by, as one number."""
    groups = BlankGroups(background_well, background_well_map,
                         corrections=("empty_well",))
    grouped = build_grouped_models(
        plate, groups=groups, builder=build_background_models,
        tokens=["gfp"], schema=SCHEMA, sep=SEP)
    out = {}
    for well in ("A01", "E01"):
        models = models_for_well(grouped, groups, well)
        field = models["gfp"]["1"]
        out[well] = float(np.median(field))
    return out


def test_each_block_is_corrected_by_its_own_blank(plate):
    """The values, not "they differ" — a wrong pairing also produces two values."""
    paired = _levels(plate, background_well_map="H12=A01; G12=E01")
    assert paired["A01"] == pytest.approx(BRIGHT, abs=1.0)
    assert paired["E01"] == pytest.approx(DIM, abs=1.0)


def test_without_a_map_every_well_gets_the_pooled_reference(plate):
    """Pooling stays, and stays the same thing it always was.

    Both wells are corrected by one field built from both blanks, so it lands
    between them and matches neither — which is the whole complaint, and is
    correct behaviour for a plate whose blanks really are interchangeable.
    """
    pooled = _levels(plate, background_well="H12,G12")
    assert pooled["A01"] == pooled["E01"]
    assert DIM < pooled["A01"] < BRIGHT
    # And it is not either blank's own level, which is what the paired run gives.
    assert pooled["A01"] != pytest.approx(BRIGHT, abs=1.0)
    assert pooled["A01"] != pytest.approx(DIM, abs=1.0)


def test_a_pooled_run_of_one_blank_is_that_blank(plate):
    """The degenerate case both modes share, pinned so they cannot drift apart."""
    assert _levels(plate, background_well="H12")["A01"] == pytest.approx(BRIGHT, abs=1.0)
    assert _levels(plate, background_well_map="H12=A01,E01")["E01"] == \
        pytest.approx(BRIGHT, abs=1.0)


def test_a_well_carries_only_its_own_group_into_the_worker(plate):
    """The slice, asserted at the boundary a well's worker is handed.

    Not on the field values — this is the step that decides which models are
    *pickled* to each well, and shipping every group to every worker would be
    invisible in the output and cost the plate its memory. The private grouped
    keys must be gone, too: they are what would carry the other blocks along.
    """
    groups = BlankGroups("", "H12=A01; G12=E01", corrections=("empty_well",))
    shared: dict = {"background_models": {}, "other": "untouched"}
    prepare_models(
        shared, what="Background", mode="empty_well", input_dir=plate,
        groups=groups, builder=build_background_models, source=plate,
        flat_key="background_models", grouped_key="_grouped_background_models",
        tokens=["gfp"], schema=SCHEMA, sep=SEP)

    for well, expected in (("A01", BRIGHT), ("E01", DIM)):
        kwargs = shared_kwargs_for_well(shared, well)
        assert kwargs["other"] == "untouched"
        assert not [k for k in kwargs if k.startswith("_grouped")]
        assert "_blank_group_of_well" not in kwargs
        assert float(np.median(kwargs["background_models"]["gfp"]["1"])) == \
            pytest.approx(expected, abs=1.0)


def test_an_unpaired_run_is_handed_the_very_same_dict(plate):
    """Pooling costs nothing and copies nothing: same object, not an equal one.

    This is what makes "pooling is unchanged" a fact about the code rather than
    a claim about its output.
    """
    groups = BlankGroups("H12", "", corrections=("empty_well",))
    shared: dict = {"background_models": {}}
    prepare_models(
        shared, what="Background", mode="empty_well", input_dir=plate,
        groups=groups, builder=build_background_models, source=plate,
        flat_key="background_models", grouped_key="_grouped_background_models",
        tokens=["gfp"], schema=SCHEMA, sep=SEP)
    assert shared_kwargs_for_well(shared, "A01") is shared


def test_a_blank_that_produced_nothing_stops_the_run_even_if_others_did(plate):
    """Per-group, which is the point.

    The plate-wide guard asks whether *any* model was built, and on a paired run
    that is the wrong question: three of four blocks losing their blank still
    leaves one model standing and the run goes through, which is exactly the
    silently-uncorrected output the guard exists to prevent.
    """
    groups = BlankGroups("", "H12=A01; F12=E01", corrections=("empty_well",))
    with pytest.raises(SystemExit) as excinfo:
        prepare_models(
            {}, what="Background", mode="empty_well", input_dir=plate,
            groups=groups, builder=build_background_models, source=plate,
            flat_key="background_models",
            grouped_key="_grouped_background_models",
            tokens=["gfp"], schema=SCHEMA, sep=SEP)
    # F12 is the blank with no archive on this plate; H12's model built fine.
    assert "F12" in str(excinfo.value)


def test_a_well_no_blank_claims_refuses_the_run():
    groups = BlankGroups("", "H12=A01", corrections=("empty_well",))
    wells = [("A01", None), ("B07", None), ("C03", None), ("H12", None)]
    with pytest.raises(SystemExit) as excinfo:
        refuse_uncovered_wells(groups, wells)
    message = str(excinfo.value)
    assert "B07" in message and "C03" in message
    # The blank itself is a reference, not a sample, so it is not "uncovered".
    assert "H12" not in message.split("\n")[0]


def test_nothing_is_refused_when_the_map_shapes_nothing():
    """A map alongside a correction that never reads the blank changes no number,
    so it must not stop a run either."""
    unused = BlankGroups("", "H12=A01", corrections=("basic", "top_hat"))
    refuse_uncovered_wells(unused, [("A01", None), ("B07", None)])
    pooled = BlankGroups("H12,G12", "", corrections=("empty_well",))
    refuse_uncovered_wells(pooled, [("A01", None), ("B07", None)])


def test_re_pairing_a_plate_declines_to_reuse_its_previous_output(tmp_path):
    """Changing only the map must not let a well's previous output stand.

    The highest-stakes claim in the feature, and the one whose failure is
    silent: a re-run that reuses output computed against a different blank
    reports success and hands back the old numbers. It is *declared* by putting
    the option in ``settings_digest.KEYED_ON`` — and nothing else in the suite
    notices if that declaration is wrong, because the roster tests check that
    every option is classified, not that it is classified correctly. Moving this
    one key to ``NOT_KEYED_ON`` leaves the rest of this file green.

    So this asserts the consequence rather than the declaration: two runs that
    differ only in which blank corrects which well do not share a settings
    digest, and the second declines the first's output.
    """
    from pipeline.settings_record import (
        record_well_settings, well_settings_allow_reuse,
    )
    from util.settings_digest import settings_digest

    base = {"flatfield": "empty_well", "background": "empty_well",
            "background_well": "H12,G12", "segmentation_method": "stardist_nuclei"}
    one = settings_digest({**base, "background_well_map": "H12=A01; G12=E01"})
    other = settings_digest({**base, "background_well_map": "H12=E01; G12=A01"})
    assert one and other, "a digest of nothing would make this test vacuous"
    assert one != other

    # The real round trip: A01's output is recorded under one pairing, then
    # asked whether it may stand for the other.
    record_well_settings(tmp_path, "A01", one)
    assert well_settings_allow_reuse(tmp_path, "A01", one)
    assert not well_settings_allow_reuse(tmp_path, "A01", other)

    # …and a run that never used a map digests exactly as one from before the
    # option existed, so no plate already on disk is invalidated by adding it.
    assert settings_digest(base) == settings_digest(
        {**base, "background_well_map": None})
