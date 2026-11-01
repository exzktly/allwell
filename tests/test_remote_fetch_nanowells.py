"""A nanowell well's tables come back with its other output (tracker P9)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.remote_fetch_nanowells import (
    fetch_nanowell_tables, missing_nanowell_tables, pull_nanowell_results, reanchored_wells, well_files_to_delete,
)


class _Fetcher:
    """Records what it was asked to list and pull; the remote holds only the names in ``remote``."""

    def __init__(self, local, remote, *, nanowell=True):
        self.run = SimpleNamespace(local_output_dir=local, remote_output_dir="/scratch/out", nanowell=nanowell)
        self.host = SimpleNamespace(ssh_alias="gpu")
        self.remote, self.listed, self.pulled = set(remote), [], []

    def _list_remote_glob(self, src):
        self.listed.append(src)
        name = src.rsplit("/", 1)[1]
        return "gpu", ([f"/scratch/out/nanowells/{name}"] if name in self.remote else []), None

    def _pull_listed(self, host, files, dest):
        self.pulled += [(f, str(dest)) for f in files]
        return len(files)


def _sidecar(path, **blocks):
    (path / "pipeline_info.json").write_text(json.dumps(blocks))


def test_the_tables_and_the_template_are_pulled_by_exact_name_into_the_nanowells_directory(tmp_path):
    _sidecar(tmp_path, nanowell={"scheme_version": 1})
    fetcher = _Fetcher(tmp_path, {"A01.csv", "A01_grid.json", "A01_template.npz", "A01_segmenter_input.png",
                                  "A01_identity.json", "A010.csv", "A010_segmenter_input.png", "A010_identity.json"})
    assert fetch_nanowell_tables(fetcher, "A01") == 5
    names = ["A01.csv", "A01_grid.json", "A01_template.npz", "A01_segmenter_input.png", "A01_identity.json"]
    # The cell masks are listed too (``_fetch_masks``), and not counted: this host has none, as an older run's has not.
    assert fetcher.listed == [f"gpu:/scratch/out/nanowells/{n}" for n in [*names, "A01_masks.npz"]]
    assert [f for f, _ in fetcher.pulled] == [f"/scratch/out/nanowells/{n}" for n in names]
    assert {d for _, d in fetcher.pulled} == {str(tmp_path / "nanowells")} and (tmp_path / "nanowells").is_dir()


def test_a_run_that_is_not_in_nanowell_mode_costs_no_remote_call(tmp_path):
    # The run's own argv decides, not the local sidecar, which may be an earlier run's of the same folder.
    _sidecar(tmp_path, nanowell={"scheme_version": 1})
    fetcher = _Fetcher(tmp_path, {"A01.csv"}, nanowell=False)
    assert fetch_nanowell_tables(fetcher, "A01") == 0 and fetcher.listed == [] and not (tmp_path / "nanowells").exists()


def test_a_table_the_remote_lacks_is_not_invented(tmp_path):
    _sidecar(tmp_path, nanowell={})
    fetcher = _Fetcher(tmp_path, {"A01_grid.json"})
    assert fetch_nanowell_tables(fetcher, "A01") == 1


def test_fetching_a_well_through_the_fetcher_brings_its_tables(tmp_path, monkeypatch):
    """Driven through ``Fetcher._fetch_one`` (the hook is a line in it), with the transport stubbed."""
    import zipfile
    from unittest.mock import patch

    from services.remote_config import RemoteHost
    from services.remote_fetch import Fetcher
    from services.remote_run_state import _RemoteRun
    from services.remote_runner import LocalInputMode, OutputFilter

    host = RemoteHost(name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.s", remote_python="/opt/python", cpu_only=False, extra_args=(),
                      cache_budget_gb=10, measured_bandwidth_mbps=100.0)
    out = tmp_path / "out"
    out.mkdir()
    _sidecar(out, nanowell={"scheme_version": 1})
    run = _RemoteRun(run_id="r", cache_key="k", remote_input_dir="/r/in", remote_output_dir="/r/out",
                     remote_pidfile="/r/pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
                     nanowell=True)
    with patch.object(Fetcher, "_loop", lambda self: None):
        # Batched mode's Fetcher, the one that deletes a well's remote files once it is fetched.
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL, log_fn=lambda _m: None,
                          delete_after_download=True)

    def fake_rsync(self, argv):
        with zipfile.ZipFile(argv[-1], "w") as zf:
            zf.writestr("x_labels.tif", b"x")
        return True

    def fake_list(self, src):
        name = src.rsplit("/", 1)[1]
        return "test-host", ([f"/r/out/nanowells/{name}"] if "nanowells" in src else [f"/r/out/{name}"]), 10

    def fake_pull(self, host_, files, dest_dir):
        for f in files:
            (dest_dir / f.rsplit("/", 1)[1]).write_text("remote")
        return len(files)

    monkeypatch.setattr(Fetcher, "_rsync", fake_rsync)
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)
    monkeypatch.setattr(Fetcher, "_already_local", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_list_remote_glob", fake_list)
    monkeypatch.setattr(Fetcher, "_pull_listed", fake_pull)
    deleted = []
    import services.remote_fetch as _rf

    monkeypatch.setattr(_rf, "delete_remote_paths", lambda host, paths, **k: deleted.append(list(paths)))
    monkeypatch.setattr(Fetcher, "_pull_settings_marker", lambda self, w: None)
    # main's remote CSV compression is an ssh call of its own; stubbed with the rest of the transport.
    import services.remote_fetch as remote_fetch

    monkeypatch.setattr(remote_fetch, "compress_remote_csvs", lambda *a, **k: None)
    try:
        fetcher._fetch_one("A01")
    finally:
        fetcher.cancel()
    assert sorted(p.name for p in (out / "nanowells").iterdir()) == [
        "A01.csv", "A01_grid.json", "A01_identity.json", "A01_masks.npz", "A01_segmenter_input.png",
        "A01_template.npz"]                   # the masks too: a later run, here or sent back, segments only what changed
    assert deleted == []                      # the re-anchoring may re-run A01 from what is on the remote (X2)


# ── the end-of-run re-anchoring revisits wells already fetched (roll-up review X2) ──
def test_a_nanowell_run_deletes_nothing_of_a_well_mid_run():
    """The end-of-run re-anchoring re-runs a well from its input archive and earlier output, so neither may go."""
    plain = SimpleNamespace(nanowell=False, remote_input_dir="/r/in", remote_output_dir="/r/out")
    assert well_files_to_delete(plain, "A01") == [
        "/r/in/A01.zip", "/r/out/A01_out.zip", "/r/out/*_A01.csv", "/r/out/*_A01.csv.gz"]
    assert well_files_to_delete(SimpleNamespace(nanowell=True, remote_input_dir="/r/in",
                                                remote_output_dir="/r/out"), "A01") == []


class _Verifier:
    def __init__(self, local, *, nanowell=True):
        # The wells this run fetched, as ``_RemoteRun.fetched_wells`` holds them. D04 is not among them.
        self._run = SimpleNamespace(local_output_dir=local, remote_output_dir="/r/out", nanowell=nanowell,
                                    nanowell_released=set(), fetched_wells={"A01", "B02", "C03"})
        self.host = SimpleNamespace(ssh_alias="gpu")
        self.pulled = []

    def _rsync_optional(self, src, dest, *, log_fn, absent_note=""):
        self.pulled.append((src, str(dest)))
        return True                       # the pull succeeded: a ``None`` here reads as a failed pull


def _grid(folder, well, methods):
    folder.mkdir(exist_ok=True)
    (folder / f"{well}_grid.json").write_text(json.dumps(
        {"well": well, "timepoints": {f"{i}h": {"status": "ok", "origin_method": m} for i, m in enumerate(methods)}}))


def test_verification_pulls_the_final_tables_and_every_re_anchored_well_again(tmp_path):
    """Every well the re-anchoring re-ran, in any batch (roll-up review X32): read from the grid records, which
    each well keeps, not from the sidecar summary, which each batch of a batched run writes over the last."""
    _sidecar(tmp_path, nanowell={"reanchor": {"anchored": ["B02/24h"], "remaining": {}}})   # the last batch's
    folder = tmp_path / "nanowells"
    _grid(folder, "A01", ["fiducial", "plate_rigid"])          # an earlier batch's re-anchored well
    _grid(folder, "B02", ["plate_rigid"])
    _grid(folder, "C03", ["fiducial", "array_centre"])         # anchored on its own: not re-run
    _grid(folder, "D04", ["fiducial", "plate_rigid"])          # another run's well, in the local folder only
    verifier, lines = _Verifier(tmp_path), []
    pull_nanowell_results(verifier, log=lines.append)
    assert verifier.pulled == [
        ("gpu:/r/out/nanowells/", str(folder)),
        ("gpu:/r/out/A01_out.zip", str(tmp_path / "A01_out.zip")), ("gpu:/r/out/*_A01.csv.gz", str(tmp_path)),
        ("gpu:/r/out/B02_out.zip", str(tmp_path / "B02_out.zip")), ("gpu:/r/out/*_B02.csv.gz", str(tmp_path))]
    assert reanchored_wells(folder) == ["A01", "B02", "D04"]
    plain = _Verifier(tmp_path, nanowell=False)
    pull_nanowell_results(plain, log=lines.append)
    assert plain.pulled == []


def test_a_well_whose_tables_did_not_arrive_fails_verification(tmp_path):
    run = SimpleNamespace(local_output_dir=tmp_path, nanowell=True)
    (tmp_path / "nanowells").mkdir()
    (tmp_path / "nanowells" / "A01.csv").write_text("x")
    assert missing_nanowell_tables(run, "A01") == ["missing nanowells/A01_grid.json for A01"]
    assert missing_nanowell_tables(SimpleNamespace(local_output_dir=tmp_path, nanowell=False), "A01") == []


def test_a_remote_incremental_nanowell_run_is_refused():
    """The remote would analyse the new timepoints without the earlier ones and its tables would replace the
    local ones (roll-up review X3)."""
    from services.remote_run_state import NANOWELL_REMOTE_INCREMENTAL_REFUSAL, _RemoteRun
    from services.remote_runner import LocalInputMode

    args = dict(run_id="r", cache_key="k", remote_input_dir="/r/in", remote_output_dir="/r/out", remote_pidfile="p",
                local_output_dir="/tmp/x", local_input_mode=LocalInputMode.ZIP)
    _RemoteRun(**args, nanowell=True)
    _RemoteRun(**args, incremental=True)
    with pytest.raises(ValueError, match="An incremental nanowell run cannot run on a remote host yet"):
        _RemoteRun(**args, nanowell=True, incremental=True)
    assert NANOWELL_REMOTE_INCREMENTAL_REFUSAL.endswith("(untick Incremental).")


def test_a_nanowell_well_s_completion_line_is_the_one_the_fetcher_and_the_progress_count_read(tmp_path, monkeypatch,
                                                                                               caplog):
    """The marker, from a real run: a variant spelling fetched no nanowell well as it finished."""
    import logging

    from services.pipeline_runner import _RE_WELL_DONE_ID
    from services.remote_wire import _RE_WELL_DONE
    from tests._nanowell_run_fixture import build_well
    from tests.test_nanowell_cli import _run_main, _write_plate

    rendered, record = build_well(timepoint="0h")
    plate = _write_plate(tmp_path, rendered, record)
    with caplog.at_level(logging.INFO):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    messages = [r.getMessage() for r in caplog.records if "A01" in r.getMessage()]
    done = [m for m in messages if "done." in m]                     # the completion line: "Well A01 — done. …"
    assert done and _RE_WELL_DONE.search(done[0]).group("well") == "A01"
    assert _RE_WELL_DONE_ID.search(done[0]).group(1) == "A01"
    # The well's progress lines ("Well A01 — step k/N done") also say "done", for the plate's dial; read as the
    # completion line, a remote run would fetch the well before it had finished.
    steps = [m for m in messages if " step " in m]
    assert steps and not any(_RE_WELL_DONE.search(m) or _RE_WELL_DONE_ID.search(m) for m in steps)


# ── the empty reference that comes back (review tracker L7) ──────────────────
def _reference_settings():
    from pipeline import nanowell_run as nr

    return nr.NanowellSettings("100um", empty_well="H12")


def _save_reference(out, level, timepoints=("0h", "24h")):
    """H12 saved as the empty reference in *out*, its templates of *level*."""
    import numpy as np

    from pipeline import nanowell_measure as nm
    from pipeline import nanowell_reference as nref

    template = nm.Template(np.full((5, 5), float(level)), 2, nm.SOURCE_OWN, 30, 30, 0.05, ())
    verdict = {"status": nref.EMPTY, "loaded_share": 0.0, "nanowells_counted": 30, "max_loaded_share": 0.05}
    nref.save(out, "H12", _reference_settings(), {tp: (template, verdict) for tp in timepoints})


def _levels(out):
    from pipeline import nanowell_reference as nref

    reference = nref.load(out, _reference_settings())
    return {tp: float(reference.at(tp)[0].array[0, 0]) if reference.at(tp)[0] is not None else None
            for tp in ("0h", "24h")}


class _CopyingVerifier(_Verifier):
    """Its rsync copies the remote ``nanowells/`` over the local one: whole, or — *partial* — the index alone, as a
    transfer cut off after its first file (rsync sends ``reference.json`` before ``tp0000.npz``) leaves it."""

    def __init__(self, local, remote, *, partial=False, fetched=("A01", "B02", "C03")):
        super().__init__(local)
        self._run.fetched_wells = set(fetched)
        self.remote, self.partial = remote, partial

    def _rsync_optional(self, src, dest, *, log_fn, absent_note=""):
        import shutil

        self.pulled.append((src, str(dest)))
        if not src.endswith("/nanowells/"):
            return True
        for path in sorted((self.remote / "nanowells").rglob("*")):
            target = dest / path.relative_to(self.remote / "nanowells")
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not self.partial or path.name == "reference.json":
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        return not self.partial


@pytest.mark.parametrize("case", ["cut_off", "not_measured_by_this_run"])
def test_a_reference_the_run_did_not_bring_back_whole_and_its_own_leaves_the_one_here(tmp_path, case):
    """The local output folder holds H12's reference over 0h and 24h. Verification pulls the remote ``nanowells/``
    over it. A transfer cut off after the index paired the remote's index with the local templates of the same names;
    a remote reference this run did not measure (a copy an earlier attempt left there, the run's own seeding having
    failed) replaced the local one with an older one. Either way the reference here must be the one it was."""
    local, remote = tmp_path / "local", tmp_path / "remote"
    _save_reference(local, 1.0)
    _save_reference(remote, 2.0, timepoints=("0h",))
    fetched = ("A01", "B02", "C03") if case == "not_measured_by_this_run" else ("A01", "H12")
    verifier, lines = _CopyingVerifier(local, remote, partial=case == "cut_off", fetched=fetched), []
    pull_nanowell_results(verifier, log=lines.append)
    assert _levels(local) == {"0h": 1.0, "24h": 1.0}
    assert any("the empty reference" in line and "kept" in line for line in lines)


def test_the_reference_this_run_measured_replaces_the_one_here(tmp_path):
    """The run measured H12 (it fetched it), so the reference it brings back whole is the plate's now."""
    local, remote = tmp_path / "local", tmp_path / "remote"
    _save_reference(local, 1.0)
    _save_reference(remote, 2.0, timepoints=("0h",))
    pull_nanowell_results(_CopyingVerifier(local, remote, fetched=("A01", "H12")), log=lambda line: None)
    assert _levels(local) == {"0h": 2.0, "24h": None}


def _reference_folder(nanowells, well: str = "B02"):
    import json  # noqa: F811 — the module imports it too; kept local to the helper

    ref = nanowells / "_confluence_reference"
    ref.mkdir(parents=True)
    (ref / "reference.json").write_text(json.dumps(
        {"well": well, "timepoints": {"t1": {"status": "empty", "template": "tp0000.npz"}}}))
    (ref / "tp0000.npz").write_bytes(b"template")
    return ref


def test_the_reference_this_computer_sent_coming_back_unchanged_is_not_reported_refused(tmp_path):
    """A run that does not measure the reference well is sent this computer's reference, and the pull brings the
    same copy back. That was logged on every such run as "the empty reference fetched back was not taken" — a
    refusal of nothing. A different copy from a run that did not measure the well is still refused, and said."""
    import shutil
    import types

    from services import remote_fetch_nanowells as m

    nanowells = tmp_path / "out" / "nanowells"
    ref = _reference_folder(nanowells)
    run = types.SimpleNamespace(fetched_wells={"A01"})
    lines: list[str] = []
    kept = m._set_reference_aside(nanowells)
    shutil.copytree(kept, ref)                              # the pull brings back the copy that was sent
    m._settle_reference(nanowells, kept, True, run, lines.append)
    assert lines == [] and (ref / "tp0000.npz").read_bytes() == b"template"

    kept = m._set_reference_aside(nanowells)
    shutil.copytree(kept, ref)
    (ref / "tp0000.npz").write_bytes(b"another run's template")
    m._settle_reference(nanowells, kept, True, run, lines.append)
    assert len(lines) == 1 and "this run did not measure B02" in lines[0]
    assert (ref / "tp0000.npz").read_bytes() == b"template"           # the one here is kept
