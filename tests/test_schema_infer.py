"""Filename-schema inference for the Analyze input scan (``util.schema_infer``)."""

from __future__ import annotations

import zipfile

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


def test_infer_from_dir_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    assert infer_from_dir(tmp_path / "empty") is None
