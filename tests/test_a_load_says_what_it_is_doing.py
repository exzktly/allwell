"""After its last CSV, a dataset load says which phase it is in and which well it is reading.

The report: the status raced through the ``.csv.gz`` files, then sat at
``40/40`` with a busy cursor and nothing else, long enough to look stuck. On a
plate larger than the row cache only the first wells are read during that
count; everything after it — restoring the analysis, measuring channel ranges,
preparing tabs, drawing — reads wells from disk with nothing on screen.

The fixture shrinks the row cache to one byte so every well is evicted as soon
as it is read, which is the reporter's situation at the mini dataset's scale:
the work after the CSV count then reads wells from disk, and each of those
reads must be named on the status line.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _record_status(app) -> list[str]:
    lines: list[str] = []
    real = app._set_status

    def record(msg):
        lines.append(msg)
        return real(msg)

    app._set_status = record
    return lines


def test_the_status_line_follows_the_load_past_its_last_csv(viewer_app, mini_dataset):
    app = viewer_app
    app._load_path(mini_dataset)
    app._app_state.dataset.cache.set_budget(1)       # nothing stays held
    lines = _record_status(app)

    app._load_path(mini_dataset)

    n = len(app._app_state.dataset.well_paths)
    last_csv = max(i for i, line in enumerate(lines) if line.startswith(f"Loading {n}/{n}"))
    after = lines[last_csv + 1:]
    assert any(line.endswith("…") for line in after), "no phase was named after the CSV count"
    for line in after:
        if " — reading well " in line:
            assert line.split(" — reading well ")[0], f"a read was reported with no phase: {line}"
            k, of_n = line.rsplit("(", 1)[1].rstrip(")").split(" of ")
            assert int(of_n) == n and 1 <= int(k) <= n, f"the count left the plate: {line}"
    loaded = [i for i, line in enumerate(after) if line.startswith("Loaded ")]
    assert loaded, "the load never said it had finished"
    assert not any(" — reading well " in line for line in after[loaded[0]:]), (
        "'Loaded' was shown while the load was still reading wells")
    assert app._app_state.dataset.read_listener is None, "the load left its status listener installed"


def test_a_well_read_off_the_gui_thread_is_not_painted(viewer_app, mini_dataset):
    """The gating worker reads on its own thread; touching the status label
    from there would be a cross-thread widget call."""
    import threading

    from review.core.load_progress import LoadProgress

    app = viewer_app
    app._load_path(mini_dataset)
    lines = _record_status(app)
    with LoadProgress(app._ctx, 4) as progress:
        progress.phase("Testing")
        before = list(lines)
        t = threading.Thread(target=progress.well_read, args=("A01",))
        t.start()
        t.join()
        assert lines == before
        progress._last_paint = 0.0
        progress.well_read("A01")
        assert lines[-1] == "Testing — reading well A01 (1 of 4)"


def test_a_phase_that_walks_the_plate_twice_never_counts_past_it(viewer_app, mini_dataset):
    """Reported: "70 well reads so far, 40 wells in the plate" for a phase that
    read every well twice. A well read again starts a new pass, and the count
    restarts rather than passing the number of wells."""
    from review.core.load_progress import LoadProgress

    app = viewer_app
    app._load_path(mini_dataset)
    lines = _record_status(app)
    wells = ["A01", "A02", "B01", "B02"]
    with LoadProgress(app._ctx, len(wells)) as progress:
        progress.phase("Scanning")
        for label in wells + wells[:3]:
            progress._last_paint = 0.0           # paint every read
            progress.well_read(label)
    counts = [line.rsplit("(", 1)[1].rstrip(")") for line in lines if "reading well" in line]
    assert counts == ["1 of 4", "2 of 4", "3 of 4", "4 of 4", "1 of 4", "2 of 4", "3 of 4"]
