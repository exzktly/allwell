"""Filename-schema inference for the Analyze input scan (``util.schema_infer``)."""

from __future__ import annotations

import zipfile

import pytest

from util.schema_infer import infer_from_dir, infer_schema, sample_stems


def _default_stems(channels=("DAPI", "GFP"), wells=("A01", "A02"), fovs=("f0", "f1"),
                   tps=("t0",), experiment="exp"):
    stems = []
    for w in wells:
        for fv in fovs:
            for t in tps:
                for c in channels:
                    stems.append(f"{experiment}_{c}_{w}_{fv}_{t}")
    return stems


def test_infer_default_schema_and_channels():
    guess = infer_schema(_default_stems())
    assert guess is not None
    assert guess.separator == "_"
    assert guess.fields == ["experiment", "channel", "well", "fov", "timepoint"]
    assert guess.channels == ["DAPI", "GFP"]
    assert guess.confident
    # Channel roles seed from the tokens (DAPI nuclear, GFP fluor).
    tokens = guess.channel_tokens()
    assert tokens.nuclear_token == "DAPI"
    assert tokens.fluor_tokens == ["GFP"]


def test_infer_detects_dash_separator():
    stems = [s.replace("_", "-") for s in _default_stems()]
    guess = infer_schema(stems)
    assert guess is not None
    assert guess.separator == "-"
    assert guess.fields == ["experiment", "channel", "well", "fov", "timepoint"]


def test_infer_without_experiment_or_fov():
    # channel_well_timepoint — no constant experiment slot, no fov slot.
    stems = [f"{c}_{w}_{t}"
             for w in ("A01", "B02") for t in ("t0", "t1") for c in ("DAPI", "GFP")]
    guess = infer_schema(stems)
    assert guess is not None
    assert guess.fields == ["channel", "well", "timepoint"]
    assert guess.channels == ["DAPI", "GFP"]
    assert guess.confident


def test_infer_returns_none_on_unparseable():
    assert infer_schema(["singlelongname", "anotherlongname"]) is None


def test_infer_not_confident_without_well():
    # No well slot anywhere → channel may still be found, but not confident.
    stems = [f"exp_{c}_{t}" for t in ("t0", "t1") for c in ("DAPI", "GFP")]
    guess = infer_schema(stems)
    assert guess is not None
    assert not guess.confident        # no well slot
    assert "well" not in guess.fields


def test_sample_stems_from_loose_tifs(tmp_path):
    d = tmp_path / "ds" / "in"
    d.mkdir(parents=True)
    for stem in _default_stems():
        (d / f"{stem}.tif").write_bytes(b"")
    # Points at the dataset root; sample_stems looks in <root>/in.
    stems = sample_stems(tmp_path / "ds")
    assert stems
    guess = infer_from_dir(tmp_path / "ds")
    assert guess is not None and guess.confident
    assert guess.channels == ["DAPI", "GFP"]


def test_sample_stems_from_per_well_zips(tmp_path):
    in_dir = tmp_path / "ds" / "in"
    in_dir.mkdir(parents=True)
    # One zip per well (the OME converter's output layout), each holding that
    # well's channel/fov members.
    for well in ("A01", "A02", "B01"):
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for c in ("DAPI", "GFP"):
                zf.writestr(f"exp_{c}_{well}_f0_t0.tif", b"")
    guess = infer_from_dir(tmp_path / "ds")
    assert guess is not None and guess.confident
    # Well variety came from across zips → the well slot was found.
    assert guess.fields == ["experiment", "channel", "well", "fov", "timepoint"]
    assert guess.channels == ["DAPI", "GFP"]


@pytest.mark.parametrize("select", ["root", "in"])
def test_sample_stems_from_per_well_folders(tmp_path, select):
    """The third grouped layout, which used to yield nothing at all.

    ``in/<WELL>/*.tif`` has no loose TIFs and no zips at either level, so the
    sampler returned an empty list, ``infer_from_dir`` returned ``None``, and
    the Analyze form kept whatever separator and schema the user last had —
    then the run failed on a schema it had never prompted them to fill in. It
    failed identically whether the dataset root or its ``in/`` was selected.
    """
    in_dir = tmp_path / "ds" / "in"
    for well in ("A01", "A02", "B01"):
        (in_dir / well).mkdir(parents=True)
        for c in ("DAPI", "GFP"):
            (in_dir / well / f"exp_{c}_{well}_f0_t0.tif").write_bytes(b"")

    target = tmp_path / "ds" if select == "root" else in_dir
    assert sample_stems(target)
    guess = infer_from_dir(target)
    assert guess is not None and guess.confident
    # Well variety came from across the folders → the well slot was found.
    assert guess.fields == ["experiment", "channel", "well", "fov", "timepoint"]
    assert guess.channels == ["DAPI", "GFP"]


def test_a_folder_that_is_not_a_well_is_not_sampled(tmp_path):
    """Only ``<WELL>/`` children count — a nesting level is not a well.

    ``in/t0/A01.zip`` is a shape the resolver accepts and this cannot name
    wells for; sampling ``t0/`` as though it were one would seed the form with
    a schema inferred from the wrong level.
    """
    in_dir = tmp_path / "ds" / "in" / "t0"
    in_dir.mkdir(parents=True)
    (in_dir / "exp_DAPI_A01_f0_t0.tif").write_bytes(b"")
    assert sample_stems(tmp_path / "ds") == []


def test_infer_from_dir_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    assert infer_from_dir(tmp_path / "empty") is None
