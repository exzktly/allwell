"""Client-side incremental split for remote runs.

``_filter_new_fovs_in_staging`` is the "split" half of the remote incremental
design: the client rewrites each staged well zip to upload only the new-FOV
members and drops wells whose local output already covers every input FOV, so
the remote does pure compute on genuinely-new files and the partial outputs are
merged back locally on fetch. Stdlib-only, so it runs without the imaging stack.
"""

import csv
import zipfile
from pathlib import Path

from services.remote_runner import _filter_new_fovs_in_staging

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _zip(path: Path, members: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in members:
            zf.writestr(name, b"data-" + name.encode())


def _prior_csv(path: Path, fov_tp_pairs: list[tuple[str, str]]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["well", "fov", "timepoint", "nucleus_id"])
        w.writeheader()
        for i, (fov, tp) in enumerate(fov_tp_pairs, 1):
            w.writerow({"well": path.stem.split("_")[-1], "fov": fov,
                        "timepoint": tp, "nucleus_id": i})


def test_split_keeps_only_new_fovs_and_drops_uptodate(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    # A01: prior CSV has FOV 1; the input zip has FOV 1 (done) + FOV 2 (new).
    _zip(staging / "A01.zip", ["e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"])
    _prior_csv(out / "measurements_A01.csv", [("1", "T01")])

    # B02: prior CSV already covers its only FOV → up to date, drop from upload.
    _zip(staging / "B02.zip", ["e_w1_B02_1_T01.tif"])
    _prior_csv(out / "measurements_B02.csv", [("1", "T01")])

    # C03: no prior CSV → new well, upload in full.
    _zip(staging / "C03.zip", ["e_w1_C03_1_T01.tif", "e_w1_C03_2_T01.tif"])

    well_zips = tuple(sorted(staging.glob("*.zip")))
    survivors, up_to_date = _filter_new_fovs_in_staging(
        well_zips,
        prior_output_dir=out,
        csv_prefix="measurements",
        filename_schema=SCHEMA,
        filename_sep=SEP,
    )

    surviving_names = {p.name for p in survivors}
    assert surviving_names == {"A01.zip", "C03.zip"}
    assert up_to_date == ("B02",)
    assert not (staging / "B02.zip").exists()          # dropped from upload

    # A01 rewritten to only the new FOV-2 member.
    assert set(zipfile.ZipFile(staging / "A01.zip").namelist()) == {"e_w1_A01_2_T01.tif"}
    # C03 (new well) uploaded in full.
    assert set(zipfile.ZipFile(staging / "C03.zip").namelist()) == {
        "e_w1_C03_1_T01.tif", "e_w1_C03_2_T01.tif"}


def test_split_recovers_done_set_from_out_zip_when_csv_missing(tmp_path):
    # "out-zips but no CSV": the well's local output archive exists (members keep
    # the input stem), but the sibling CSV is gone. The split must recover the
    # done FOV-groups from the archive so it uploads only the new FOV — never the
    # whole well for a full remote re-segmentation.
    staging = tmp_path / "stage"
    staging.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    _zip(staging / "A01.zip", ["e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"])
    # No measurements_A01.csv — only the processed output archive.
    _zip(out / "A01_out.zip", [
        "e_w1_A01_1_T01_overlay.png", "e_w1_A01_1_T01_labels.tif"])

    survivors, up_to_date = _filter_new_fovs_in_staging(
        tuple(staging.glob("*.zip")),
        prior_output_dir=out, csv_prefix="measurements",
        filename_schema=SCHEMA, filename_sep=SEP)

    assert {p.name for p in survivors} == {"A01.zip"}
    assert up_to_date == ()
    # Only the genuinely-new FOV-2 member survives the upload split.
    assert set(zipfile.ZipFile(staging / "A01.zip").namelist()) == {"e_w1_A01_2_T01.tif"}


def test_split_no_prior_outputs_is_passthrough(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    _zip(staging / "A01.zip", ["e_w1_A01_1_T01.tif"])
    well_zips = tuple(staging.glob("*.zip"))

    survivors, up_to_date = _filter_new_fovs_in_staging(
        well_zips, prior_output_dir=out, csv_prefix="measurements",
        filename_schema=SCHEMA, filename_sep=SEP)

    assert {p.name for p in survivors} == {"A01.zip"}
    assert up_to_date == ()
    assert set(zipfile.ZipFile(staging / "A01.zip").namelist()) == {"e_w1_A01_1_T01.tif"}
