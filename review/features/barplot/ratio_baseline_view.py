"""The Bar tab's *Then relative to* row, inside its Fold change popover.

Appended below the shared fold-change controls by ``tab_view`` — the Bar tab's
own module, so the shared popover (``core/fold_change_controls_view``) neither
knows about it nor offers it on Line or Response, which do not apply it::

    Then relative to
    [Treatment ▾] = [Vehicle ▾]
    within each: [x] RNA

A **standing setting**, applied on every redraw (``_baseline_ratio``), not a
one-shot fill like the per-sample editor's *Pair*: it is stored as the rule on
``BarPlotState`` and recomputed from the plate's annotation each time, so
re-annotating the plate cannot leave it pointing at the wrong bars.

Choosing a value ticks the "within each" variables the baseline bars differ on
(``variable_pairing.default_within``) — for vehicle wells that sit at one dose
but span both RNAs, RNA is ticked and Dose is not. Whether the rule could be
applied, and why not, is written on the figure; the row only states the rule.

Two stacked lines so it fits the phone Controls sheet, which this popover
reaches through its ``bandRole="display"`` (``core/ui_helpers.make_popover_button``).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from review.domain import plot_members as _members
from review.domain import variable_pairing as _vp
from review.features.barplot.renderer import redraw_bars

#: The variable combo's "off" entry.
OFF_LABEL = "—"


def _slice(ctx):
    return ctx.state.feature_slice("bar")


def _vset(ctx):
    return getattr(ctx.state.selection, "variables", None)


def set_relative_to(ctx, variable_id: str, value_id: str, within) -> None:
    """Write the rule (empty ids turn it off) and redraw the bars."""
    slice_ = _slice(ctx)
    if slice_ is None:
        return
    slice_.rel_variable_id = str(variable_id or "")
    slice_.rel_value_id = str(value_id or "") if variable_id else ""
    slice_.rel_within = [str(v) for v in (within or [])] if variable_id else []
    redraw_bars(ctx)


def build_relative_to_row(ctx, parent: QWidget, layout) -> QWidget:
    """Build the row into *layout*. ``_aw_refresh`` re-reads variables and state."""
    box = QWidget(parent)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 6, 0, 0)
    outer.setSpacing(2)
    outer.addWidget(QLabel("Then relative to", box))

    line = QWidget(box)
    row = QHBoxLayout(line)
    row.setContentsMargins(0, 0, 0, 0)
    variable_cb = QComboBox(line)
    variable_cb.setObjectName("barRelVariable")
    variable_cb.setToolTip(
        "Divide each bar by the bar that matches it on the ticked variables "
        "but holds this value — e.g. Treatment = Vehicle. Needs a per-sample "
        "control; reference bars become the dotted line at 1.")
    row.addWidget(variable_cb)
    row.addWidget(QLabel("=", line))
    value_cb = QComboBox(line)
    value_cb.setObjectName("barRelValue")
    row.addWidget(value_cb)
    outer.addWidget(line)

    within_line = QWidget(box)
    within_row = QHBoxLayout(within_line)
    within_row.setContentsMargins(0, 0, 0, 0)
    within_row.addWidget(QLabel("within each:", within_line))
    checks_box = QWidget(within_line)
    checks_row = QHBoxLayout(checks_box)
    checks_row.setContentsMargins(0, 0, 0, 0)
    within_row.addWidget(checks_box)
    within_row.addStretch(1)
    outer.addWidget(within_line)
    layout.addWidget(box)

    checks: dict = {}
    built: dict = {"signature": None}

    def _wells():
        return _members.member_wells(ctx, "bar")

    def _signature():
        variables = _vp.annotating_variables(_vset(ctx), _wells())
        return tuple((v.id, v.name, tuple((x.id, x.label) for x in v.values))
                     for v in variables)

    def _fill_checks(ticked) -> None:
        while checks_row.count():
            widget = checks_row.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        checks.clear()
        variable_id = variable_cb.currentData() or ""
        for variable in _vp.annotating_variables(_vset(ctx), _wells()):
            if variable.id == variable_id:
                continue
            check = QCheckBox(variable.name, checks_box)
            check.setChecked(variable.id in ticked)
            check.toggled.connect(lambda _on: _write())
            checks_row.addWidget(check)
            checks[variable.id] = check
        within_line.setVisible(bool(variable_id))

    def _fill_values(selected: str) -> None:
        value_cb.blockSignals(True)
        try:
            value_cb.clear()
            vset = _vset(ctx)
            variable = vset.by_id(variable_cb.currentData() or "") if vset else None
            for value in (variable.values if variable is not None else ()):
                value_cb.addItem(variable.display_label(value.id), value.id)
            index = value_cb.findData(selected) if selected else -1
            value_cb.setCurrentIndex(index if index >= 0 else 0)
        finally:
            value_cb.blockSignals(False)
        value_cb.setEnabled(variable_cb.currentData() not in (None, ""))

    def _defaults() -> list:
        return list(_vp.default_within(_vset(ctx), _wells(),
                                       variable_cb.currentData() or "",
                                       value_cb.currentData() or ""))

    def _write() -> None:
        variable_id = variable_cb.currentData() or ""
        within = [vid for vid, check in checks.items() if check.isChecked()]
        set_relative_to(ctx, variable_id, value_cb.currentData() or "", within)

    def _on_variable() -> None:
        _fill_values("")
        _fill_checks(set(_defaults()))
        _write()

    def _on_value() -> None:
        _fill_checks(set(_defaults()))
        _write()

    def _refresh() -> None:
        slice_ = _slice(ctx)
        signature = _signature()
        if signature == built["signature"] and slice_ is not None and \
                (variable_cb.currentData() or "") == slice_.rel_variable_id:
            return
        built["signature"] = signature
        variable_cb.blockSignals(True)
        try:
            variable_cb.clear()
            variable_cb.addItem(OFF_LABEL, "")
            for variable in _vp.annotating_variables(_vset(ctx), _wells()):
                variable_cb.addItem(variable.name, variable.id)
            index = variable_cb.findData(slice_.rel_variable_id if slice_ else "")
            variable_cb.setCurrentIndex(index if index >= 0 else 0)
        finally:
            variable_cb.blockSignals(False)
        _fill_values(slice_.rel_value_id if slice_ else "")
        _fill_checks(set(slice_.rel_within if slice_ else ()))

    variable_cb.currentIndexChanged.connect(lambda *_: _on_variable())
    value_cb.currentIndexChanged.connect(lambda *_: _on_value())
    box._aw_refresh = _refresh
    ctx.widgets.register("_bar_rel_row", box)
    _refresh()
    return box
