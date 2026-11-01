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
removal that had to keep a set, wells with no value — is stated there, on a
warning line of its own, rather than left for the exported figures to reveal.

**A group built from values is named for them.** A new group is auto-named
from its members, which for a group filled by ``Cell line = HeLa`` is a list of
twenty well tokens — and that name is the exported figure's title. So the
first value added to an auto-named group names it (``HeLa``), a second extends
the name (``HeLa + U2OS``), removing a value drops it from the name, and a
later plate drag leaves the name alone, because the group is no longer one the
app named from its wells. Renaming by hand ends all of that
(``_vsel_forget_name``, called from ``_grp_rename``).

Mixed into :class:`base_panel.BatchExportPanel`, which owns ``_groups``,
``_active_grp``, ``_refresh_group_list`` and the auto-name bookkeeping this
reads and writes. Names carry a ``_vsel_`` prefix so they cannot collide with
the Response panel's own variable combos.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from review.core.lazy import lazy as _lazy
from review.features.batch_export._common import BarGroup, btn_secondary
from review.features.batch_export._variable_groups import (
    add_membership,
    groups_per_value,
    membership_for_value,
    remove_membership,
)
from ui import theme

# The same roster the plate drag reads (``base_panel._rep_sets_loaded``), so a
# set chosen here is the set a drag would have pulled in.
_rep_sets_loaded = _lazy("review.domain.plot_members", "plot_replicate_sets_loaded")

#: Item data for the value combo's "not set" entry. A string rather than
#: ``None`` because ``QComboBox.findData(None)`` matches nothing.
_UNASSIGNED = "\x00unassigned"

#: What the row says, and the Quick Setup submenu says, on a plate with no
#: sample variables — one wording for both, naming where they are defined.
NO_VARIABLES_TEXT = "No sample variables — define them in Home → Sample Map → Variables."

#: How many wells a status line names before it says "and N more".
_MAX_NAMED_WELLS = 8

_VSEL_COMBO_MIN_W = 90


def _well_order_key(tok: str):
    return (tok[:1], int(tok[1:]) if tok[1:].isdigit() else 0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _wells_list(wells) -> str:
    wells = list(wells)
    shown = ", ".join(wells[:_MAX_NAMED_WELLS])
    more = len(wells) - _MAX_NAMED_WELLS
    return f"{shown} and {more} more" if more > 0 else shown


class VariableSelectMixin:
    """The "Select by variable" row and the per-value Quick Setup entry."""

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

    def _vsel_value_names(self) -> dict:
        """``{id(group): [value label, …]}`` for groups named by their values."""
        names = getattr(self, "_vsel_named_groups", None)
        if names is None:
            names = self._vsel_named_groups = {}
        return names

    def _vsel_forget_name(self, grp) -> None:
        """*grp* was renamed by hand: stop naming it after its values."""
        self._vsel_value_names().pop(id(grp), None)

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_variable_select_row(self, layout: QVBoxLayout) -> None:
        # The controls sit in one container so the empty state can replace them
        # with a sentence, rather than leaving dead combos that look live.
        self._vsel_controls = QWidget()
        row = QHBoxLayout(self._vsel_controls)
        row.setContentsMargins(8, 2, 8, 2)
        row.addWidget(QLabel("Select by variable:"))
        self._vsel_var_cb = QComboBox()
        self._vsel_var_cb.setMinimumWidth(_VSEL_COMBO_MIN_W)
        self._vsel_var_cb.setToolTip("A sample variable from Home → Sample Map → Variables")
        self._vsel_val_cb = QComboBox()
        self._vsel_val_cb.setMinimumWidth(_VSEL_COMBO_MIN_W)
        self._vsel_val_cb.setToolTip("Every loaded well holding this value")
        row.addWidget(self._vsel_var_cb, 1)
        row.addWidget(self._vsel_val_cb, 1)
        layout.addWidget(self._vsel_controls)

        # "Add wells", not "Add": the header's "+ Add" a few centimetres away
        # makes a group, and two buttons reading the same word must not do
        # different things.
        btns = QHBoxLayout()
        btns.setContentsMargins(8, 0, 8, 2)
        btns.addStretch(1)
        self._vsel_add_btn = btn_secondary(None, "Add wells", self._vsel_add)
        self._vsel_add_btn.setToolTip(
            "Add every well holding this value to the selected group "
            "(or to a new group named after the value, if there is none)")
        self._vsel_remove_btn = btn_secondary(None, "Remove wells", self._vsel_remove)
        self._vsel_remove_btn.setToolTip(
            "Take every well holding this value out of the selected group")
        btns.addWidget(self._vsel_add_btn)
        btns.addWidget(self._vsel_remove_btn)
        self._vsel_buttons = QWidget()
        self._vsel_buttons.setLayout(btns)
        layout.addWidget(self._vsel_buttons)

        self._vsel_empty = QLabel(NO_VARIABLES_TEXT)
        self._vsel_empty.setWordWrap(True)
        self._vsel_empty.setContentsMargins(8, 2, 8, 2)
        self._vsel_empty.setStyleSheet(f"color: {theme.Colors.text_muted};")
        layout.addWidget(self._vsel_empty)

        self._vsel_status = QLabel("")
        self._vsel_status.setWordWrap(True)
        self._vsel_status.setTextFormat(Qt.RichText)
        self._vsel_status.setContentsMargins(8, 0, 8, 2)
        self._vsel_status.hide()
        layout.addWidget(self._vsel_status)
        self._vsel_var_cb.currentIndexChanged.connect(
            lambda _i: self._vsel_refresh_values())
        self._vsel_refresh_variables()

    def _vsel_refresh_variables(self) -> None:
        """Repopulate the variable combo, keeping the pick by **id**.

        A name is renamed and an index moves when another variable is defined;
        either would re-point the row at a different variable while the combo
        went on reading the same word. A status line describing the previous
        variables is cleared when they change, since it would describe a plate
        that is no longer on screen.
        """
        combo = getattr(self, "_vsel_var_cb", None)
        if combo is None:
            return
        vset = self._vsel_variable_set()
        if vset is not getattr(self, "_vsel_seen_vset", None):
            self._vsel_seen_vset = vset
            self._vsel_say("")
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
        self._vsel_controls.setVisible(has_any)
        self._vsel_buttons.setVisible(has_any)
        self._vsel_empty.setVisible(not has_any)
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
                combo.addItem("(not set)", _UNASSIGNED)
            idx = combo.findData(previous) if previous is not None else -1
            combo.setCurrentIndex(idx if idx >= 0 else (0 if combo.count() else -1))
        finally:
            combo.blockSignals(blocked)
        on = variable is not None
        for w in (combo, self._vsel_add_btn, self._vsel_remove_btn):
            w.setEnabled(on)

    def _vsel_say(self, text: str, warnings=()) -> None:
        """Show *text*, then each warning on its own warn-coloured line."""
        label = getattr(self, "_vsel_status", None)
        if label is None:
            return
        lines = [escape(text)] if text else []
        lines += [f'<span style="color:{theme.Colors.warn};">⚠ {escape(w)}</span>'
                  for w in warnings]
        label.setText("<br>".join(lines))
        label.setVisible(bool(lines))

    def _vsel_status_text(self) -> str:
        """The status line as plain text — what a test or a screen reader gets."""
        from PySide6.QtGui import QTextDocument

        label = getattr(self, "_vsel_status", None)
        if label is None or not label.text():
            return ""
        doc = QTextDocument()
        doc.setHtml(label.text())
        return doc.toPlainText()

    # ── actions ──────────────────────────────────────────────────────────
    def _vsel_current(self) -> tuple | None:
        """``(variable, value_id or None, value label)`` for the row, or ``None``."""
        vset = self._vsel_variable_set()
        variable = vset.by_id(self._vsel_var_cb.currentData()) if vset else None
        data = self._vsel_val_cb.currentData()
        if variable is None or data is None:
            return None
        value_id = None if data == _UNASSIGNED else data
        label = self._vsel_val_cb.currentText()
        if value_id is None:
            label = f"{variable.name} not set"
        return variable, value_id, label

    def _vsel_membership(self):
        picked = self._vsel_current()
        if picked is None:
            return None, ""
        variable, value_id, label = picked
        membership = membership_for_value(
            self._vsel_variable_set(), variable.id, value_id,
            self._vsel_loaded_wells(), self._vsel_rep_sets())
        return membership, label

    def _vsel_name_after_add(self, grp, label: str) -> None:
        names = self._vsel_value_names()
        if id(grp) in self._auto_named_group_ids:
            # Leaving the auto-name set is what keeps a later drag from
            # replacing the value name with a list of wells.
            self._auto_named_group_ids.discard(id(grp))
            names[id(grp)] = []
        if id(grp) in names:
            if label not in names[id(grp)]:
                names[id(grp)].append(label)
            grp.name = " + ".join(names[id(grp)])

    def _vsel_name_after_remove(self, grp, label: str) -> None:
        names = self._vsel_value_names()
        held = names.get(id(grp))
        if held is None or label not in held:
            return
        held.remove(label)
        if held:
            grp.name = " + ".join(held)
        else:
            # Nothing left that the name stood for: back to a member name.
            names.pop(id(grp), None)
            self._auto_named_group_ids.add(id(grp))
            self._refresh_auto_group_name(grp)

    def _vsel_add(self) -> None:
        membership, label = self._vsel_membership()
        if membership is None:
            return
        if not membership.wells:
            self._vsel_say(f"No loaded well holds {label}.")
            return
        grp = self._active_group()
        if grp is None:
            # The obvious intent of "add these wells" with no group: a group of
            # them. Refusing would make the user find "+ Add" and come back.
            grp = BarGroup("New Group")
            self._groups.append(grp)
            self._auto_named_group_ids.add(id(grp))
            self._active_grp = len(self._groups) - 1
        gained = add_membership(grp, membership)
        self._vsel_name_after_add(grp, label)
        self._refresh_group_list()
        text = f"Added {_plural(gained, 'well')} to “{grp.name}”."
        if gained < len(membership.wells):
            text += f" {len(membership.wells) - gained} were already in it."
        warnings = []
        if membership.split_sets:
            in_sets = {w for r in self._vsel_rep_sets() for w in r.wells}
            split_wells = [w for w in membership.solo_wells if w in in_sets]
            warnings.append(
                f"{_plural(len(membership.split_sets), 'replicate set')} split "
                f"({', '.join(membership.split_sets)}): its other wells are not "
                f"{label}, so {_wells_list(split_wells)} joined as solo wells.")
        self._vsel_say(text, warnings)

    def _vsel_remove(self) -> None:
        grp = self._active_group()
        if grp is None:
            self._vsel_say("Select a group first.")
            return
        membership, label = self._vsel_membership()
        if membership is None:
            return
        removed, kept = remove_membership(grp, membership)
        self._vsel_name_after_remove(grp, label)
        self._refresh_group_list()
        text = f"Removed {_plural(removed, 'well')} from “{grp.name}”."
        warnings = []
        if kept:
            warnings.append(
                f"Kept {', '.join(kept)}: it also holds wells that are not "
                f"{label}. Use its − button on the group card to remove it.")
        self._vsel_say(text, warnings)

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
                act = sub.addAction(NO_VARIABLES_TEXT)
                act.setEnabled(False)
                return
            for variable in variables:
                sub.addAction(variable.name,
                              lambda vid=variable.id: self._quick_by_variable(vid))

        sub.aboutToShow.connect(_fill)
        _fill()

    def _vsel_confirm_replace(self) -> bool:
        """Ask before a Quick Setup replaces groups the user built.

        A user may expect "one group per value" to *add* groups; Clear All
        asks first for the same loss, so this does too.
        """
        if not self._groups:
            return True
        ret = QMessageBox.question(
            self, "Replace export groups?",
            f"Replace the {_plural(len(self._groups), 'existing group')} "
            "with one group per value?",
            QMessageBox.Yes | QMessageBox.No,
        )
        return ret == QMessageBox.Yes

    def _quick_by_variable(self, variable_id: str) -> None:
        """Replace every group with one per value of *variable_id*."""
        vset = self._vsel_variable_set()
        variable = vset.by_id(variable_id) if vset else None
        if variable is None or not self._vsel_confirm_replace():
            return
        groups, unassigned = groups_per_value(
            vset, variable_id, self._vsel_loaded_wells(), self._vsel_rep_sets())
        self._groups.clear()
        self._auto_named_group_ids.clear()
        self._vsel_value_names().clear()
        split: list[str] = []
        for vg in groups:
            m = vg.membership
            grp = BarGroup(vg.name, members=list(m.members),
                           solo_wells=list(m.solo_wells))
            self._groups.append(grp)
            self._vsel_value_names()[id(grp)] = [vg.name]
            split.extend(s for s in m.split_sets if s not in split)
        self._active_grp = 0 if self._groups else -1
        self._refresh_group_list()
        warnings = []
        if unassigned:
            warnings.append(
                f"{_plural(len(unassigned), 'well')} with no {variable.name} "
                f"left out: {_wells_list(unassigned)}.")
        if split:
            warnings.append(
                f"{_plural(len(split), 'replicate set')} split across groups "
                f"({', '.join(split)}): their wells hold different values.")
        self._vsel_say(
            f"{_plural(len(groups), 'group')}, one per {variable.name}.", warnings)
