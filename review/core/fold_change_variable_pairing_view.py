"""The per-sample control editor's "Pair by variable" row.

One line under the pairing table that states the rule the way a person says it:
*Control: [Compound] = [DMSO], within each: [x] Cell line [ ] Dose  [Pair]*.
Pressing **Pair** runs ``domain/variable_pairing.pair_by_variable`` over the
plotted members and writes the result into the same ``fc_control_map`` every
per-sample surface reads, so the table, the plot, the CSV's
``fold_change_outcome`` column and the status line all describe the same
mapping. The sentence under the row says how many members were paired and
names each one that was not, with the reason — the table alone would show those
rows as "no control set", which is true and does not say why.

It is a one-shot fill, like *Auto-pair*: the mapping is written once and is then
the user's to edit row by row. Re-annotating the plate afterwards does not
re-pair it; pressing **Pair** again does.

Split from ``fold_change_pairing_view`` because the row has its own widget
state (the reference choice and the ticked variables) that the table does not
share, and its refresh rule is different: the table rebuilds on every state
sync, while the row rebuilds only when the plate's variables change, so a
sync never discards a half-made choice.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from review.domain import plot_members as _members
from review.domain import variable_pairing as _vp


def _vset(ctx):
    return getattr(ctx.state.selection, "variables", None)


def addressable_member_refs(ctx, scope: str) -> dict:
    """``member key -> ControlRef`` for the members a control can point at.

    ``member_control_refs`` addresses every member by its own kind, and a cell
    of a Response *Group by* cross is a set by name that resolves to no wells:
    choosing it as a control would show "control not found" with nothing to
    say why. Those are left out here, so the rule reports them as
    ``UNADDRESSABLE`` instead.
    """
    from review.core.fold_change_pairing_view import member_control_refs
    from review.domain.fold_change import resolve_control_wells

    return {key: ref for key, ref in member_control_refs(ctx, scope).items()
            if resolve_control_wells(ctx, ref)}


def run_variable_pairing(ctx, scope: str, variable_id: str, value_id: str,
                         within) -> _vp.VariablePairing:
    """Pair *scope*'s plotted members by the rule, and write the mapping."""
    from review.core.fold_change_controls_view import set_fold_change_state

    pairing = _vp.pair_by_variable(
        _vset(ctx), _members.member_wells(ctx, scope),
        addressable_member_refs(ctx, scope),
        variable_id=variable_id, value_id=value_id, within=within)
    current = dict(getattr(ctx.state.plot, "fc_control_map", {}) or {})
    set_fold_change_state(ctx, control_map=_vp.apply_pairing(current, pairing),
                          initiating_scope="")
    return pairing


def _variables_signature(ctx, scope: str) -> tuple:
    """What the row's choices are built from; it rebuilds only when this moves."""
    variables = _vp.annotating_variables(_vset(ctx), _members.member_wells(ctx, scope))
    return tuple((v.id, v.name, tuple((x.id, x.label) for x in v.values))
                 for v in variables)


def build_variable_pairing_row(ctx, scope: str, parent: QWidget) -> QWidget:
    """The row and its result sentence. ``_aw_refresh`` re-reads the variables."""
    box = QWidget(parent)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 0)

    row = QWidget(box)
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.addWidget(QLabel("Pair by variable — control:", row))
    variable_cb = QComboBox(row)
    variable_cb.setObjectName("fcPairVariable")
    line.addWidget(variable_cb)
    line.addWidget(QLabel("=", row))
    value_cb = QComboBox(row)
    value_cb.setObjectName("fcPairValue")
    line.addWidget(value_cb)
    line.addWidget(QLabel("within each:", row))
    within_box = QWidget(row)
    within_line = QHBoxLayout(within_box)
    within_line.setContentsMargins(0, 0, 0, 0)
    line.addWidget(within_box)
    line.addStretch(1)
    pair_btn = QPushButton("Pair", row)
    pair_btn.setObjectName("fcPairByVariable")
    line.addWidget(pair_btn)
    outer.addWidget(row)

    result = QLabel("", box)
    result.setObjectName("Muted")
    result.setWordWrap(True)
    result.setVisible(False)
    outer.addWidget(result)

    empty = QLabel("Pair by variable: define a sample variable on "
                   "Home → Variables to pair by condition.", box)
    empty.setObjectName("Muted")
    empty.setWordWrap(True)
    outer.addWidget(empty)

    checks: dict[str, QCheckBox] = {}
    built: dict = {"signature": None, "written": None}

    def _fill_within() -> None:
        while within_line.count():
            item = within_line.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Detached now, not only when the deferred delete runs: until
                # then it is still a child of the row, still painted, and
                # still what ``_on_pair`` would have to skip.
                widget.setParent(None)
                widget.deleteLater()
        checks.clear()
        variable_id = variable_cb.currentData() or ""
        value_id = value_cb.currentData() or ""
        wells = _members.member_wells(ctx, scope)
        defaults = set(_vp.default_within(_vset(ctx), wells, variable_id, value_id))
        for variable in _vp.annotating_variables(_vset(ctx), wells):
            if variable.id == variable_id:
                continue
            check = QCheckBox(variable.name, within_box)
            check.setChecked(variable.id in defaults)
            within_line.addWidget(check)
            checks[variable.id] = check

    def _fill_values() -> None:
        value_cb.blockSignals(True)
        try:
            value_cb.clear()
            vset = _vset(ctx)
            variable = vset.by_id(variable_cb.currentData() or "") if vset else None
            for value in (variable.values if variable is not None else ()):
                value_cb.addItem(variable.display_label(value.id), value.id)
        finally:
            value_cb.blockSignals(False)
        _fill_within()

    def _refresh() -> None:
        # The sentence describes one press of Pair. Once the mapping has been
        # edited since, it describes a table that is no longer on screen.
        current = dict(getattr(ctx.state.plot, "fc_control_map", {}) or {})
        if built.get("written") is not None and current != built["written"]:
            built["written"] = None
            result.setVisible(False)
        signature = _variables_signature(ctx, scope)
        if signature == built["signature"]:
            return
        built["signature"] = signature
        keep = variable_cb.currentData()
        variable_cb.blockSignals(True)
        try:
            variable_cb.clear()
            for variable in _vp.annotating_variables(
                    _vset(ctx), _members.member_wells(ctx, scope)):
                variable_cb.addItem(variable.name, variable.id)
            index = variable_cb.findData(keep) if keep else -1
            variable_cb.setCurrentIndex(index if index >= 0 else 0)
        finally:
            variable_cb.blockSignals(False)
        _fill_values()
        has_any = variable_cb.count() > 0
        row.setVisible(has_any)
        empty.setVisible(not has_any)

    def _on_pair() -> None:
        variable_id = variable_cb.currentData() or ""
        value_id = value_cb.currentData() or ""
        if not variable_id or not value_id:
            return
        within = [vid for vid, check in checks.items() if check.isChecked()]
        pairing = run_variable_pairing(ctx, scope, variable_id, value_id, within)
        built["written"] = dict(getattr(ctx.state.plot, "fc_control_map", {}) or {})
        result.setText(_vp.summary_text(pairing))
        result.setVisible(True)

    variable_cb.currentIndexChanged.connect(lambda *_: _fill_values())
    value_cb.currentIndexChanged.connect(lambda *_: _fill_within())
    pair_btn.clicked.connect(_on_pair)
    box._aw_refresh = _refresh
    box._aw_result = result
    _refresh()
    return box
