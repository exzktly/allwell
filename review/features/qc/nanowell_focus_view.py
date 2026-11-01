"""The Cell Gating panel's nanowell focus row: **Exclude out-of-focus nanowells**, its percentage, and what it did.

The switch is the QC layer's (``QCParams.exclude_soft_nanowells`` / ``nanowell_focus_fraction``), shown here
beside the other inclusion layers and mirrored on the Nanowells page's band; ticking either changes both, because
both write the same parameters through ``set_qc_params_with_progress``. The verdict is
:mod:`review.data.nanowell_focus_gate`'s ``NW_Focus_Pass``.

**Shown only on a dataset with nanowell tables**, and on such a dataset the per-cell focus row is hidden unless the
plate also holds ordinary wells (a Merge Plates result): a nanowell run records no per-cell focus, so that row could
only say "nothing to exclude" and send the user to re-run a plate for a measurement a nanowell run never makes.
One live focus switch per kind of well, never two.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from review.data.nanowell_focus_gate import nanowell_dir, nanowell_focus_status
from review.domain import nanowell_rim_focus as rfc
from widgets.spin_box import SpinBox

TOOLTIP = (
    "Drop the cells of a nanowell at any timepoint where its own rim was out of focus — its tile was soft while "
    "others in the well were sharp.\n"
    "A nanowell is out of focus when its rim is less sharp than this percentage of the sharpest tenth of its "
    "well's rims at that timepoint. Every nanowell run measures each nanowell's rim, so this needs no number from "
    "you and no re-run.\n"
    "It applies everywhere: the Nanowells page draws those nanowell-timepoints as gaps, and their cells leave "
    "Plotting, Scatter, histograms and exports.\n"
    "Not seen: a timepoint blurred evenly everywhere (nothing sharp to compare with), and a nanowell whose rim "
    "could not be fitted (kept, and counted below).\n"
    "On by default for a nanowell run.")


def is_nanowell_dataset(ctx) -> bool:
    """Whether the loaded dataset has nanowell tables for this row to judge."""
    return nanowell_dir(getattr(ctx.state.dataset, "data_dir", None)) is not None


def shows_per_cell_focus(ctx) -> bool:
    """Whether the per-cell focus row belongs on the panel: on any dataset with ordinary wells — every dataset
    without nanowell tables, and a merged plate whose sidecar says it is partly ordinary."""
    if not is_nanowell_dataset(ctx):
        return True
    from domain.nanowell_sidecar import is_partly_ordinary

    info = getattr(ctx.state.dataset, "pipeline_info", None) or {}
    block = info.get("nanowell") if isinstance(info, dict) else None
    return isinstance(block, dict) and is_partly_ordinary(block)


def build_row(parent: QWidget, params, indent: int, text_width: int) -> tuple[QWidget, dict]:
    """The row's widget and the widgets the panel registers (``exclude_soft_nanowells``, ``nanowell_focus_fraction``,
    ``nanowell_focus_status``). Wiring is the panel's: it applies every control through one ``_apply``."""
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 6, 0, 0)
    layout.setSpacing(2)
    # The switch on its own line and its percentage on the next, indented under the switch's text: on one line the
    # row asked for about 510 px, and the Cell Gating panel is often narrower, so the layout squeezed every part of
    # it — the checkbox's text, the sentence and the number in the box.
    box = QCheckBox("Exclude out-of-focus nanowells", row)
    box.setChecked(bool(params.exclude_soft_nanowells))
    box.setToolTip(TOOLTIP)
    layout.addWidget(box)
    head = QWidget(row)
    head_layout = QHBoxLayout(head)
    head_layout.setContentsMargins(indent, 0, 0, 0)
    below = QLabel("rim below", head)
    below.setObjectName("Muted")
    head_layout.addWidget(below)
    spin = SpinBox(head)
    spin.setRange(int(round(100 * rfc.MIN_FRACTION)), int(round(100 * rfc.MAX_FRACTION)))
    # A bare number, no suffix: Qt's spin box refuses typing and Backspace while the cursor sits in a suffix, so a
    # click after the number ignored every digit typed. The labels either side say what the number is.
    spin.setValue(int(round(100 * params.nanowell_focus_fraction)))
    spin.commit_after_pause()                # Enter and focus-out commit; so does a pause: a click on a canvas ends no edit
    spin.setToolTip(TOOLTIP)
    head_layout.addWidget(spin)
    of = QLabel("of the well's sharpest rims", head)
    of.setObjectName("Muted")
    of.setWordWrap(True)                     # wraps under itself in a narrow panel, never squeezes the number
    head_layout.addWidget(of, 1)
    head_layout.addStretch(1)
    layout.addWidget(head)
    status = QLabel("", row)
    status.setObjectName("Muted")
    status.setWordWrap(True)
    status.setMaximumWidth(text_width)
    status.setIndent(indent)
    layout.addWidget(status)
    return row, {"exclude_soft_nanowells": box, "nanowell_focus_fraction": spin,
                 "nanowell_focus_status": status, "nanowell_focus_row": row}


#: What the status line says while :func:`refresh` counts on its worker.
COUNTING = "Checking each nanowell's rim focus across the plate…"


def refresh(ctx, w: dict) -> None:
    """Show the row on a nanowell dataset only, hide the per-cell focus row where it has nothing to judge, and
    re-read the plate-wide sentence. Shared by the builder and ``qc_gate_refresh_widgets``, so the panel says the
    same thing built and refilled.

    The sentence is counted on a worker thread (:func:`_count_off_thread`): it judges every well's site table and
    reads every well's cells, and this runs inside the dataset load (``load_controller`` refreshes the panel after
    hydrating the QC parameters), where doing it on the GUI thread froze the window for a minute on a large plate.
    Until it arrives the line says :data:`COUNTING` rather than keeping the previous dataset's numbers."""
    row, label = w.get("nanowell_focus_row"), w.get("nanowell_focus_status")
    nanowell = is_nanowell_dataset(ctx)
    if row is not None:
        row.setVisible(nanowell)
    per_cell = w.get("focus_row")
    if per_cell is not None:
        per_cell.setVisible(shows_per_cell_focus(ctx))
    spin, box = w.get("nanowell_focus_fraction"), w.get("exclude_soft_nanowells")
    if spin is not None and box is not None:
        spin.setEnabled(box.isChecked())
    if label is None or not nanowell:
        return
    _count_off_thread(ctx, label)


def _show(label, text: str, is_problem: bool) -> None:
    label.setText(text)
    label.setObjectName("Notice" if is_problem else "Muted")
    label.style().unpolish(label)
    label.style().polish(label)


def _count_off_thread(ctx, label) -> None:
    """Count :func:`nanowell_focus_status` on a worker and write it onto *label* on the GUI thread.

    Each call supersedes the one before: only the newest request's answer is written, so a refresh made while an
    older count runs (a second refresh in the same load, a fraction typed while counting) is never overwritten by
    the stale one. Nothing is written once the label is gone. The bridge is kept on the label, as
    ``run_off_thread`` requires a reference for as long as the call runs."""
    import shiboken6

    from core.safe import swallow
    from core.worker_bridge import run_off_thread

    request = getattr(label, "_aw_count_request", 0) + 1
    label._aw_count_request = request
    _show(label, COUNTING, False)

    def current() -> bool:
        return shiboken6.isValid(label) and getattr(label, "_aw_count_request", None) == request

    def done(result) -> None:
        if current():
            with swallow("Cell Gating: show the nanowell focus sentence"):
                _show(label, *result)

    def failed(message: str) -> None:
        if current():
            with swallow("Cell Gating: show that the nanowell focus count failed"):
                _show(label, f"The nanowell focus check could not be counted: {message}", True)

    label._aw_count_bridge = run_off_thread(lambda: nanowell_focus_status(ctx), on_done=done, on_failed=failed)
