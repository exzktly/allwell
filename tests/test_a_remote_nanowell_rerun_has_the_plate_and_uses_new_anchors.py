"""A remote nanowell re-run of a few wells is given the rest of the plate, and an anchor set since is never skipped.

Reported: "I just specified a well fiducial and forced a rerun, and it still said the well was unaddressed and had
no accepted fiducial." The anchor was set at the well's second timepoint, and *Re-run anchored wells…* ran the well
on a remote host. Measured with the real pipeline on the plate of ``test_nanowell_manual_anchor_run``: the anchored
timepoint came back addressed from the anchor, and the first came back "No accepted fiducial, array centre, or
precise plate-rigid prediction" — because the end-of-run pass that carries an origin to another timepoint fits the
plate's motion from the other wells' grid records, and a remote host cleaned after its last fetch has none. The same
re-run on this computer, beside the plate's records, addressed both.

Two things are held here, each through the code the app runs:

* ``RemoteRunner.spawn`` sends the other wells' grid records into the run's remote ``nanowells/`` before it starts
  the pipeline (``services.remote_plate_grids``); the pipeline side — that those records alone address the
  timepoint — is ``test_nanowell_manual_anchor_run``'s, in the nanowell lane;
* a well whose output was made under other anchors than the ones set now is not "finished": the remote re-run
  sends it (``services.remote_finished_wells``), and the local one forces it
  (``pipeline.nanowell_task.force_wells_with_new_anchors``), both from ``util.nanowell_anchors.wells_with_unused_anchors``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from util.nanowell_anchors import anchors_path, set_anchor, wells_with_unused_anchors

pytestmark = pytest.mark.usefixtures("offline_transport")


@pytest.fixture
def host():
    from tests.test_remote_runner import host as remote_host

    return remote_host.__wrapped__()


def _grid(out: Path, well: str, blocks: dict) -> Path:
    (out / "nanowells").mkdir(parents=True, exist_ok=True)
    path = out / "nanowells" / f"{well}_grid.json"
    path.write_text(json.dumps({"scheme_version": 1, "well": well, "timepoints": blocks}))
    return path


# ── which wells were made under other anchors ────────────────────────────────

def test_an_anchor_set_since_the_well_s_last_run_names_the_well(tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    _grid(out, "A01", {"0h": {"status": "unaddressed"}, "24h": {"status": "unaddressed"}})
    _grid(out, "B02", {"0h": {"status": "ok"}})
    assert wells_with_unused_anchors(inp, out) == []                     # no anchors file
    set_anchor(anchors_path(inp), "A01", "24h", (120.5, 80.25))
    assert wells_with_unused_anchors(inp, out) == ["A01"]


def test_an_anchor_the_last_run_used_is_no_reason_to_run_again_and_a_moved_or_cleared_one_is(tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    set_anchor(anchors_path(inp), "A01", "24h", (120.5, 80.25))
    used = {"0h": {"status": "unaddressed"}, "24h": {"status": "ok", "manual_anchor_um": [120.5, 80.25]}}
    _grid(out, "A01", used)
    assert wells_with_unused_anchors(inp, out) == []
    set_anchor(anchors_path(inp), "A01", "24h", (140.0, 80.25))         # moved
    assert wells_with_unused_anchors(inp, out) == ["A01"]
    anchors_path(inp).unlink()                                          # cleared: its result came from it
    assert wells_with_unused_anchors(inp, out) == ["A01"]


def test_the_remote_re_run_sends_an_unaddressed_well_once_an_anchor_is_set_and_forces_it(tmp_path):
    """The well ``test_a_remote_rerun_skips_finished_wells`` holds back as finished (every timepoint has a verdict)
    is sent once an anchor is set on it, and not again once a run has used that anchor."""
    from tests.test_a_remote_rerun_skips_finished_wells import NANOWELL, _forced, _opts, _plate, _record, _run

    inp, out = _plate(tmp_path)
    opts = _opts(**NANOWELL)
    for well in ("A01", "B02"):
        _record(out, well, opts)
        _grid(out, well, {"T01": {"status": "ok"}})
    _grid(out, "B02", {"T01": {"status": "unaddressed"}})
    assert _run(inp, out, opts)[0] is None                              # nothing to improve: held back
    set_anchor(anchors_path(inp), "B02", "T01", (10.0, 20.0))
    argv: list = []
    sent, _lines = _run(inp, out, opts, argv_sent=argv)
    assert sent == ["B02"] and _forced(argv[0]) == ["B02"]
    _grid(out, "B02", {"T01": {"status": "ok", "manual_anchor_um": [10.0, 20.0]}})
    assert _run(inp, out, opts)[0] is None                              # that anchor has been used


def test_a_local_plain_re_run_forces_the_wells_with_a_new_anchor(tmp_path):
    from types import SimpleNamespace

    from pipeline.nanowell_task import force_wells_with_new_anchors

    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    _grid(out, "B02", {"T01": {"status": "unaddressed"}})
    set_anchor(anchors_path(inp), "B02", "T01", (10.0, 20.0))
    args = SimpleNamespace(force=False, incremental=False, force_wells=["A01"])
    force_wells_with_new_anchors({"nanowell_format": "100um"}, args, inp, out)
    assert args.force_wells == ["A01", "B02"]
    for unchanged in (SimpleNamespace(force=True, incremental=False, force_wells=None),
                      SimpleNamespace(force=False, incremental=True, force_wells=None)):
        force_wells_with_new_anchors({"nanowell_format": "100um"}, unchanged, inp, out)
        assert unchanged.force_wells is None
    plain = SimpleNamespace(force=False, incremental=False, force_wells=None)
    force_wells_with_new_anchors({}, plain, inp, out)                   # not a nanowell run
    assert plain.force_wells is None


# ── the remote run is given the rest of the plate ───────────────────────────

ARGV = ["/usr/bin/python", "/local/process_microscopy.py", "--input_dir", "/local/in", "--output_dir", "/local/out"]


def _spawn(host, tmp_path, monkeypatch, stop_fetchers, argv, well="B02"):
    """``RemoteRunner.spawn`` of *well* alone into *tmp_path*/out, the harness of ``test_remote_runner``; returns
    ``(rsync commands sent through the transport, whether each came before the pipeline was launched, log)``."""
    from services import remote_transport
    from services.remote_run_state import OutputFilter
    from services.remote_staging import LocalInputMode, ZippedInput
    from tests.test_remote_runner import _make_spawn_test_runner, _make_well_zip

    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(_make_well_zip(tmp_path, f"{well}.zip", ["x.tif"]),))
    runner, captured = _make_spawn_test_runner(host, tmp_path, monkeypatch, stop_fetchers, completed_remote_wells=set())
    sent = []

    def rsync(cmd, **kw):
        sent.append((list(cmd), "argv" not in captured))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_transport, "_rsync_with_retry", rsync)
    log: list[str] = []
    runner.spawn(zipped, tmp_path / "out", pipeline_argv=argv, output_filter=OutputFilter.FULL, log_fn=log.append)
    return sent, runner._run, "".join(log)


def test_a_remote_nanowell_run_of_one_well_is_sent_the_other_wells_grid_records_first(
        host, tmp_path, monkeypatch, stop_fetchers):
    out = tmp_path / "out"
    a01 = _grid(out, "A01", {"0h": {"status": "ok"}})
    _grid(out, "B02", {"0h": {"status": "unaddressed"}})                # the well being run: not sent
    c03 = _grid(out, "C03", {"0h": {"status": "ok"}})
    (out / "nanowells" / "B02.csv").write_text("timepoint\n")           # a sites table is not a grid record
    sent, run, log = _spawn(host, tmp_path, monkeypatch, stop_fetchers, [*ARGV, "--nanowell_format", "100um"])
    grids = [(cmd, before) for cmd, before in sent if any(c.endswith("_grid.json") for c in cmd)]
    assert len(grids) == 1
    cmd, before_launch = grids[0]
    assert before_launch                                                # on the host before the pipeline starts
    assert "--update" in cmd
    assert [c for c in cmd if c.endswith("_grid.json")] == [str(a01), str(c03)]
    assert cmd[-1] == f"{host.ssh_alias}:{run.remote_output_dir}/nanowells/"
    assert "sent 2 other well(s)' grid records" in log


def test_a_run_that_is_not_in_nanowell_mode_sends_no_grid_records(host, tmp_path, monkeypatch, stop_fetchers):
    _grid(tmp_path / "out", "A01", {"0h": {"status": "ok"}})
    sent, _run, log = _spawn(host, tmp_path, monkeypatch, stop_fetchers, ARGV)
    assert not [cmd for cmd, _b in sent if any(c.endswith("_grid.json") for c in cmd)]
    assert "grid records" not in log


def test_a_copy_that_fails_is_said_and_the_run_goes_on(tmp_path):
    from types import SimpleNamespace

    from services import remote_plate_grids as rpg

    out = tmp_path / "out"
    _grid(out, "A01", {"0h": {"status": "ok"}})
    cache = SimpleNamespace(host=SimpleNamespace(ssh_alias="gpu", ssh_argv=lambda **k: ["ssh", "gpu"]),
                            _ssh_run=MagicMock(side_effect=OSError("no route to host")))
    run = SimpleNamespace(nanowell=True, local_output_dir=out, remote_output_dir="/r/out")
    log: list[str] = []
    assert rpg.seed_plate_grids(cache, run, ["B02"], log.append) == 0
    assert log == ["[remote] nanowell: the other wells' grid records could not be sent; timepoints that only the "
                   "rest of the plate could address will stay unaddressed\n"]


def test_both_remote_run_paths_send_the_grid_records():
    """The single-shot and the batched path each prepare their own run; each must send the records once its
    output directory exists. A path that forgot it would leave a batched re-run as unaddressed as before."""
    from tests._repo_scan import parse_source
    import ast

    def callers(module, name):
        tree = parse_source(Path(module))
        return {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == name for n in ast.walk(f))}

    assert "_run_prepared" in callers("services/remote_process.py", "seed_plate_grids")
    # The confluence reference: on both paths when staging left its well out, and on the batched path also when
    # the local-completion filter or the remote adoption drops it after the run was prepared (tracker H2).
    assert {"_run_prepared", "_run_batches"} <= callers("services/remote_process.py", "seed_confluence_reference")
    assert "_prepare_run" in callers("services/remote_runner.py", "_run_prepared")
    assert "__init__" in callers("services/remote_process.py", "_run_prepared")


# ── the remote run is given the plate's empty confluence reference (tracker H2) ──

def _reference(out: Path, well: str) -> Path:
    """A saved confluence reference measured on *well*, as ``pipeline.nanowell_reference.save`` lays it out."""
    from pipeline import nanowell_reference as nref

    folder = nref.directory(out)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reference.json").write_text(json.dumps({"well": well, "timepoints": {"0h": {"status": "empty",
                                                                                           "template": "tp0000.npz"}}}))
    (folder / "tp0000.npz").write_bytes(b"template")
    return folder


def _reference_sends(sent, folder):
    return [(cmd, before) for cmd, before in sent if str(folder) in cmd]


def test_a_remote_run_without_the_reference_well_is_sent_the_reference_before_it_starts(
        host, tmp_path, monkeypatch, stop_fetchers):
    """Reported by review: a remote re-run of a few wells found no reference on a cleaned host, and every well came
    back with no confluence, written over the values this computer had."""
    folder = _reference(tmp_path / "out", "H12")
    argv = [*ARGV, "--nanowell_format", "100um", "--nanowell_empty_well", "H12"]
    sent, run, log = _spawn(host, tmp_path, monkeypatch, stop_fetchers, argv)
    sends = _reference_sends(sent, folder)
    assert len(sends) == 1
    cmd, before_launch = sends[0]
    assert before_launch and "--update" in cmd and "-a" in cmd
    assert cmd[-2:] == [str(folder), f"{host.ssh_alias}:{run.remote_output_dir}/nanowells/"]
    assert "sent the plate's empty confluence reference" in log


def test_a_remote_run_that_measures_the_reference_well_is_not_sent_the_old_one(
        host, tmp_path, monkeypatch, stop_fetchers):
    folder = _reference(tmp_path / "out", "H12")
    argv = [*ARGV, "--nanowell_format", "100um", "--nanowell_empty_well", "h12"]
    sent, _run, log = _spawn(host, tmp_path, monkeypatch, stop_fetchers, argv, well="H12")
    assert _reference_sends(sent, folder) == []
    assert "confluence reference" not in log


def test_the_reference_folder_named_here_is_the_pipeline_s():
    from pipeline import nanowell_reference as nref
    from services import remote_plate_grids as rpg

    assert rpg.REFERENCE_DIR == nref.DIR_NAME
    assert nref.directory(Path("o")) == Path("o") / rpg.NANOWELL_DIR / rpg.REFERENCE_DIR


def test_a_reference_copy_that_fails_is_said_and_the_run_goes_on(tmp_path):
    from types import SimpleNamespace

    from services import remote_plate_grids as rpg

    _reference(tmp_path / "out", "H12")
    cache = SimpleNamespace(host=SimpleNamespace(ssh_alias="gpu"), _ssh_run=MagicMock(side_effect=OSError("down")))
    run = SimpleNamespace(nanowell=True, local_output_dir=tmp_path / "out", remote_output_dir="/r/out")
    log: list[str] = []
    assert rpg.seed_confluence_reference(cache, run, ["B02"], log.append) is False
    assert log == ["[remote] nanowell: the plate's empty confluence reference could not be sent; the wells of this "
                   "run will come back with no confluence\n"]
