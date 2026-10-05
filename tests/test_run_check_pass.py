"""The end-of-run pass that records completeness, driven directly.

``scan_run`` is tested on its own in ``test_run_check_block.py``. What this
covers is the wiring around it inside ``process_microscopy``: that the block
reaches ``pipeline_info.json`` without disturbing what was already there, that
turning the check off is distinguishable from never having run one, that a
suspended run is not shouted at, and that a failure in the check cannot take
down a run whose real output is already on disk.
"""

from __future__ import annotations

import json
import types
import zipfile
from pathlib import Path

import pytest

import pipeline.completeness as pc
import process_microscopy as pm
from pipeline import cli_args
from util import schema
import pipeline.suspend as _suspend

SCHEMA = "exp:channel:well:fov:timepoint"
SEP = "_"
PREFIX = "measurements"
FOVS = ("F001", "F002")
TPS = ("0", "24h")


def _args(**kw):
    base = dict(filename_schema=SCHEMA, filename_sep=SEP, csv_prefix=PREFIX,
                no_run_check=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _plate(root: Path, wells, *, cover=None):
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    containers = {}
    for well in wells:
        names = [f"exp1_DAPI_{well}_{f}_{t}.tif" for f in FOVS for t in TPS]
        containers[well] = in_dir / f"{well}.zip"
        with zipfile.ZipFile(containers[well], "w") as zf:
            for name in names:
                zf.writestr(name, b"x")
        kept = names if cover is None else names[:cover]
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for name in kept:
                zf.writestr(name, b"x")
        pairs = [(f, t) for f in FOVS for t in TPS][:len(kept)]
        (out_dir / f"{PREFIX}_{well}.csv").write_text(
            "fov,timepoint,nucleus_id\n"
            + "".join(f"{f},{t},1\n" for f, t in pairs))
    (out_dir / pm.PIPELINE_INFO_FILENAME).write_text(json.dumps({"schema": SCHEMA}))
    return containers, out_dir


def _sidecar(out_dir: Path) -> dict:
    return json.loads((out_dir / pm.PIPELINE_INFO_FILENAME).read_text())


class _Log:
    def __init__(self):
        self.info_lines, self.warnings = [], []

    def info(self, msg, *a):
        self.info_lines.append(msg % a if a else msg)

    def warning(self, msg, *a):
        self.warnings.append(msg % a if a else msg)

    def error(self, msg, *a):
        self.warnings.append(msg % a if a else msg)


def _run(args, out_dir, containers, log=None, **kw):
    log = log or _Log()
    kw.setdefault("started_at", None)
    kw.setdefault("mode", "zip")
    kw.setdefault("suspended", _suspend.is_suspended())
    block = pc.record_run_check(
        info_path=out_dir / pm.PIPELINE_INFO_FILENAME,
        output_dir=out_dir,
        in_containers=containers,
        schema=schema.parse_schema(args.filename_schema),
        sep=args.filename_sep,
        csv_prefix=args.csv_prefix,
        atomic_writer=pm._safe_atomic_text_write,
        log=log,
        skip=bool(args.no_run_check),
        **kw)
    return block, log


# ── the happy path ──────────────────────────────────────────────────────────

def test_the_block_lands_in_the_sidecar(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"])
    block, log = _run(_args(), out_dir, containers)

    assert _sidecar(out_dir)["run_check"] == block
    assert block["totals"]["complete"] == 2
    assert block["skipped"] is False
    assert any("all 2 well(s) cover every FOV-group" in line
               for line in log.info_lines)
    assert log.warnings == []


def test_the_merge_leaves_the_rest_of_the_sidecar_alone(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    info_path = out_dir / pm.PIPELINE_INFO_FILENAME
    info_path.write_text(json.dumps({
        "schema": SCHEMA,
        "cell_gating": {"thresh_frac_on": {"GFP": 42.0}},
        "sample_definitions": {"notes": "do not lose me"},
    }))

    _run(_args(), out_dir, containers)
    info = _sidecar(out_dir)
    assert info["cell_gating"] == {"thresh_frac_on": {"GFP": 42.0}}
    assert info["sample_definitions"] == {"notes": "do not lose me"}
    assert info["schema"] == SCHEMA
    assert "run_check" in info


def test_a_shortfall_is_warned_about_and_named(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"], cover=1)
    block, log = _run(_args(), out_dir, containers)

    assert block["totals"]["partial"] == 2
    assert len(log.warnings) == 1
    warning = log.warnings[0]
    assert warning.startswith("INCOMPLETE:")
    assert "A01" in warning and "A02" in warning
    assert "--incremental" in warning


# ── turned off ──────────────────────────────────────────────────────────────

def test_turning_the_check_off_still_records_that_it_was_turned_off(tmp_path):
    """A stub, not silence.

    "The check was turned off" and "this dataset predates the check" need
    different things from whoever reads the record, and a reader that cannot
    tell them apart will offer the wrong one.
    """
    containers, out_dir = _plate(tmp_path, ["A01"])
    block, log = _run(_args(no_run_check=True), out_dir, containers)

    assert block["skipped"] is True
    assert block["skipped_reason"] == "--no_run_check"
    assert block["finished_at"]
    assert _sidecar(out_dir)["run_check"]["skipped"] is True
    assert block["wells"] == {}
    assert any("skipped" in line for line in log.info_lines)


def test_a_dataset_with_no_block_at_all_is_a_third_state(tmp_path):
    """Nothing here asserts behaviour — it pins the premise the other two rest
    on. A sidecar that never had the check run against it has no key, which is
    neither the skipped stub nor a real record."""
    _containers, out_dir = _plate(tmp_path, ["A01"])
    assert "run_check" not in _sidecar(out_dir)


# ── suspended ───────────────────────────────────────────────────────────────

def test_a_suspended_run_is_recorded_as_suspended_and_not_shouted_at(tmp_path,
                                                                     monkeypatch):
    """Suspend stops taking on new FOV-groups and exits through the normal
    end-of-well path, so most wells are genuinely partial. Shouting INCOMPLETE
    describes the user's own stop back at them as a fault."""
    containers, out_dir = _plate(tmp_path, ["A01", "A02"], cover=1)
    monkeypatch.setattr(_suspend, "is_suspended", lambda: True)

    block, log = _run(_args(), out_dir, containers)
    assert block["suspended"] is True
    assert log.warnings == []
    assert any("suspended" in line for line in log.info_lines)


def test_an_unsuspended_partial_run_still_warns(tmp_path, monkeypatch):
    containers, out_dir = _plate(tmp_path, ["A01"], cover=1)
    monkeypatch.setattr(_suspend, "is_suspended", lambda: False)
    _block, log = _run(_args(), out_dir, containers)
    assert any(w.startswith("INCOMPLETE:") for w in log.warnings)


# ── failures come from the run report ───────────────────────────────────────

def test_recorded_failures_are_attached_to_their_well(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01", "A02"], cover=1)
    info_path = out_dir / pm.PIPELINE_INFO_FILENAME
    info = json.loads(info_path.read_text())
    info["run_report"] = {"wells": {
        "A01": {"well": "A01", "groups_total": 4, "groups_covered": 1,
                "failed": ["F002_24h"], "aborted_reason": ""},
        "A02": {"well": "A02", "groups_total": 4, "groups_covered": 1,
                "failed": [], "aborted_reason": ""},
    }}
    info_path.write_text(json.dumps(info))

    block, _log = _run(_args(), out_dir, containers)
    assert block["wells"]["A01"]["failed"] == ["F002_24h"]
    assert block["wells"]["A02"]["failed"] == []


def test_an_unreadable_run_report_costs_the_labels_and_nothing_else(tmp_path):
    containers, out_dir = _plate(tmp_path, ["A01"])
    (out_dir / pm.PIPELINE_INFO_FILENAME).write_text("{not json")

    block, log = _run(_args(), out_dir, containers)
    assert block["wells"]["A01"]["failed"] == []
    assert block["totals"]["complete"] == 1
    assert log.warnings == []


# ── the check never takes a run down ────────────────────────────────────────

def test_a_failure_in_the_check_is_a_warning_not_an_exception(tmp_path,
                                                              monkeypatch):
    """The run's real output is already on disk. A bookkeeping step that raised
    here would turn a finished plate into a failed run."""
    containers, out_dir = _plate(tmp_path, ["A01"])
    monkeypatch.setattr(pc, "scan_run", _boom)

    block, log = _run(_args(), out_dir, containers)
    assert block == {}
    assert any("Completeness check failed" in w for w in log.warnings)
    assert "run_check" not in _sidecar(out_dir)


def test_an_unwritable_sidecar_does_not_raise(tmp_path, monkeypatch):
    containers, out_dir = _plate(tmp_path, ["A01"])
    monkeypatch.setattr(pm, "_safe_atomic_text_write", _boom)
    block, log = _run(_args(no_run_check=True), out_dir, containers)
    assert block["skipped"] is True
    assert any("skipped completeness check" in w for w in log.warnings)


def _boom(*a, **kw):
    raise RuntimeError("nope")


# ── the flag is declared where the pipeline can see it ──────────────────────

def test_the_parser_accepts_the_flag_and_defaults_it_off():
    parser = cli_args.build_parser()
    args = parser.parse_args([
        "--input_dir", "in", "--output_dir", "out", "--nuclear_token", "DAPI"])
    assert args.no_run_check is False
    args = parser.parse_args([
        "--input_dir", "in", "--output_dir", "out", "--nuclear_token", "DAPI",
        "--no_run_check"])
    assert args.no_run_check is True


def test_the_flag_reaches_the_sidecars_execution_options():
    """``vars(args)`` becomes ``execution_options``, which is what lets the
    Analyze form restore the checkbox from a prior run."""
    parser = cli_args.build_parser()
    args = parser.parse_args([
        "--input_dir", "in", "--output_dir", "out", "--nuclear_token", "DAPI",
        "--no_run_check"])
    assert vars(args)["no_run_check"] is True


@pytest.mark.parametrize("block_name", ["run_report", "run_check"])
def test_a_run_description_is_never_carried_across_a_sidecar_rewrite(block_name):
    """Both describe one run's output. Carrying either across a rewrite means a
    run that died before describing itself keeps the previous run's
    description, which is worse than having none."""
    from pipeline.sidecar import USER_SIDE_BLOCKS
    assert block_name not in USER_SIDE_BLOCKS
