"""Force reprocess all wells reprocesses all wells on a remote run.

It did nothing, through two defects that each needed the other:

* ``AnalyzeSuspendMixin.resume_aware_spec`` runs on every Start and turns
  ``incremental`` on whenever an earlier run on the same input did not finish —
  without looking at ``force``, though its own log line tells the user to tick
  Force to recompute from scratch. A cluster that loses nodes leaves many
  unfinished runs, so it fired on every run.
* Remote staging (``stage_local_zips``) knew nothing of ``force``. Given
  ``incremental`` it dropped every well whose local CSV already covered its
  input — "✓ A01: up to date (local) — not uploaded" — so the pipeline on the
  host received ``--force`` and an input with nothing in it.

The runner test drives the real ``PipelineRunner._run_pipeline_remote`` with the
real staging over a real plate whose wells are complete locally; only the host
is a stub that records what it was handed.
"""

from __future__ import annotations

import csv
import types
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import services.suspend_record as sr
from analyze.suspend import AnalyzeSuspendMixin
from services.run_spec import RunSpec

SCHEMA = "experiment:channel:well:fov:timepoint"


def _complete_plate(tmp_path: Path) -> tuple[Path, Path]:
    """Two wells of two FOVs each, both already measured in the output folder."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    for well in ("A01", "B02"):
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for fov in (1, 2):
                for ch in ("DAPI", "GFP"):
                    zf.writestr(f"e_{ch}_{well}_{fov}_T01.tif", b"x")
        with open(out / f"measurements_{well}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["well", "fov", "timepoint"])
            w.writeheader()
            for fov in (1, 2):
                w.writerow({"well": well, "fov": str(fov), "timepoint": "T01"})
    return inp, out


def _run_remote(tmp_path: Path, **opts_extra):
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args

    inp, out = _complete_plate(tmp_path)
    spawned: list = []

    class _Proc:
        pid = 1
        returncode = 0
        stdout = iter(())
        _ssh = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def spawn(zipped, out_dir, argv, **k):
        spawned.append((zipped, list(argv)))
        return _Proc()

    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="cluster")
    r._remote_runner = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn, verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False)
    opts = {"filename_schema": SCHEMA, "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **opts_extra}
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    return spawned


def test_force_uploads_every_well_whole_even_when_incremental_is_on(tmp_path):
    spawned = _run_remote(tmp_path, force=True, incremental=True)
    assert spawned, "nothing was sent to the host"
    (zipped, argv), = spawned
    assert "--force" in argv
    assert [p.stem for p in zipped.well_zips] == ["A01", "B02"]
    for wz in zipped.well_zips:
        assert len(zipfile.ZipFile(wz).namelist()) == 4, (
            f"{wz.stem} was trimmed to its unmeasured FOVs")
    assert zipped.incremental is False


def test_incremental_without_force_still_skips_complete_wells(tmp_path):
    """The other half of the rule: incremental keeps its meaning."""
    spawned = _run_remote(tmp_path, incremental=True)
    assert all(not z.well_zips for z, _ in spawned)


# ── the resume rewrite ───────────────────────────────────────────────────────

class _Host(AnalyzeSuspendMixin):
    def __init__(self):
        self.logged: list[str] = []

    def _log_line(self, text, level="INFO"):
        self.logged.append(text)


@pytest.fixture
def unfinished_run_behind_the_input(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    sr.save(sr.SuspendedRun(
        spec={"opts": {"raw": "/data/in"}}, input_dir="/data/in",
        output_dir="/data/out", suspended_at=1000.0, wells_done=4,
        wells_total=10))


def test_a_forced_run_is_not_turned_incremental(unfinished_run_behind_the_input):
    host = _Host()
    spec = RunSpec(opts={"raw": "/data/in", "force": True}, remote_name="cluster")
    assert host.resume_aware_spec(spec) is spec
    assert host.logged == []


def test_an_unforced_run_still_resumes_incrementally(unfinished_run_behind_the_input):
    out = _Host().resume_aware_spec(RunSpec(opts={"raw": "/data/in"}, remote_name=None))
    assert out.opts["incremental"] is True
