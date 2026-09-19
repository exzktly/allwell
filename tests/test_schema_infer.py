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


# ── the match report (what the guess parses, and what it does not) ──────────
#
# The readout these back exists because the guess is written into two form
# fields whose new values look no different from hand-entered ones, so a wrong
# guess is otherwise invisible until it turns up in a sidecar after a run.
# Each test below names the wrong implementation it excludes, because an
# assertion that a count "looks plausible" is satisfied by several of them.


def test_every_sampled_name_counts_as_parsed_on_a_clean_corpus():
    guess = infer_schema(_default_stems())
    assert guess is not None
    assert guess.sampled == len(_default_stems())
    assert guess.matched == guess.sampled
    assert guess.misses == ()


def test_a_name_that_splits_right_but_fails_a_slot_is_not_counted_as_parsed():
    """Excludes the report that counts token-count agreement and stops there.

    ``ZZ9`` is not a well label, but ``exp_GFP_ZZ9_f0_t0`` splits into exactly
    the five tokens the schema wants. A report built on ``len(parts) == k``
    alone calls it parsed, and the count then says nothing the schema string did
    not already say.
    """
    stems = _default_stems() + ["exp_GFP_ZZ9_f0_t0"]
    guess = infer_schema(stems)
    assert guess is not None
    assert guess.fields[2] == "well"
    assert guess.matched == len(stems) - 1
    assert guess.misses == (
        ("exp_GFP_ZZ9_f0_t0", "position 3 is 'ZZ9', not a well"),)


def test_a_bare_number_fov_slot_is_checked_with_the_rule_that_claimed_it():
    """Excludes the report that checks ``_is_fov`` on every slot named ``fov``.

    ``_classify_slots`` has a last pass that claims a leftover all-numeric
    column as the FOV slot — a slot ``_is_fov`` itself rejects, since it accepts
    only ``f<n>``. A report that looks the field name up in a table of
    predicates instead of asking which predicate claimed the slot calls **every
    name on such a plate** unparsed, and reports 0 of N on a plate that is
    entirely well-formed.
    """
    stems = [f"exp_{c}_{w}_{n}_t0"
             for w in ("A01", "A02") for n in ("1", "2") for c in ("DAPI", "GFP")]
    guess = infer_schema(stems)
    assert guess is not None
    assert guess.fields == ["experiment", "channel", "well", "fov", "timepoint"]
    assert guess.matched == len(stems), guess.summary()
    assert guess.misses == ()


def test_misses_are_capped_and_the_remainder_is_counted_not_dropped():
    """Excludes the report that quotes examples and lets the rest vanish."""
    good = _default_stems(wells=("A01", "A02", "A03", "B01", "B02", "B03"))
    stems = good + [f"junk{i}" for i in range(10)]
    guess = infer_schema(stems)
    assert guess is not None
    assert len(guess.misses) == 3
    assert guess.sampled - guess.matched == 10
    assert "7 more" in guess.shortfall()


def test_a_corpus_whose_majority_does_not_split_is_refused_outright():
    """The modal token count decides the layout, so a corpus where the
    malformed names outnumber the well-formed ones infers nothing at all —
    ``infer_schema`` returns ``None`` rather than describing the minority.

    Pinned because it is the boundary either side of the test above, and
    because a caller that renders a summary has to handle the ``None`` before
    it can render anything. Found by writing the capping test with ten junk
    names against eight good ones and watching the whole inference refuse.
    """
    stems = _default_stems() + [f"junk{i}" for i in range(10)]
    assert len(stems) - 8 > 8, "the junk must be the majority for this to bite"
    assert infer_schema(stems) is None


def test_summary_states_the_schema_the_separator_and_both_counts():
    """Excludes the summary that reads well and says nothing checkable."""
    stems = _default_stems() + ["exp_GFP_ZZ9_f0_t0"]
    guess = infer_schema(stems)
    assert "experiment:channel:well:fov:timepoint" in guess.summary()
    assert "'_'" in guess.summary()
    assert f"{len(stems) - 1} of {len(stems)}" in guess.summary()
    # The filename belongs to the shortfall, which is styled to be read — not
    # to the neutral summary, which is styled to be skimmed.
    assert "ZZ9" not in guess.summary()
    assert "ZZ9" in guess.shortfall()
    assert "will be skipped" in guess.shortfall()


def test_summary_on_a_clean_corpus_says_all_of_them_and_has_no_shortfall():
    guess = infer_schema(_default_stems())
    assert f"All {len(_default_stems())} filenames sampled matched" in guess.summary()
    assert guess.shortfall() == ""


def test_the_readout_says_matched_rather_than_parsed():
    """One word per concept. The Zip Loose Folder page has always said the
    files that don't fit "didn't match the schema"; "parse" was a second
    vocabulary for the same event, and a programmer's one at that."""
    guess = infer_schema(_default_stems() + ["exp_GFP_ZZ9_f0_t0"])
    text = guess.summary() + " " + guess.shortfall()
    assert "parse" not in text.lower()
    assert "slot" not in text.lower(), "the control's own word is 'position'"
    assert "Matched" in guess.summary()
    assert "position 3" in guess.shortfall()


def test_the_count_survives_the_round_trip_from_a_real_folder(tmp_path):
    """The counts must come back from ``infer_from_dir``, not only from the
    pure entry point — that is the one the Analyze form calls."""
    src = tmp_path / "in"
    src.mkdir()
    for stem in _default_stems():
        (src / f"{stem}.tif").write_bytes(b"")
    (src / "exp_GFP_ZZ9_f0_t0.tif").write_bytes(b"")
    # The mess real folders carry, none of which is an input image.
    (src / ".DS_Store").write_bytes(b"")
    (src / "._exp_GFP_A01_f0_t0.tif").write_bytes(b"")
    (src / "notes.txt").write_text("")
    guess = infer_from_dir(src)
    assert guess is not None
    assert guess.sampled == len(_default_stems()) + 1
    assert guess.matched == len(_default_stems())
