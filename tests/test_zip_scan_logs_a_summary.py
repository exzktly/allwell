"""Scanning a well's archive logs a summary, not a line per image.

Reported: with one timepoint and one FOV selected in the Image Table, the log
drawer still listed every image in the well's zip. The scan builds one
inventory of the whole well (every timepoint and FOV, cached for the session),
which is by design, but ``scan_zip_members`` also logged each member it
classified — an INFO "+ FLUOR fov=… tp=…" line per image and a DEBUG line per
member — and the drawer shows DEBUG. Those per-member lines now print only when
image-load debugging is on (``core.debug_flags.review_image_load_debug_enabled``);
the "Scanning … (N members)" and "Result: …" lines always print.
"""

from __future__ import annotations

import logging
import zipfile

import pytest

from core import debug_flags
from review.data import image_discovery


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "A01.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for fov in (1, 2):
            for tp in range(6):
                zf.writestr(f"EXP_GFP_A01_f{fov}_t{tp}.tif", b"x")
        zf.writestr("notes.txt", b"x")
    return path


@pytest.fixture
def records():
    seen: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            seen.append(record)

    lg = logging.getLogger("review")
    h = _Keep(level=logging.DEBUG)
    old_level = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    yield seen
    lg.removeHandler(h)
    lg.setLevel(old_level)


def _messages(seen):
    return [r.getMessage() for r in seen]


def test_a_scan_logs_two_lines_however_many_images(archive, records, monkeypatch):
    monkeypatch.setattr(debug_flags, "REVIEW_TAB_DEBUG", False)
    monkeypatch.setattr(debug_flags, "REVIEW_IMAGE_LOAD_DEBUG", False)
    fluor = image_discovery.scan_zip_members(archive, "gfp")[0]
    assert len(fluor) == 12, "the inventory still covers every timepoint and FOV"
    msgs = _messages(records)
    assert msgs == [
        "Scanning A01.zip  (13 members)",
        "Result: 12 fluor, 0 tophat, 0 smfish, 0 overlay(s), 0 mask(s) from A01.zip",
    ]


def test_image_load_debugging_still_traces_every_member(archive, records, monkeypatch):
    monkeypatch.setattr(debug_flags, "REVIEW_IMAGE_LOAD_DEBUG", True)
    image_discovery.scan_zip_members(archive, "gfp")
    msgs = _messages(records)
    assert sum(m.startswith("  + FLUOR") for m in msgs) == 12
    assert any(m.startswith("  SKIP non-image: notes.txt") for m in msgs)
