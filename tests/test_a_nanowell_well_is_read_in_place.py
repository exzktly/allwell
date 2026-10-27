"""A nanowell well reads its tiles out of the archive in place, and says so while it works.

A nanowell well unpacked every tile it would process — every timepoint, phase and fluorescence — into the scratch
directory before its first timepoint was located, after logging ``Found N image group(s)`` and before anything
else. On a cluster whose scratch is the networked home directory, eight workers doing that at once held a remote
batch silent for about ten minutes. ``pipeline.nanowell_task.process_well_zip`` now reads each tile from the
archive when a timepoint loads it (``pipeline.zip_members.ZipMemberReader``), and ``nanowell_run.run_well`` says
when it starts and finishes reading, placing and registering a timepoint's tiles — the longest quiet stretch that
is left.

Driven through ``process_microscopy.main``, as ``test_nanowell_registration_reuse`` is, because the unpack sat in
the task between the grouper and the run, where no test of the reader alone could see it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well
from tests.test_nanowell_registration_reuse import WELL, _run, _write_plate


@pytest.fixture(scope="module")
def rendered_well():
    return build_well(timepoint="0h")


def _images_in_scratch() -> list[str]:
    root = Path(os.environ["ALLWELL_SCRATCH_DIR"])
    return sorted(p.name for p in root.rglob("*.tif")) if root.exists() else []


def test_no_tile_is_unpacked_and_the_well_still_completes(tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    seen_during_locate: list[list[str]] = []
    real = nr.locate_timepoint

    def spying(*args, **kwargs):
        seen_during_locate.append(_images_in_scratch())
        return real(*args, **kwargs)

    monkeypatch.setattr(nr, "locate_timepoint", spying)
    out = _run(monkeypatch, plate, "out")

    assert seen_during_locate == [[]], (
        f"tiles were unpacked to scratch before the timepoint was located: {seen_during_locate}")
    assert (out / "nanowells" / f"{WELL}_grid.json").is_file()
    assert (out / "nanowells" / f"{WELL}.csv").is_file()


def test_the_well_says_when_it_reads_and_registers_a_timepoint(tmp_path, monkeypatch, rendered_well, caplog):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    caplog.set_level(logging.INFO)
    _run(monkeypatch, plate, "out")
    said = [r.getMessage() for r in caplog.records]
    n = len(rendered.images)
    assert f"Well {WELL} — 0h: reading, placing and registering {n} tile(s)" in said
    done = [m for m in said if m.startswith(f"Well {WELL} — 0h: tiles placed and registered in ")]
    assert len(done) == 1 and done[0].endswith(" s (ok)"), done
    first = said.index(f"Well {WELL} — 0h: reading, placing and registering {n} tile(s)")
    assert said.index(done[0]) > first
