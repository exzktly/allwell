"""Batch Export's group editor: choosing members by sample variable.

The plate map picks wells by position. This slice of the editor picks them by
what the Variables tab says they *are* — every well holding ``Dose = 10 µM`` —
and adds them to, or removes them from, the active export group. Quick Setup
gains the matching whole-plate action: one group per value of a variable.

The decisions about which replicate sets join, which are split and which wells
are unassigned are Qt-free and live in :mod:`._variable_groups`; this mixin
only reads the plate's variables, drives the two combos and says what each
action did on the status line beneath them. A selection that could not be
taken exactly as asked — a replicate set split because its wells disagree, a
removal that had to keep a set, wells with no value — is stated there rather
than left for the exported figures to reveal.

Mixed into :class:`base_panel.BatchExportPanel`, which owns ``_groups``,
``_active_grp``, ``_refresh_group_list`` and the auto-name bookkeeping this
reads and writes. Names carry a ``_vsel_`` prefix so they cannot collide with
the Response panel's own variable combos.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMenu, QVBoxLayout

from review.core.lazy import lazy as _lazy
from review.features.batch_export._common import BarGroup, btn_secondary
from review.features.batch_export._variable_groups import (
    add_membership,
    groups_per_value,
    membership_for_value,
    remove_membership,
)

# The same roster the plate drag reads (``base_panel._rep_sets_loaded``), so a
# set chosen here is the set a drag would have pulled in.
_rep_sets_loaded = _lazy("review.domain.plot_members", "plot_replicate_sets_loaded")

#: Item data for the value combo's "no value" entry. A string rather than
#: ``None`` because ``QComboBox.findData(None)`` matches nothing.
_UNASSIGNED = "\x00unassigned"

_VSEL_COMBO_MIN_W = 110


def _well_order_key(tok: str):
    return (tok[:1], int(tok[1:]) if tok[1:].isdigit() else 0)


class VariableSelectMixin:
    """The "By variable" row and the per-value Quick Setup entry."""

    # ── data ─────────────────────────────────────────────────────────────
    def _vsel_variable_set(self):
        return getattr(self._ctx.state.selection, "variables", None)

    def _vsel_loaded_wells(self) -> list[str]:
        return sorted(self._ctx.state.dataset.well_paths or {}, key=_well_order_key)

    def _vsel_rep_sets(self) -> list:
        return list(_rep_sets_loaded(self._ctx))

    def _vsel_variables(self) -> list:
        vset = self._vsel_variable_set()
        return list(getattr(vset, "variables", ()) or ())

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_variable_select_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(8, 2, 8, 2)
        row.addWidget(QLabel("By variable:"))
        self._vsel_var_cb = QComboBox()
        self._vsel_var_cb.setMinimumWidth(_VSEL_COMBO_MIN_W)
        self._vsel_var_cb.setToolTip("A sample variable from Home → Variables")
        self._vsel_val_cb = QComboBox()
        self._vsel_val_cb.setMinimumWidth(_VSEL_COMBO_MIN_W)
        self._vsel_val_cb.setToolTip("Every loaded well holding this value")
        row.addWidget(self._vsel_var_cb, 1)
        row.addWidget(self._vsel_val_cb, 1)
        self._vsel_add_btn = btn_secondary(None, "Add", self._vsel_add)
        self._vsel_add_btn.setToolTip(
            "Add every well holding this value to the selected group")
        self._vsel_remove_btn = btn_secondary(None, "Remove", self._vsel_remove)
        self._vsel_remove_btn.setToolTip(
            "Take every well holding this value out of the selected group")
        row.addWidget(self._vsel_add_btn)
        row.addWidget(self._vsel_remove_btn)
        layout.addLayout(row)
        self._vsel_status = QLabel("")
        self._vsel_status.setWordWrap(True)
        self._vsel_status.setContentsMargins(8, 0, 8, 2)
        layout.addWidget(self._vsel_status)
        self._vsel_var_cb.currentIndexChanged.connect(
            lambda _i: self._vsel_refresh_values())
        self._vsel_refresh_variables()

    def _vsel_refresh_variables(self) -> None:
        """Repopulate the variable combo, keeping the pick by **id**.

        A name is renamed and an index moves when another variable is defined;
        either would re-point the row at a different variable while the combo
        went on reading the same word.
        """
        combo = getattr(self, "_vsel_var_cb", None)
        if combo is None:
            return
        previous = combo.currentData()
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for variable in self._vsel_variables():
                combo.addItem(variable.name, variable.id)
            idx = combo.findData(previous) if previous is not None else -1
            combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
        finally:
            combo.blockSignals(blocked)
        has_any = combo.count() > 0
        combo.setEnabled(has_any)
        if not has_any:
            combo.setPlaceholderText("No sample variables")
        self._vsel_refresh_values()

    def _vsel_refresh_values(self) -> None:
        combo = getattr(self, "_vsel_val_cb", None)
        if combo is None:
            return
        previous = combo.currentData()
        vset = self._vsel_variable_set()
        variable = vset.by_id(self._vsel_var_cb.currentData()) if vset else None
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            if variable is not None:
                for value in variable.values:
                    combo.addItem(variable.display_label(value.id) or value.label,
                                  value.id)
                combo.addItem("(no value)", _UNASSIGNED)
            idx = combo.findData(previous) if previous is not None else -1
            combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
        finally:
            combo.blockSignals(blocked)
        on = variable is not None
        for w in (combo, self._vsel_add_btn, self._vsel_remove_btn):
            w.setEnabled(on)

    def _vsel_say(self, text: str) -> None:
        label = getattr(self, "_vsel_status", None)
        if label is not None:
            label.setText(text)

    # ── actions ──────────────────────────────────────────────────────────
    def _vsel_current(self) -> tuple | None:
        """``(variable, value_id or None, label)`` for the row, or ``None``."""
        vset = self._vsel_variable_set()
        variable = vset.by_id(self._vsel_var_cb.currentData()) if vset else None
        data = self._vsel_val_cb.currentData()
        if variable is None or data is None:
            return None
        value_id = None if data == _UNASSIGNED else data
        return variable, value_id, f"{variable.name} = {self._vsel_val_cb.currentText()}"

    def _vsel_membership(self):
        picked = self._vsel_current()
        if picked is None:
            return None, ""
        variable, value_id, label = picked
        membership = membership_for_value(
            self._vsel_variable_set(), variable.id, value_id,
            self._vsel_loaded_wells(), self._vsel_rep_sets())
        return membership, label

    def _vsel_add(self) -> None:
        grp = self._active_group()
        if grp is None:
            self._vsel_say("Select or add a group first.")
            return
        membership, label = self._vsel_membership()
        if membership is None:
            return
        if not membership.wells:
            self._vsel_say(f"No loaded well holds {label}.")
            return
        gained = add_membership(grp, membership)
        self._refresh_auto_group_name(grp)
        self._refresh_group_list()
        msg = (f"{label}: added {gained} well{'s' if gained != 1 else ''} "
               f"to “{grp.name}”")
        if gained < len(membership.wells):
            msg += f" ({len(membership.wells) - gained} already in it)"
        msg += "."
        if membership.split_sets:
            msg += (" Split, because their wells hold different values: "
                    + ", ".join(membership.split_sets)
                    + " — only the matching wells were added, as solo wells.")
        self._vsel_say(msg)

    def _vsel_remove(self) -> None:
        grp = self._active_group()
        if grp is None:
            self._vsel_say("Select a group first.")
            return
        membership, label = self._vsel_membership()
        if membership is None:
            return
        removed, kept = remove_membership(grp, membership)
        self._refresh_auto_group_name(grp)
        self._refresh_group_list()
        msg = (f"{label}: removed {removed} well{'s' if removed != 1 else ''} "
               f"from “{grp.name}”.")
        if kept:
            msg += (" Kept, because they also hold other values: "
                    + ", ".join(kept) + " — drag them off the plate to remove them.")
        self._vsel_say(msg)

    # ── Quick Setup ──────────────────────────────────────────────────────
    def _vsel_add_quick_setup_menu(self, menu: QMenu) -> None:
        """Add "One group per value of ▸" to Quick Setup, filled on open.

        Filled when the menu opens rather than when it is built, because the
        variables arrive with a dataset that is routinely opened after this
        panel exists.
        """
        # Built here and handed to the parent, not made by ``addMenu(str)``: a
        # menu Qt creates on its own has a Python wrapper that does not learn
        # of its deletion with the parent, and deleting it again crashes.
        sub = QMenu("One group per value of…", menu)
        menu.addMenu(sub)
        self._vsel_quick_menu = sub

        def _fill() -> None:
            sub.clear()
            variables = self._vsel_variables()
            if not variables:
                act = sub.addAction("No sample variables (Home → Variables)")
                act.setEnabled(False)
                return
            for variable in variables:
                sub.addAction(variable.name,
                              lambda vid=variable.id: self._quick_by_variable(vid))

        sub.aboutToShow.connect(_fill)
        _fill()

    def _quick_by_variable(self, variable_id: str) -> None:
        """Replace every group with one per value of *variable_id*."""
        vset = self._vsel_variable_set()
        variable = vset.by_id(variable_id) if vset else None
        if variable is None:
            return
        groups, unassigned = groups_per_value(
            vset, variable_id, self._vsel_loaded_wells(), self._vsel_rep_sets())
        self._groups.clear()
        self._auto_named_group_ids.clear()
        split: list[str] = []
        for vg in groups:
            m = vg.membership
            self._groups.append(BarGroup(vg.name, members=list(m.members),
                                         solo_wells=list(m.solo_wells)))
            split.extend(s for s in m.split_sets if s not in split)
        self._active_grp = 0 if self._groups else -1
        self._refresh_group_list()
        msg = f"{len(groups)} group{'s' if len(groups) != 1 else ''} from {variable.name}."
        if unassigned:
            n = len(unassigned)
            msg += (f" {n} well{'s' if n != 1 else ''} with no {variable.name} "
                    f"left out: {', '.join(unassigned)}.")
        if split:
            msg += (" Split, because their wells hold different values: "
                    + ", ".join(split) + ".")
        self._vsel_say(msg)
