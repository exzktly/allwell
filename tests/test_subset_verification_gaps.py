"""Subset must not report success on a slice it could not actually produce.

Two ways it did. Both end in the same artifact — a destination whose
``out/measurements_<WELL>.csv`` names images that are nowhere in its ``in/`` —
and in both the run said "Subset written to …".

* The "did anything land?" cross-check ran only when the filtered
  ``in/<WELL>.zip`` had kept *something*. An empty result was read as "no
  container to compare against", so the one archive that certainly cannot hold
  a row's image was the one archive whose rows went unchecked.
* ``row_matches`` read the ``timepoint_hours`` **column** while
  ``member_matches`` read the **filename's** timepoint token. A CSV that leaves
  the numeric column blank — the shape an older pipeline wrote — failed every
  row against a filter every image passed, and the verification error then sent
  the user off to fix a filter that was never wrong.

Qt-free, fast lane. The fixtures carry the debris a real archive carries
(``.DS_Store``, a directory entry, an AppleDouble sidecar, a stray text file):
an unparseable member is *kept* by the member filter, so a corpus of nothing but
well-formed TIFs cannot produce the empty-archive case at all.
"""

from __future__ import annotations

import csv
import zipfile

import pytest

from datasets.subset.apply import execute_plan
from datasets.subset.subset_compute import (
    SubsetError,
    SubsetFilter,
    build_subset_plan,
    member_matches,
    row_matches,
)
from tests.fixtures.dataset_builder import ROW_COLUMNS, build_dataset


def _rewrite_csv(path, mutate):
    rows = list(csv.DictReader(path.open(newline="")))
    for row in rows:
        mutate(row)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _drop_members(zip_path, substring):
    keep = {
        name: zipfile.ZipFile(zip_path).read(name)
        for name in zipfile.ZipFile(zip_path).namelist()
        if substring not in name and not name.endswith("/")
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in keep.items():
            zf.writestr(name, data)


# ── item 18: an empty filtered archive is not "nothing to compare against" ───
def test_a_row_is_checked_even_when_the_filter_emptied_the_well_s_archive(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"], fovs=("F001",))
    # A01's raw input lost its 24h image — a partial upload, or a user tidying
    # up in Finder. Everything else about the dataset still says the row exists.
    _drop_members(src / "in" / "A01.zip", "_24h")

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    with pytest.raises(SubsetError) as excinfo:
        execute_plan(plan)

    message = str(excinfo.value)
    assert "exp_GFP_A01_F001_24h.tif" in message
    # And it says *why* there is nothing to point into, rather than implying an
    # archive exists that merely lacks the file.
    assert "kept no member of that archive at all" in message


def test_a_well_whose_archive_kept_members_is_still_checked(tmp_path):
    """The other side of the same predicate: a non-empty archive that really is
    missing the row's file must still fail, exactly as it did before."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001", "F002"))
    _drop_members(src / "in" / "A01.zip", "F001_24h")

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    with pytest.raises(SubsetError, match="exp_GFP_A01_F001_24h.tif"):
        execute_plan(plan)


def test_a_well_with_no_raw_input_container_at_all_is_still_copied(tmp_path):
    """The case the sentinel exists to preserve: no ``in/<WELL>.zip`` means
    there is genuinely nothing to check a row against, and that is not an
    error."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    (src / "in" / "A01.zip").unlink()

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    assert "1 well(s)" in execute_plan(plan)
    assert (dest / "out" / "measurements_A01.csv").exists()


# ── item 69: the row predicate and the member predicate read one answer ──────
def test_a_legacy_csv_with_no_timepoint_hours_column_still_subsets(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(
        src, wells=["A01"], timepoints=["0h", "24h", "48h"], fovs=("F001", "F002"),
        junk=True, stray=True,
    )
    _rewrite_csv(src / "out" / "measurements_A01.csv",
                 lambda row: row.__setitem__("timepoint_hours", ""))

    filt = SubsetFilter(timepoint_labels=frozenset({"24h", "48h"}))
    plan = build_subset_plan(src, dest, filt)

    # The two predicates agree about one image before the run is even started.
    assert member_matches(
        "exp_GFP_A01_F001_24h.tif", schema=plan.schema, channels=plan.channels, filt=filt,
    )
    assert row_matches({"fov": "F001", "timepoint": "24h", "timepoint_hours": ""}, filt)

    execute_plan(plan)
    kept = list(csv.DictReader((dest / "out" / "measurements_A01.csv").open(newline="")))
    assert len(kept) == 4                                  # 2 FOVs × 2 timepoints
    assert {row["timepoint"] for row in kept} == {"24h", "48h"}


def test_the_filename_column_is_the_last_resort_for_a_row_s_timepoint(tmp_path):
    """Both timepoint columns blank: the row is resolved through the very token
    ``member_matches`` reads, so the two cannot disagree."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))

    def _blank_both(row):
        row["timepoint_hours"] = ""
        row["timepoint"] = ""

    _rewrite_csv(src / "out" / "measurements_A01.csv", _blank_both)

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    execute_plan(plan)
    kept = list(csv.DictReader((dest / "out" / "measurements_A01.csv").open(newline="")))
    assert [row["filename"] for row in kept] == ["exp_GFP_A01_F001_24h.tif"]


def test_rows_that_state_no_timepoint_at_all_are_named_as_the_cause(tmp_path):
    """"Not computed" is not "computed and rejected". A CSV that states no
    timepoint anywhere the schema can find one is a broken CSV, and the
    verification message has to say that rather than send the user back to the
    filter — which is what a single "check that the filter matches" said for
    every one of the three ways zero rows happens.

    Asserted through the message the user actually sees, not through the
    predicate's return value, so the test measures the artifact rather than the
    derivation behind it.
    """
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))

    def _blank_everything(row):
        row["timepoint_hours"] = ""
        row["timepoint"] = ""
        row["filename"] = "not-a-schema-name.tif"

    _rewrite_csv(src / "out" / "measurements_A01.csv", _blank_everything)

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    with pytest.raises(SubsetError) as excinfo:
        execute_plan(plan)
    message = str(excinfo.value)
    assert "could not be tested against the filter at all" in message
    assert "check that the filter actually" not in message


def test_zero_rows_beside_matching_images_does_not_blame_the_filter(tmp_path):
    """The verification message names the disagreement it can see. Images that
    matched the same filter in their dozens mean the filter matches data fine;
    what disagrees is the CSV."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    # Every row says 999h; every image is still labelled 0h/24h.
    _rewrite_csv(src / "out" / "measurements_A01.csv", lambda row: (
        row.__setitem__("timepoint", "999h"), row.__setitem__("timepoint_hours", "999.0"),
    ))

    plan = build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"})))
    with pytest.raises(SubsetError) as excinfo:
        execute_plan(plan)
    message = str(excinfo.value)
    assert "image(s) matched the same filter" in message
    assert "check that the filter actually" not in message
