"""The Segmentation tab's Delete-scope control.

A drag in Delete mode normally acts on the one frame on screen. Plate-scale
artifacts — a dust speck at a fixed stage position, a bright corner, a scratch on
the plate bottom — sit in the same image region across many wells, fields and
timepoints, and clearing those one frame at a time means repeating an identical
gesture hundreds of times.

This popover arms the next drag with a wider set of wells / FOVs / timepoints, so
one rectangle covers all of them. It writes nothing but
``ReviewImageState.delete_scope_*``; the delete itself is
``review_image/callbacks.py``.

The armed scope is legible without opening anything: the button's own text is the
readout ("Delete scope: this frame", "Delete scope: 3 wells · all FOVs · 2 TPs").
That is deliberate — a scope that stays armed across drags has to be visible on
the control row, or a later delete reaches further than the user remembers
asking for.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from well_viewer.core.ui_helpers import make_popover_button
from well_viewer.domain.viewer_state import parse_rc
from widgets.checkable_dropdown import CheckableDropdown

_SCOPE_BTN = "_review_delete_scope_btn"


def _current_frame(ctx) -> tuple[str, str, str]:
    """The well / FOV / timepoint the control row is showing.

    Normalised the same way the option lists and the delete's own scope
    resolution are, so the seeded tick actually lands on an offered option — a
    combo reading ``T01`` and an option list holding ``1`` are the same
    timepoint, and comparing them raw would leave the dropdown looking empty.
    """
    from well_viewer.data.image_identity import (
        normalize_fov, normalize_tp, normalize_well,
    )
    well = normalize_well(ctx.state.preview.selected_well or "")
    fov_menu = (ctx.runtime.review_image.fov_menu
                or ctx.widgets.get_or_none("_preview_fov_cb"))
    fov = normalize_fov(fov_menu.currentText()) if fov_menu is not None else ""
    tp_cb = ctx.runtime.review_image.tp_cb
    tp = normalize_tp(tp_cb.currentText()) if tp_cb is not None else ""
    return well, fov, tp


def _scope_options(ctx) -> tuple[list[str], list[str], list[str]]:
    """The pickable wells / FOVs / timepoints for the loaded dataset.

    Read fresh every time the popover opens rather than cached at build time —
    the tab is built lazily and can outlive several datasets, so a list captured
    once would offer the previous plate's wells.
    """
    from well_viewer.data.image_identity import normalize_fov, normalize_tp
    wells = sorted(ctx.state.dataset.well_paths.keys(), key=parse_rc)
    fovs = [normalize_fov(f) for f in (ctx.state.dataset.all_fovs_cache or [])]
    tps = [normalize_tp(t) for t in (ctx.state.dataset.all_timepoints_cache or [])]
    return wells, fovs, tps


def describe_delete_scope(ctx) -> str:
    """One-line readout of the armed scope, used as the button's label."""
    ri = ctx.state.review_image
    if (ri.delete_scope_wells is None and ri.delete_scope_fovs is None
            and ri.delete_scope_tps is None):
        return "Delete scope: this frame"

    def _part(values, noun: str, total: int) -> str:
        if values is None:
            return f"this {noun}"
        n = len(values)
        if n == 0:
            return f"no {noun}s"
        if total and n >= total:
            return f"all {noun}s"
        return f"{n} {noun}s" if n != 1 else f"1 {noun}"

    wells, fovs, tps = _scope_options(ctx)
    return "Delete scope: " + " · ".join((
        _part(ri.delete_scope_wells, "well", len(wells)),
        _part(ri.delete_scope_fovs, "FOV", len(fovs)),
        _part(ri.delete_scope_tps, "TP", len(tps)),
    ))


def refresh_delete_scope_button(ctx) -> None:
    """Re-label the scope button. Safe to call when the tab was never built."""
    btn = ctx.widgets.get_or_none(_SCOPE_BTN)
    if btn is None:
        return
    btn.setText(describe_delete_scope(ctx) + " ▾")


def _row(parent: QWidget, caption: str, dropdown: CheckableDropdown) -> QWidget:
    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lbl = QLabel(caption, row)
    lbl.setMinimumWidth(74)
    lay.addWidget(lbl)
    lay.addWidget(dropdown, 1)
    return row


def build_delete_scope_control(ctx, ctrl: QWidget):
    """Build the Delete-scope popover button for the Segmentation control band."""
    btn, panel, lay = make_popover_button("Delete scope: this frame", ctrl)
    btn.setToolTip(
        "Which frames the next drag-delete applies to.\n"
        "• On the displayed frame a cell goes if any of its pixels are in the "
        "box.\n"
        "• On every other frame in scope a cell goes if its centroid is in the "
        "box (its mask is not loaded, so the CSV's centroid decides).\n"
        "The scope stays armed until you reset it."
    )
    ctx.widgets.register(_SCOPE_BTN, btn)

    hint = QLabel("Apply a drag-delete to:", panel)
    hint.setObjectName("Muted")
    lay.addWidget(hint)

    wells_dd = CheckableDropdown(panel, noun="wells", empty_text="No wells")
    fovs_dd = CheckableDropdown(panel, noun="FOVs", empty_text="No FOVs")
    tps_dd = CheckableDropdown(panel, noun="timepoints", empty_text="No timepoints")
    lay.addWidget(_row(panel, "Wells", wells_dd))
    lay.addWidget(_row(panel, "FOVs", fovs_dd))
    lay.addWidget(_row(panel, "Timepoints", tps_dd))

    def _seed(dropdown: CheckableDropdown, options, armed, current) -> None:
        """Refill *dropdown* and restore its ticks without emitting ``changed``.

        When the field is un-armed (``None``) the current frame's value is shown
        ticked, so the popover reads as "this frame" rather than as an empty
        list — but the state stays ``None``, so nothing is armed until the user
        actually changes something.
        """
        blocked = dropdown.blockSignals(True)
        try:
            dropdown.clear()
            for opt in options:
                dropdown.addItem(str(opt), str(opt))
            dropdown.setCheckedData(
                [str(current)] if armed is None else [str(v) for v in armed]
            )
        finally:
            dropdown.blockSignals(blocked)

    def _repopulate() -> None:
        well, fov, tp = _current_frame(ctx)
        wells, fovs, tps = _scope_options(ctx)
        ri = ctx.state.review_image
        # An un-armed field seeds from the displayed frame; make sure that value
        # is offered even when the caches have not been rebuilt for it yet.
        _seed(wells_dd, wells or ([well] if well else []),
              ri.delete_scope_wells, well)
        _seed(fovs_dd, fovs or ([fov] if fov else []), ri.delete_scope_fovs, fov)
        _seed(tps_dd, tps or ([tp] if tp else []), ri.delete_scope_tps, tp)
        refresh_delete_scope_button(ctx)

    def _commit(field: str, dropdown: CheckableDropdown) -> None:
        """Arm one field from its dropdown, leaving the other two alone.

        Committing all three together would be the trap: ticking two wells would
        also pin the FOV and timepoint to whatever happened to be on screen, so
        navigating to the next FOV and dragging would delete nothing there and
        the user would have to work out why. A field the user never touched stays
        ``None`` and keeps tracking the displayed frame.
        """
        setattr(ctx.state.review_image, field,
                {str(v) for v in dropdown.checkedData()})
        refresh_delete_scope_button(ctx)

    for _field, _dd in (("delete_scope_wells", wells_dd),
                        ("delete_scope_fovs", fovs_dd),
                        ("delete_scope_tps", tps_dd)):
        _dd.changed.connect(partial(_commit, _field, _dd))

    plate_btn = QPushButton("Use plate selection", panel)
    plate_btn.setProperty("variant", "secondary")
    plate_btn.setToolTip(
        "Arm the wells currently selected on the plate. The plate selection "
        "itself is not changed."
    )

    def _use_plate_selection() -> None:
        # Read-only on the app-wide selection: writing it here would leak this
        # tab's scope into every other tab (see the plate-selection note in
        # ARCHITECTURE §15).
        chosen = [w for w in ctx.state.selection.selected_wells
                  if w in ctx.state.dataset.well_paths]
        if not chosen:
            ctx.notify.set_status(
                "Delete scope: no wells are selected on the plate."
            )
            return
        ctx.state.review_image.delete_scope_wells = set(chosen)
        _repopulate()
        ctx.notify.set_status(
            f"Delete scope: armed {len(chosen)} well(s) from the plate selection."
        )

    plate_btn.clicked.connect(lambda _=False: _use_plate_selection())
    lay.addWidget(plate_btn)

    reset_btn = QPushButton("Reset to this frame", panel)
    reset_btn.setProperty("variant", "secondary")
    reset_btn.setToolTip(
        "Disarm the scope: a drag-delete goes back to acting on the displayed "
        "frame only."
    )

    def _reset() -> None:
        ctx.state.review_image.reset_delete_scope()
        _repopulate()
        ctx.notify.set_status("Delete scope: this frame only.")

    reset_btn.clicked.connect(lambda _=False: _reset())
    lay.addWidget(reset_btn)

    menu = btn.menu()
    if menu is not None:
        menu.aboutToShow.connect(_repopulate)
    return btn
