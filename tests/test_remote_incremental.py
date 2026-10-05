"""Client-side incremental split for remote runs.

NOTE: ``_filter_new_fovs_in_staging`` is no longer what a real run uses —
``_plan_staging`` is (see its docstring). These tests still pin the older
shape's behaviour; a change to the incremental split has to land in
``_plan_staging`` as well, or it will not reach a run at all.

``_filter_new_fovs_in_staging`` is the "split" half of the remote incremental
design: the client rewrites each staged well zip to upload only the new-FOV
members and drops wells whose local output already covers every input FOV, so
the remote does pure compute on genuinely-new files and the partial outputs are
merged back locally on fetch. Stdlib-only, so it runs without the imaging stack.
"""

import csv
import types
import zipfile
from pathlib import Path

import json

from services.remote_staging import _filter_new_fovs_in_staging
from services.remote_fetch import refresh_pipeline_info_available_from_outputs

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


def _out_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name in members:
            zf.writestr(name, b"x")


def test_refresh_pipeline_info_adds_missing_timepoints_from_outputs(tmp_path):
    # The fetched sidecar from a remote incremental run only lists the timepoints
    # the remote saw (the uploaded new-FOV split). The merged local out-zips hold
    # the complete picture; the refresh must union them into available_timepoints
    # / available_fovs so dropdowns aren't stale.
    out = tmp_path / "out"
    out.mkdir()
    # Sidecar knows only the first-half timepoints / one FOV.
    (out / "pipeline_info.json").write_text(json.dumps({
        "schema": SCHEMA, "separator": SEP,
        "available_timepoints": ["00d00h00m", "00d08h00m"],
        "available_fovs": ["1"],
        "cell_gating": {"thresh_frac_on": {"gfp": 0.25}},   # must be preserved
    }))
    # Merged outputs carry old + the new 48h timepoint, and a second FOV.
    _out_zip(out / "A01_out.zip", [
        "exp_NIR_A01_1_00d00h00m_labels.tif",
        "exp_NIR_A01_1_00d08h00m_labels.tif",
        "exp_NIR_A01_2_02d00h00m_overlay.png",
    ])

    changed = refresh_pipeline_info_available_from_outputs(out)
    assert changed is True

    info = json.loads((out / "pipeline_info.json").read_text())
    # New 48h timepoint + new FOV are now present, sorted by real hours.
    assert info["available_timepoints"] == ["00d00h00m", "00d08h00m", "02d00h00m"]
    assert info["available_fovs"] == ["1", "2"]
    # Curation untouched.
    assert info["cell_gating"] == {"thresh_frac_on": {"gfp": 0.25}}

    # Idempotent: a second pass finds nothing to add.
    assert refresh_pipeline_info_available_from_outputs(out) is False


def test_refresh_pipeline_info_noop_without_sidecar(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert refresh_pipeline_info_available_from_outputs(out) is False


def test_plan_staging_uploads_a_forced_well_in_full(tmp_path):
    """The live staging path must honour the per-well force list.

    Validate flags a well with images and no measurements CSV, and the
    completion handoff asks for it by name. On a REMOTE run the client-side
    split gets there first: it computes its own done-set (CSV rows unioned with
    the local out-zip's members), finds this well covered, drops it before a
    single byte is uploaded, and logs "up to date (local) — not uploaded". The
    remote then never sees the well, so ``--force_wells`` on the remote command
    line has nothing to act on — the one well that most needs reprocessing is
    the one guaranteed not to be.
    """
    from services.remote_staging import _plan_staging

    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    members = [f"exp_GFP_C08_F001_{tp}.tif" for tp in ("0h", "24h")]
    wz = in_dir / "C08.zip"
    with zipfile.ZipFile(wz, "w") as zf:
        for m in members:
            zf.writestr(m, b"img")
    # The well's local output already covers every input FOV — which is exactly
    # why the split would otherwise skip it.
    with zipfile.ZipFile(out_dir / "C08_out.zip", "w") as zf:
        for m in members:
            zf.writestr(m, b"mask")

    common = dict(
        keep_tokens=None, incremental=True, prior_output_dir=out_dir,
        csv_prefix="measurements",
        filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
    )
    plans, up_to_date, _zero = _plan_staging((wz,), **common)
    assert plans == [] and up_to_date == ["C08"]        # the reported behaviour

    plans, up_to_date, _zero = _plan_staging(
        (wz,), **common, force_wells=frozenset({"C08"}))
    assert up_to_date == []
    assert len(plans) == 1
    # keep=None means "upload this archive as it is" — the whole well.
    assert plans[0].keep is None


def test_plan_staging_force_list_matches_wells_canonically(tmp_path):
    """``C8`` typed in the GUI and ``C08.zip`` on disk are one well."""
    from services.remote_staging import _plan_staging
    from pipeline.well_skip import _canonical_force_wells

    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    wz = in_dir / "C08.zip"
    with zipfile.ZipFile(wz, "w") as zf:
        zf.writestr("exp_GFP_C08_F001_0h.tif", b"img")
    with zipfile.ZipFile(out_dir / "C08_out.zip", "w") as zf:
        zf.writestr("exp_GFP_C08_F001_0h.tif", b"mask")

    plans, up_to_date, _zero = _plan_staging(
        (wz,), keep_tokens=None, incremental=True, prior_output_dir=out_dir,
        csv_prefix="measurements",
        filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
        force_wells=_canonical_force_wells(["c8"]))
    assert up_to_date == []
    assert len(plans) == 1


def test_fetch_skips_a_well_this_run_did_not_produce(tmp_path):
    """A well the remote skipped (up to date, or never uploaded by the split)
    emits no ``Well X — done.`` marker, so this run produced nothing new for
    it. If its local artefacts are already complete there is nothing on the
    remote a fetch could improve — and pulling it anyway costs an ssh size
    probe plus two retry-wrapped ``rsync --timeout=120`` transfers, which is
    the shape behind a fetch sitting on one well for minutes while the file it
    is pulling is already on disk."""
    from services.remote_runner import OutputFilter
    from services.remote_fetch import Fetcher

    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(out / "C08_out.zip", "w") as zf:
        zf.writestr("exp_GFP_C08_F001_0h.tif", b"mask")
    (out / "measurements_C08.csv").write_text("well\nC08\n")

    run = types.SimpleNamespace(
        expected_wells=set(),               # no "done" marker for C08
        fetched_wells=set(),
        local_output_dir=out,
        output_filter=OutputFilter.FULL,
        # Nothing was asked to be reprocessed — a forced well is never skipped.
        force=False, force_wells=frozenset(), skipped_wells={},
    )
    fetcher = object.__new__(Fetcher)
    fetcher.run = run

    assert Fetcher._already_local(fetcher, "C08") is not None

    # A well this run *did* process is always re-fetched, however complete the
    # local copy looks: the local artefact is what the new output replaces.
    run.expected_wells.add("C08")
    assert Fetcher._already_local(fetcher, "C08") is None


def test_fetch_does_not_skip_an_incomplete_local_well(tmp_path):
    """The skip is narrow on purpose — a missing CSV, or a zip that is not a
    complete archive, still fetches."""
    from services.remote_runner import OutputFilter
    from services.remote_fetch import Fetcher

    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(out / "C08_out.zip", "w") as zf:
        zf.writestr("exp_GFP_C08_F001_0h.tif", b"mask")

    run = types.SimpleNamespace(
        expected_wells=set(), fetched_wells=set(),
        local_output_dir=out, output_filter=OutputFilter.FULL,
        force=False, force_wells=frozenset(), skipped_wells={})
    fetcher = object.__new__(Fetcher)
    fetcher.run = run
    assert Fetcher._already_local(fetcher, "C08") is None      # no CSV

    (out / "measurements_C08.csv").write_text("well\nC08\n")
    assert Fetcher._already_local(fetcher, "C08") is not None

    (out / "C08_out.zip").write_bytes(b"not a zip")            # truncated
    assert Fetcher._already_local(fetcher, "C08") is None


def _sidecar(path, wells: dict) -> None:
    path.write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint",
        "run_report": {"wells": wells, "incomplete_wells": []},
    }))


def test_fetched_run_report_does_not_erase_earlier_wells(tmp_path):
    """A remote output dir starts empty, so the sidecar it writes describes
    only the wells THAT run processed. Pulling it over the local one replaced a
    whole plate's completeness records with a handful — and every skipped well
    lost the record saying whether its empty output was a measurement or a gap,
    which is exactly what Validate reads to avoid forcing a well that
    legitimately segments zero cells."""
    from pipeline.run_report import merge_fetched_run_report, read_run_report_block

    info = tmp_path / "pipeline_info.json"
    _sidecar(info, {
        "A01": {"well": "A01", "groups_total": 4, "groups_covered": 4, "failed": []},
        "C08": {"well": "C08", "groups_total": 4, "groups_covered": 0, "failed": []},
    })
    prior = read_run_report_block(info)
    assert set(prior["wells"]) == {"A01", "C08"}

    # The fetch overwrites it with this run's much smaller block.
    _sidecar(info, {
        "C08": {"well": "C08", "groups_total": 4, "groups_covered": 3, "failed": ["x.tif"]},
    })
    assert merge_fetched_run_report(info, prior) is True

    merged = json.loads(info.read_text())["run_report"]
    assert set(merged["wells"]) == {"A01", "C08"}          # A01 survived
    assert merged["wells"]["C08"]["groups_covered"] == 3   # this run's wins
    assert merged["wells"]["C08"]["failed"] == ["x.tif"]
    # Derived fields are recomputed, never merged, so they cannot disagree
    # with the wells map they summarise.
    assert merged["incomplete_wells"] == ["C08"]
    assert merged["groups_total"] == 8
    assert merged["groups_covered"] == 7


def test_merging_a_run_report_is_a_no_op_when_there_is_nothing_to_restore(tmp_path):
    from pipeline.run_report import merge_fetched_run_report, read_run_report_block

    info = tmp_path / "pipeline_info.json"
    _sidecar(info, {"A01": {"well": "A01", "groups_total": 1, "groups_covered": 1}})
    # No prior block (first run into this folder) — nothing to fold back.
    assert merge_fetched_run_report(info, None) is False
    # Prior block identical to the fetched one — no rewrite.
    assert merge_fetched_run_report(info, read_run_report_block(info)) is False
    assert read_run_report_block(tmp_path / "absent.json") is None


def _quality_sidecar(path, wells: dict) -> None:
    path.write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint",
        "field_quality": {"wells": wells, "partial_wells": []},
    }))


def _well(label, *, measured, of, focus):
    return {"well": label, "measured": measured, "measured_of": of,
            "fields": [{"fov": str(i), "focus": focus, "sat_frac": 0.0}
                       for i in range(measured)]}


def test_fetched_field_quality_does_not_erase_earlier_wells(tmp_path):
    """The same defect as the run report, one block over, with a worse result.

    Losing coverage records loses history. Losing field-quality records
    produces a **wrong number**: the acquisition metrics are scored as robust
    z against the plate, so a block silently reduced to the wells one remote
    incremental run touched gives every one of them a z computed against its
    own tiny median. The shortfall accounting cannot catch it either — each of
    those wells is individually complete, so nothing is marked partial and the
    scorer proceeds.
    """
    from pipeline.field_quality import (
        merge_fetched_field_quality, read_field_quality_block,
    )

    info = tmp_path / "pipeline_info.json"
    _quality_sidecar(info, {
        "A01": _well("A01", measured=4, of=4, focus=27.4),
        "C08": _well("C08", measured=4, of=4, focus=11.0),
    })
    prior = read_field_quality_block(info)
    assert set(prior["wells"]) == {"A01", "C08"}

    # The fetch overwrites it with this run's much smaller block, in which
    # C08 was only partly re-measured.
    _quality_sidecar(info, {"C08": _well("C08", measured=2, of=4, focus=26.9)})
    assert merge_fetched_field_quality(info, prior) is True

    merged = json.loads(info.read_text())["field_quality"]
    assert set(merged["wells"]) == {"A01", "C08"}            # A01 survived
    assert merged["wells"]["C08"]["measured"] == 2           # this run's wins
    assert merged["wells"]["C08"]["fields"][0]["focus"] == 26.9
    # Derived fields recomputed from the merged map, never merged themselves,
    # so they cannot disagree with the wells they summarise — and the partial
    # well the fetch introduced is named rather than lost.
    assert merged["partial_wells"] == ["C08"]
    assert merged["measured"] == 6
    assert merged["measured_of"] == 8


def test_merging_field_quality_is_a_no_op_when_there_is_nothing_to_restore(tmp_path):
    from pipeline.field_quality import (
        merge_fetched_field_quality, read_field_quality_block,
    )

    info = tmp_path / "pipeline_info.json"
    _quality_sidecar(info, {"A01": _well("A01", measured=1, of=1, focus=27.0)})
    # No prior block (first run into this folder) — nothing to fold back.
    assert merge_fetched_field_quality(info, None) is False
    # Prior block identical to the fetched one — no rewrite.
    assert merge_fetched_field_quality(info, read_field_quality_block(info)) is False
    assert read_field_quality_block(tmp_path / "absent.json") is None


def test_the_fetch_captures_the_field_quality_block_before_it_is_overwritten():
    """Excludes the merge that is written and never called.

    Both halves have to be at the fetch site and in the right order: read the
    local block BEFORE the rsync that overwrites the sidecar, merge AFTER it.
    A merge function nothing calls, or one called with a block captured after
    the overwrite, is the branch-that-exists defect (CLAUDE.md §7.7) — and it
    would look exactly like this one working.
    """
    import inspect
    from services.remote_fetch import FetchedOutputVerifier

    src = inspect.getsource(FetchedOutputVerifier.verify)
    read_at = src.index("read_field_quality_block")
    rsync_at = src.index("_rsync_optional")
    merge_at = src.index("merge_fetched_field_quality")
    assert read_at < rsync_at < merge_at, (
        "the local field-quality block must be captured before the sidecar is "
        "pulled over it, and merged back after")


def test_every_layout_takes_the_incremental_split(tmp_path):
    """A resumed plate that is not already one zip per well is split too.

    Only the ZIP layout used to apply the split. A MIXED or FOLDER plate — a
    local folder-mode run, or a staging interrupted part way through its
    zip-up — came back with ``incremental`` unset, so everything the remote side
    keys on it was skipped: the run re-attached to the suspended run's
    directory and could find its partial wells "complete" there, and batched
    mode skipped every well with a local CSV, the partial ones included.
    """
    from services.remote_runner import LocalInputMode, stage_local_zips

    out = tmp_path / "out"
    out.mkdir()
    _prior_csv(out / "measurements_A01.csv", [("1", "T01")])     # partial
    _prior_csv(out / "measurements_B02.csv", [("1", "T01")])     # complete

    for layout in ("mixed", "folder"):
        plate = tmp_path / layout
        plate.mkdir()
        if layout == "mixed":
            _zip(plate / "A01.zip", ["e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"])
        else:
            (plate / "A01").mkdir()
            for name in ("e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"):
                (plate / "A01" / name).write_bytes(b"data")
        (plate / "B02").mkdir()
        (plate / "B02" / "e_w1_B02_1_T01.tif").write_bytes(b"data")

        zipped = stage_local_zips(
            plate, filename_schema=SCHEMA, filename_sep=SEP, incremental=True,
            prior_output_dir=out)
        try:
            assert zipped.mode is LocalInputMode[layout.upper()]
            assert zipped.incremental, f"{layout}: the resume was not split"
            assert zipped.up_to_date_wells == ("B02",), layout
            (a01,) = zipped.well_zips
            assert set(zipfile.ZipFile(a01).namelist()) == {"e_w1_A01_2_T01.tif"}, (
                f"{layout}: the partial well was uploaded whole")
        finally:
            if zipped.cleanup is not None:
                zipped.cleanup()
