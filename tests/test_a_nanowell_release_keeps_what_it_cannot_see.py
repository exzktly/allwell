"""A nanowell well is released from the remote only on answers the host actually gave (tracker row A4).

``release_batch`` deletes a batch's wells from the remote once their re-anchored output is home. It decides which
wells the re-anchoring re-ran from the grid records it has just pulled, and whether their output arrived from a
``(listed, pulled)`` count. Both answers used to come out of a failed ssh as "nothing to do": a ``nanowells/``
table pull that failed left yesterday's grid, which reads as "not re-anchored"; a listing that failed made
``_rsync_glob`` say ``(0, 0)``, and ``0 < 0`` is false. Either way the well's remote archive, cell CSV and input —
the only re-anchored copy — were deleted. The existing ``tests/test_nanowell_batch_release.py::_Fetcher`` stub
could not show it, because its listing always succeeds; these drive the real ``Fetcher`` and verifier against the
stand-in binaries of ``tests/_standin_remote.py``.
"""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from services.remote_fetch import FetchedOutputVerifier, Fetcher, _snapshot_local_artifacts
from services.remote_fetch_nanowells import release_batch
from services.remote_run_state import LocalInputMode, OutputFilter, _RemoteRun
from services.remote_upload import DatasetCache
from tests._standin_remote import StandinHost, standin_env_ok

pytestmark = pytest.mark.skipif(not standin_env_ok(), reason="needs bash and rsync")

RUN = "~/.allwell-staging/runs/r1"
#: The listing scripts, before and after the fix.
LISTINGS = ("ls -1 ", '[ -e "$f" ]')


@pytest.fixture
def standin(tmp_path, monkeypatch):
    return StandinHost(tmp_path, monkeypatch)


def _grid(path: Path, method: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"well": "B02", "timepoints": {"24h": {"status": "ok", "origin_method": method}}}))


def _plate(standin, out: Path) -> None:
    """B02 fetched once before the end-of-run re-anchoring re-ran it: the host has the re-anchored copy."""
    base = standin.root / ".allwell-staging/runs/r1"
    with zipfile.ZipFile(standin.path(".allwell-staging/runs/r1/in/B02.zip"), "w") as zf:
        zf.writestr("exp_DAPI_B02_f1_24h.tif", b"raw")
    with zipfile.ZipFile(standin.path(".allwell-staging/runs/r1/out/B02_out.zip"), "w") as zf:
        zf.writestr("exp_DAPI_B02_f1_24h.tif", b"re-anchored")
    standin.path(".allwell-staging/runs/r1/out/measurements_B02.csv.gz").write_bytes(b"\x1f\x8b re-anchored rows")
    _grid(base / "out/nanowells/B02_grid.json", "plate_rigid")
    (base / "out/nanowells/B02.csv").write_text("site,timepoint\n1,24h\n")
    # Here: the first download, from before the re-anchoring.
    _grid(out / "nanowells/B02_grid.json", "fiducial")
    (out / "nanowells/B02.csv").write_text("site,timepoint\n1,24h\n")
    with zipfile.ZipFile(out / "B02_out.zip", "w") as zf:
        zf.writestr("exp_DAPI_B02_f1_24h.tif", b"first fit")


def _fetcher(standin, out: Path) -> Fetcher:
    run = _RemoteRun(run_id="r1", cache_key="k1", remote_input_dir=f"{RUN}/in", remote_output_dir=f"{RUN}/out",
                     remote_pidfile=f"{RUN}/run.pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
                     output_filter=OutputFilter.FULL, nanowell=True, pre_run_artifacts=_snapshot_local_artifacts(out))
    run.fetched_wells.add("B02")
    # No worker: release_batch is called here as the worker would call it. (Cancelling one instead would set the
    # stop flag every pull honours, and each would come back "cancelled" — a failure this file did not inject.)
    with patch.object(Fetcher, "_loop", lambda self: None):
        return Fetcher(standin.host(), run, output_filter=OutputFilter.FULL, log_fn=lambda _m: None)


def _remote_kept(standin) -> bool:
    base = standin.root / ".allwell-staging/runs/r1"
    return all(p.exists() for p in (base / "out/B02_out.zip", base / "out/measurements_B02.csv.gz", base / "in/B02.zip"))


@pytest.mark.parametrize("fault", ["tables-listing", "tables-pull"])
def test_a_well_whose_tables_did_not_come_back_is_not_released(standin, tmp_path, fault):
    out = tmp_path / "out"
    _plate(standin, out)
    if fault == "tables-listing":
        standin.fail_ssh(*LISTINGS)
    else:
        standin.fail_rsync("nanowells/B02_grid.json")
    fetcher, lines = _fetcher(standin, out), []
    assert release_batch(fetcher, ["B02"], log=lines.append) == []
    assert _remote_kept(standin), "the only re-anchored copy was deleted on a grid record from before it"
    assert not any("pulled again after the end-of-run re-anchoring" in line for line in lines), lines


def test_a_re_anchored_well_whose_output_listing_failed_is_not_released(standin, tmp_path):
    out = tmp_path / "out"
    _plate(standin, out)
    standin.fail_ssh("ls -1 && B02_out.zip", "for f in && B02_out.zip")      # the listing, never the rm
    fetcher, lines = _fetcher(standin, out), []
    assert release_batch(fetcher, ["B02"], log=lines.append) == []
    assert _remote_kept(standin), "'0 of 0 arrived' was read as 'all of it arrived'"
    assert not any("pulled again after the end-of-run re-anchoring" in line for line in lines), lines


def test_the_released_well_is_the_one_whose_output_came_home(standin, tmp_path):
    """The happy path, through the same fixture: nothing fails, B02 is pulled again and released."""
    out = tmp_path / "out"
    _plate(standin, out)
    fetcher, lines = _fetcher(standin, out), []
    assert release_batch(fetcher, ["B02"], log=lines.append) == ["B02"]
    assert not _remote_kept(standin)
    with zipfile.ZipFile(out / "B02_out.zip") as zf:
        assert zf.read("exp_DAPI_B02_f1_24h.tif") == b"re-anchored"


def test_verification_fails_when_the_nanowells_pull_fails(standin, tmp_path):
    out = tmp_path / "out"
    _plate(standin, out)
    standin.path(".allwell-staging/runs/r1/out/pipeline_info.json").write_text("{}")
    fetcher = _fetcher(standin, out)
    standin.fail_rsync("out/nanowells/")
    verifier = FetchedOutputVerifier(standin.host(), fetcher.run, DatasetCache(standin.host()), threading.Event())
    verdict = verifier.verify(True, log=lambda _m: None)
    assert not verdict.passed
    assert any("nanowells/ tables" in m for m in verdict.missing), verdict.missing
