"""The completeness record is an inventory of the dataset, not a log of the run.

This is the hazard that decides where the ``run_check`` block gets its list of
wells from, and it is easy to get wrong because there is already a per-well
record in the sidecar that looks like it would do.

It would not. ``pipeline/run_report.py``'s block is cleared at the start of
every run, is merged back wholesale rather than per well, and is only written by
wells a run actually *processed* — an incremental run's "up to date (no new
FOVs)" path returns before recording anything. So after adding one timepoint to
a 96-well plate, ``run_report.wells`` holds the handful of wells that had new
data and nothing else. A completeness record built on it would report a finished
plate as three wells, and every surface reading that record would agree with it.

So ``scan_run`` reads the artefacts. These tests hold it to that: whatever the
run report says, and whatever this invocation touched, the record describes the
whole dataset.
"""

from __future__ import annotations

import contextlib
import json
import zipfile
from pathlib import Path

from pipeline.completeness import scan_run
from pipeline.run_report import (
    collect_run_report,
    finalize_run_report,
    write_well_report,
)

SCHEMA = ["exp", "channel", "well", "fov", "timepoint"]
SEP = "_"
PREFIX = "measurements"
FOVS = ("F001", "F002")
TPS = ("0", "24h")


def _build_plate(root: Path, wells):
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    containers = {}
    for well in wells:
        names = [f"exp1_DAPI_{well}_{f}_{t}.tif" for f in FOVS for t in TPS]
        containers[well] = in_dir / f"{well}.zip"
        for path in (containers[well], out_dir / f"{well}_out.zip"):
            with zipfile.ZipFile(path, "w") as zf:
                for name in names:
                    zf.writestr(name, b"x")
        rows = "\n".join(f"{f},{t},1" for f in FOVS for t in TPS)
        (out_dir / f"{PREFIX}_{well}.csv").write_text(
            "fov,timepoint,nucleus_id\n" + rows + "\n")
    return containers, out_dir


def _scan(containers, out_dir):
    return scan_run(
        in_containers=containers, output_dir=out_dir, schema=SCHEMA, sep=SEP,
        csv_prefix=PREFIX, fov_field_present=True)


def test_the_record_covers_wells_the_run_report_never_mentions(tmp_path):
    """The shape an incremental re-run leaves behind.

    Three complete wells on disk; a run report naming one, because that is the
    only well this invocation had new data for. The completeness record must
    still describe all three — and must not inherit the run report's silence
    about the other two as evidence that they are empty.
    """
    wells = ["A01", "A02", "A03"]
    containers, out_dir = _build_plate(tmp_path, wells)

    write_well_report(out_dir, "A02", groups_total=4, groups_covered=4)
    info_path = out_dir / "pipeline_info.json"
    info_path.write_text(json.dumps({"schema": ":".join(SCHEMA)}))
    finalize_run_report(
        out_dir, info_path, atomic_writer=_plain_writer, log=_SilentLog())
    run_report = json.loads(info_path.read_text())["run_report"]
    assert set(run_report["wells"]) == {"A02"}, "premise: the run report is partial"

    block = _scan(containers, out_dir)
    assert set(block["wells"]) == set(wells)
    assert block["totals"]["complete"] == 3
    assert block["totals"]["wells"] == 3


def test_the_scan_never_reads_the_run_report_at_all(tmp_path):
    """Not merely "produces the same answer" — it must not consult it.

    A run report claiming a well failed everything, sitting beside output that
    covers everything, has to lose. The artefacts are the ground truth; the run
    report describes one invocation's attempt at them.
    """
    containers, out_dir = _build_plate(tmp_path, ["A01"])
    (out_dir / "pipeline_info.json").write_text(json.dumps({
        "run_report": {
            "wells": {"A01": {"well": "A01", "groups_total": 4,
                              "groups_covered": 0, "failed": ["everything"],
                              "aborted_reason": "disk full"}},
            "incomplete_wells": ["A01"], "groups_total": 4, "groups_covered": 0,
        },
    }))

    rec = _scan(containers, out_dir)["wells"]["A01"]
    assert rec["health"] == "complete"
    assert rec["covered_csv"] == 4
    assert rec["failed"] == [], "failures come from the caller, not from the sidecar"


def test_a_stale_run_report_staging_dir_does_not_leak_into_the_record(tmp_path):
    """``.aw_run_report/`` is a hidden staging directory under the output dir.
    It is not a well, and it must not be counted as one."""
    containers, out_dir = _build_plate(tmp_path, ["A01"])
    write_well_report(out_dir, "H12", groups_total=4, groups_covered=1)
    assert collect_run_report(out_dir)["wells"], "premise: the staging dir exists"

    assert set(_scan(containers, out_dir)["wells"]) == {"A01"}


@contextlib.contextmanager
def _plain_writer(path):
    """Stand-in for ``pipeline.io._safe_atomic_text_write``.

    That helper is a context manager yielding a handle, not a
    ``(path, text)`` function — passing the wrong shape makes
    ``finalize_run_report`` swallow the ``TypeError`` as "could not record run
    completeness" and write nothing, which looks exactly like the feature under
    test being broken.
    """
    with Path(path).open("w") as handle:
        yield handle


class _SilentLog:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass
