"""A batched nanowell run end to end on the stand-in cluster, through its end-of-run re-anchoring pass.

The companion of ``tests/test_a_remote_run_on_a_standin_cluster.py`` (tracker P1), on the path incident 1 was on.
The plate is ``tests/test_nanowell_reanchor.py``'s: four one-tile wells imaged at 0h and 24h, the plate turned and
shifted in between, and B02 at 24h unable to find its own square or boundary. In batches of two, the batch that
runs B02 holds its archive in ``keep/``, the driver runs one more batch with ``--nanowell_reanchor_only``, which
places B02's 24h from the other wells' grid records — still on the remote, because a batch's release never deletes
``nanowells/`` — and B02 is pulled again once that pass has rewritten it (``remote_fetch_nanowells.release_batch``).

Everything is the app's and the pipeline's own code on rendered tiles, except what the in-process nanowell tests
replace as well, here inside the node's interpreter (``StandinCluster.segment_nanowells_with_a_stand_in``): Cellpose,
which is not installed, and B02's blindness at 24h.

In the nanowell end-to-end lane (``tests/_lanes.py``): it renders tiles and runs the real detection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._standin_remote import (
    StandinCluster,
    assert_correct_or_visibly_failed,
    journal_states,
    remote_output_wells,
    shorten_deadlines,
    standin_env_ok,
    start_run,
)

pytestmark = pytest.mark.skipif(not standin_env_ok(), reason="needs bash and rsync")

pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("tifffile")

WELLS = ("A01", "A02", "B01", "B02")


def _opts() -> dict:
    return {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
            "nuclear_token": "PHASE", "phase_token": "PHASE", "fluor_tokens": ["GFP"], "csv_prefix": "measurements",
            "segmentation_method": "cellpose_labelfree", "nanowell_format": "100um",
            "background": "none", "flatfield": "none", "auto_threshold": False, "remote_batch_size": 2}


def _nanowell_complete(out: Path, wells) -> set[str]:
    """A nanowell well is here when its archive, its cells table, its site table and its grid record are."""
    import gzip

    here = set()
    for w in wells:
        cells = out / f"measurements_{w}.csv.gz"
        if ((out / f"{w}_out.zip").is_file() and cells.is_file() and gzip.decompress(cells.read_bytes())
                and (out / "nanowells" / f"{w}.csv").is_file() and (out / "nanowells" / f"{w}_grid.json").is_file()):
            here.add(w)
    return here


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    c = StandinCluster(tmp_path / "remote", monkeypatch)
    c.segment_nanowells_with_a_stand_in(monkeypatch, blind="B02:24h")
    yield c
    c.reap()


def test_a_batched_nanowell_run_re_anchors_a_well_from_the_plate_and_brings_it_home(cluster, tmp_path,
                                                                                    stop_fetchers, monkeypatch):
    from tests.test_nanowell_reanchor import _write_plate

    shorten_deadlines(monkeypatch)
    plate, _truth = _write_plate(tmp_path)
    inp, out = plate / "in", plate / "out"

    run = start_run(cluster, inp, out, stop_fetchers, _opts(), limit_s=80)

    assert_correct_or_visibly_failed(run, cluster, WELLS, expect="done", complete=_nanowell_complete)
    starts = [ln for ln in run.text.splitlines() if "batch-driver: batch" in ln and "starting" in ln]
    assert starts == [f"[remote] batch-driver: batch {i} starting" for i in (1, 2, 3)], (
        "two data batches and the final pass, in one allocation", starts)
    assert run.text.count("[remote] scheduler: allocation granted") == 1
    assert "Well B02 — held for the end-of-run re-anchoring (24h unaddressed)" in run.text, run.text[-4000:]
    assert "[remote] B02: pulled again after the end-of-run re-anchoring re-ran it." in run.text, run.text[-4000:]
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())
    assert grid["timepoints"]["24h"]["status"] == "ok", grid["timepoints"]["24h"]
    assert grid["timepoints"]["24h"]["origin_method"] == "plate_rigid", grid["timepoints"]["24h"]
    others = [json.loads((out / "nanowells" / f"{w}_grid.json").read_text()) for w in ("A01", "A02", "B01")]
    assert {g["timepoints"]["24h"]["origin_method"] for g in others} == {"fiducial"}
    assert journal_states(out) == {w: "fetched" for w in WELLS}
    assert remote_output_wells(cluster) == set(), "a well's output was left on the host after Auto-delete"
    assert cluster.jobs() == [], "the allocation outlived the run"
