"""Wait for the Cell Gating panel's background jobs, as a user waits for the popup.

Every edit on the panel now runs its work on a thread behind a progress popup
(``review/core/progress_jobs.py``) and finishes on the GUI thread once that
thread has returned. A test that presses a control and then reads what it did
has to let the event loop deliver that finish, exactly as the real window does
while the popup is up — reading straight after the press would read the frames
before the job has rewritten them.
"""

from __future__ import annotations


def wait_for_panel_jobs(qtbot, ctx, *, timeout: int = 10_000) -> None:
    """Spin the event loop until no panel job is running or waiting."""
    from review.core import progress_jobs

    qtbot.waitUntil(lambda: not progress_jobs.busy(ctx), timeout=timeout)


def apply_qc_params(qtbot, ctx, params) -> None:
    """Apply *params* the way the QC panel does, and wait for it to finish.

    ``qc.controller.set_qc_params_with_progress`` is the one route a threshold
    edit takes in the app: the re-projection runs on the job thread behind the
    progress popup and the save, the plate's markers and the redraw follow on the
    GUI thread.
    """
    from review.features.qc.controller import set_qc_params_with_progress

    set_qc_params_with_progress(ctx, params)
    wait_for_panel_jobs(qtbot, ctx)
