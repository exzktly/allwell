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


_PLATE96 = [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)]
_FOVS9 = [f"f{i}" for i in range(9)]


def test_full_plate_loose_tifs_named_channel_first_sees_every_channel(tmp_path):
    # A leading slice of this sorted listing is all DIC — 864 names before the
    # first GFP. A channel slot holding one distinct value is not recognised as
    # the channel slot, so the scan used to report no channels at all and the
    # Analyze auto-fill had nothing to offer.
    d = tmp_path / "ds" / "in"
    d.mkdir(parents=True)
    for c in ("DIC", "GFP", "RFP"):
        for w in _PLATE96:
            for f in _FOVS9:
                (d / f"{c}_{w}_{f}_t0.tif").write_bytes(b"")
    guess = infer_from_dir(tmp_path / "ds")
    assert guess is not None
    assert sorted(guess.channels) == ["DIC", "GFP", "RFP"]


def test_full_plate_channel_major_zips_sees_every_channel(tmp_path):
    # Same shape in the per-well-zip layout: each archive holds all of one
    # channel's fields before the next channel starts, and only a few members
    # per archive are sampled.
    in_dir = tmp_path / "ds" / "in"
    in_dir.mkdir(parents=True)
    for w in _PLATE96:
        with zipfile.ZipFile(in_dir / f"{w}.zip", "w") as zf:
            for c in ("DIC", "GFP", "RFP"):
                for f in _FOVS9:
                    zf.writestr(f"exp_{c}_{w}_{f}_t0.tif", b"")
    guess = infer_from_dir(tmp_path / "ds")
    assert guess is not None
    assert sorted(guess.channels) == ["DIC", "GFP", "RFP"]


def test_phase_channel_does_not_take_the_segmentation_role(tmp_path):
    # End of the reported chain: the scan reports channels in sorted-filename
    # order, so DIC leads whatever order the images were acquired in. It must
    # still come back as the phase channel, not the segmentation one, and it must
    # stay in the channel list.
    d = tmp_path / "ds" / "in"
    d.mkdir(parents=True)
    for c in ("mCherry", "GFP", "DIC"):
        for w in ("A01", "A02", "B01"):
            for f in ("f0", "f1"):
                (d / f"exp_{c}_{w}_{f}_t0.tif").write_bytes(b"")
    guess = infer_from_dir(tmp_path / "ds")
    assert guess is not None
    assert guess.channels == ["DIC", "GFP", "mCherry"]
    tokens = guess.channel_tokens()
    assert tokens.phase_token == "DIC"
    assert tokens.nuclear_token != "DIC"
    assert "DIC" in tokens.fluor_tokens
