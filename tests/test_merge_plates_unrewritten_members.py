"""A remapped well's images must arrive labelled for the well they arrived in.

Merge Plates rewrites the ``well`` token inside every member name of a remapped
archive through ``rewrite_member_field``, which returns ``(new_name,
was_rewritten)``. The caller took ``[0]`` and discarded the flag, so a member
that failed the structural gate — one separator-delimited field too many, an
extension the role catalog does not know, a well token that is not the source
well — landed in the *destination* well's archive still spelled with dataset
2's well, and the merge reported success. The CSV row for it went unrewritten in
exactly the same way, so the ``dest_in_names`` cross-check compared two
consistent wrong answers and passed.

The other half of the rule is which members the question may be asked of at all:
a ``.DS_Store``, an AppleDouble ``._`` sidecar and a ``readme.txt`` a user put
in on purpose all fail the rewrite gate too, carry no well token, and must pass
through untouched. A predicate asked outside its domain makes a whole archive
look unrewritable, so every fixture here carries that debris.

Qt-free, fast lane.
"""

from __future__ import annotations

import csv
import zipfile

import pytest

from datasets.merge_plates.apply import execute_plan
from datasets.merge_plates.merge_plates_compute import (
    MergePlatesError,
    build_plate_merge_plan,
)
from tests.fixtures.dataset_builder import ROW_COLUMNS, build_dataset


def _two_plates(tmp_path, **kwargs):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], **kwargs)
    build_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"], **kwargs)
    return ds1, ds2, dest


def _append_row(csv_path, filename):
    rows = list(csv.DictReader(csv_path.open(newline="")))
    extra = dict(rows[0])
    extra["filename"] = filename
    extra["nucleus_id"] = "77"
    rows.append(extra)
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize("stray_name", [
    "exp_GFP_A01_F001_24h_rescan.tif",   # one field too many for the schema
    "exp_GFP_D04_F001_24h.tif",          # a well token that is not the source well
])
def test_an_image_whose_well_token_will_not_move_refuses_the_merge(tmp_path, stray_name):
    ds1, ds2, dest = _two_plates(tmp_path, junk=True, stray=True)
    with zipfile.ZipFile(ds2 / "in" / "A01.zip", "a") as zf:
        zf.writestr(stray_name, b"img")
    _append_row(ds2 / "out" / "measurements_A01.csv", stray_name)

    plan = build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "B02"})
    with pytest.raises(MergePlatesError) as excinfo:
        execute_plan(plan)

    message = str(excinfo.value)
    assert stray_name in message
    assert "B02" in message and "A01" in message
    assert "still labelled for another well" in message


def test_the_csv_alone_refuses_it_too_when_the_well_has_no_raw_input(tmp_path):
    """The one case the archive-side refusal cannot cover: a well with no
    ``in/<WELL>.zip`` at all, so nothing walks its members and only the
    measurements CSV carries the filename."""
    ds1, ds2, dest = _two_plates(tmp_path)
    (ds2 / "in" / "A01.zip").unlink()
    _append_row(ds2 / "out" / "measurements_A01.csv", "exp_GFP_A01_F001_24h_rescan.tif")

    plan = build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "B02"})
    with pytest.raises(MergePlatesError, match="exp_GFP_A01_F001_24h_rescan.tif"):
        execute_plan(plan)


def test_os_debris_and_a_stray_text_file_still_pass_through_a_remap(tmp_path):
    """The entitlement half of the rule. None of these carries a well token, so
    none of them is evidence that the remap failed — refusing on them would
    make every archive a real user has ever opened in Finder unmergeable."""
    ds1, ds2, dest = _two_plates(tmp_path, junk=True, stray=True)

    plan = build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "B02"})
    assert "2 well(s)" in execute_plan(plan)

    members = set(zipfile.ZipFile(dest / "in" / "B02.zip").namelist())
    assert ".DS_Store" in members
    assert "__MACOSX/._sidecar" in members
    assert "readme.txt" in members
    # And every real image did move.
    assert {name for name in members if name.endswith(".tif")} == {
        "exp_GFP_B02_F001_0h.tif", "exp_GFP_B02_F001_24h.tif",
    }
    assert not [name for name in members if "_A01_" in name]
