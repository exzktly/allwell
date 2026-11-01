"""A packed-file converter reports its plane count in order, so the last count reported is the total.

Each converter (CZI, ND2, OME-TIFF) writes wells on several threads and counts planes under a lock, but it called
``progress_fn`` after releasing the lock. Two threads could then report out of order — one takes count 2, the other
takes 3 and 4 and reports them first, and 2 arrives last. The Datasets page keeps the last count it was given and
re-shows it when the run ends (``CziImportPage.set_outcome``), so a run that wrote four images ended reading
``2 of 4 image(s)``: CI's ``tests/test_czi_record_progress.py`` failed exactly that way.

The progress callback here sleeps on count 2, which is the window in which another thread could report past it. With
the callback inside the lock, nobody can; outside it, the counts arrive as 0, 1, 3, 4, 2.
"""

from __future__ import annotations

import threading
import time

import pytest


def _recorder():
    seen: list[int] = []
    totals: set[int] = set()
    guard = threading.Lock()

    def progress(done: int, total: int) -> None:
        if done == 2:
            time.sleep(0.3)               # hold count 2 back while the other well's thread writes on
        with guard:
            seen.append(done)
            totals.add(total)

    return progress, seen, totals


def _czi(tmp_path, progress):
    from pipeline.czi_explode import explode_czi
    from tests.test_czi_import_page import _named_czi

    src = _named_czi(tmp_path / "src", wells=("C07", "A02"))
    explode_czi(src, tmp_path / "out", experiment="E", workers=2, progress_fn=progress)


def _nd2(tmp_path, progress):
    pytest.importorskip("nd2")
    from pipeline.nd2_explode import explode_nd2
    from tests._nd2_fixture import write_nd2

    src = write_nd2(tmp_path / "Plate7.nd2", n_pos=2, n_t=1, n_c=2, well_names=["C07", "A02"],
                    channel_names=["DAPI", "GFP"])
    explode_nd2(src, tmp_path / "out", experiment="E", workers=2, progress_fn=progress)


def _ome(tmp_path, progress):
    from pipeline.ome_explode import explode_ome_tiff
    from tests.test_ome_explode import _write_ome

    src = _write_ome(tmp_path / "s.ome.tif", positions=2, size_t=1, size_c=2)
    explode_ome_tiff(src, tmp_path / "out", grid_rows=1, grid_cols=2, origin_well="A01", direction="row",
                     experiment="E", workers=2, progress_fn=progress)


@pytest.mark.parametrize("convert", [_czi, _nd2, _ome], ids=["czi", "nd2", "ome"])
def test_the_plane_counts_arrive_in_order_and_end_on_the_total(tmp_path, convert):
    progress, seen, totals = _recorder()
    convert(tmp_path, progress)
    assert totals == {4}
    assert seen == [0, 1, 2, 3, 4]
