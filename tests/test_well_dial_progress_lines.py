"""The lines Analyze's run reader turns into a well's progress dial on the plate.

Reported: "for nanowells, there's basically no status update on the pie in the analyze plate map when running". The
dial follows two lines — a well's total and each piece of it done — which a field-by-field well printed as
``FOV-group`` / ``FOV k/n done`` and a nanowell well, processed a timepoint at a time, never printed at all. It now
prints steps (``pipeline.nanowell_task._log_steps``); ``test_nanowell_cli.py`` drives a real nanowell run through
the reader. This holds the reader on both spellings, as the run writes them.
"""

from __future__ import annotations

from services.pipeline_runner import ProgressTracker


def _dial(lines):
    runner = ProgressTracker()
    return [payload for line in lines for kind, payload in runner.parse(line) if kind == "well_fov"]


def test_a_field_by_field_well_moves_its_dial_as_before():
    assert _dial(["12:00:01 INFO Well A01 — 3 FOV-group(s)", "12:00:09 INFO Well A01 — FOV 1/3 done",
                  "12:00:20 INFO Well A01 — FOV 3/3 done"]) == [("A01", 0, 3), ("A01", 1, 3), ("A01", 3, 3)]


def test_a_nanowell_well_moves_its_dial_by_steps():
    from pipeline.nanowell_task import _log_steps

    import logging

    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(f"12:00:00 INFO {record.getMessage()}")

    handler = Collect()
    logger = logging.getLogger("allwell.nanowell")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        for done in range(5):
            _log_steps("B07", done, 4)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    assert _dial(seen) == [("B07", 0, 4), ("B07", 1, 4), ("B07", 2, 4), ("B07", 3, 4), ("B07", 4, 4)]
