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

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from review.data.nanowell_focus_gate import nanowell_dir, nanowell_focus_status
from review.domain import nanowell_rim_focus as rfc

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
    head = QWidget(row)
    head_layout = QHBoxLayout(head)
    head_layout.setContentsMargins(0, 0, 0, 0)
    box = QCheckBox("Exclude out-of-focus nanowells", head)
    box.setChecked(bool(params.exclude_soft_nanowells))
    box.setToolTip(TOOLTIP)
    head_layout.addWidget(box)
    head_layout.addSpacing(8)
    below = QLabel("rim below", head)
    below.setObjectName("Muted")
    head_layout.addWidget(below)
    spin = QSpinBox(head)
    spin.setRange(int(round(100 * rfc.MIN_FRACTION)), int(round(100 * rfc.MAX_FRACTION)))
    spin.setSuffix(" %")
    spin.setValue(int(round(100 * params.nanowell_focus_fraction)))
    spin.setKeyboardTracking(False)          # one recompute on Enter or focus-out, not one per digit
    spin.setToolTip(TOOLTIP)
    head_layout.addWidget(spin)
    of = QLabel("of the well's sharpest rims", head)
    of.setObjectName("Muted")
    head_layout.addWidget(of)
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


def refresh(ctx, w: dict) -> None:
    """Show the row on a nanowell dataset only, hide the per-cell focus row where it has nothing to judge, and
    re-read the plate-wide sentence. Shared by the builder and ``qc_gate_refresh_widgets``, so the panel says the
    same thing built and refilled."""
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
    text, is_problem = nanowell_focus_status(ctx)
    label.setText(text)
    label.setObjectName("Notice" if is_problem else "Muted")
    label.style().unpolish(label)
    label.style().polish(label)
