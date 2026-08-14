"""The per-sample control editor: a row per plotted member, live-applied.

Opened from the Control dropdown's ``Per-sample control…`` entry. A **non-modal**
``QDialog``, built lazily and hidden rather than destroyed, following
``cell_gating/optimizer_view.build_optimizer_window`` — and for the same reason
its docstring gives: the answer is judged against the plot behind it, so the
window has to stay open while the user looks. A ``Qt.Popup`` would dismiss on
the first click outside it, and a modal dialog cannot be reached from a test at
all (``tests/conftest.py::_no_modal_dialogs``).

Four columns, because the question "did this do what I meant?" needs all four:

* **Sample** — the plotted member, with the bar's own colour chip.
* **Control** — a combo of the same entries the single-control dropdown offers,
  plus a retained entry for a control that no longer resolves. Stale
  assignments are kept and labelled, never silently reset: a user who unloads a
  plate and reloads it gets their pairing back.
* **Status** — the member's :class:`ControlOutcome`, in words.
* **Resolves to** — the well tokens ``resolve_control_wells`` actually
  returned. This is the column that answers the question; the other three
  describe intent, and intent is what was already wrong.

Edits live-apply. The footer carries ``Close`` and nothing else, because a
Cancel that cannot un-draw the plot behind it is a lie.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import theme
from well_viewer.core.safe import swallow
from well_viewer.domain import plot_members as _members
from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change_pairing import (
    CONTROL_FIRST,
    CONTROL_LAST,
    ControlOutcome,
    auto_pair,
    build_per_member_controls,
    summary_text,
)

#: Combo text for "this member has no control".
NONE_LABEL = "—"

#: Suffix marking a retained assignment whose control no longer resolves. It is
#: display chrome and never reaches the stored reference.
MISSING_SUFFIX = " (missing)"

#: Chunk sizes offered by Auto-pair. Small on purpose: a plate laid out in
#: repeating blocks is the layout per-sample controls exist for, and a chunk
#: larger than a block silently pairs across conditions.
AUTO_PAIR_SIZES = (2, 3, 4, 6)

_TABLE_MIN_W = 560
_TABLE_MIN_H = 260
_CHIP = "●"     # ● — the bar's colour, so a row is findable in the figure

#: Column order. Kept as a constant because the tests index by name.
COL_SAMPLE, COL_CONTROL, COL_STATUS, COL_RESOLVES = range(4)

_STATUS_WORDS = {
    ControlOutcome.OK: "paired",
    ControlOutcome.SELF: "own control",
    ControlOutcome.UNMAPPED: "no control set",
    ControlOutcome.CONTROL_UNRESOLVED: "control not found",
    ControlOutcome.CONTROL_NO_SAMPLE: "no data at this timepoint",
}


def status_word(outcome) -> str:
    """The Status cell's text for an outcome.

    Words rather than the enum's own value: the column is read by someone
    looking at a plate, not by a script. The CSV keeps the machine spelling.
    """
    return _STATUS_WORDS.get(outcome, str(outcome or ""))


def pairing_rows(ctx, scope: str) -> list[dict]:
    """One row per plotted member, in draw order — the table's whole content.

    Pure: it reads state and returns dicts, so the table's correctness can be
    tested without building a window. That matters more here than usual,
    because the failure this editor exists to prevent is a *table* that
    describes a plot which does not exist.
    """
    plot = ctx.state.plot
    keys = _members.plotted_member_keys(ctx, scope)
    controls = build_per_member_controls(
        ctx, keys, dict(getattr(plot, "fc_control_map", {}) or {}),
        member_wells=_members.member_wells(ctx, scope),
        member_rsets=_members.member_replicate_sets(ctx),
        render=str(getattr(plot, "fc_control_render", "self") or "self"),
    )
    rows: list[dict] = []
    for key in controls.order:
        rec = controls.get(key)
        rows.append({
            "member": key,
            "control": rec.control,
            "outcome": rec.outcome,
            "status": status_word(rec.outcome),
            "resolves_to": list(rec.wells),
            "inherited_from": rec.inherited_from,
        })
    return rows


def unused_rows(ctx, scope: str) -> list[tuple[str, object]]:
    """Assignments whose member is not currently plotted, kept not dropped."""
    plot = ctx.state.plot
    controls = build_per_member_controls(
        ctx, _members.plotted_member_keys(ctx, scope),
        dict(getattr(plot, "fc_control_map", {}) or {}),
        member_wells=_members.member_wells(ctx, scope),
    )
    return list(controls.unused)


def member_control_refs(ctx, scope: str) -> dict:
    """``member key -> the reference that addresses *that member*``.

    What Auto-pair needs: the chunk's control is one of the plotted members,
    and a plain name could not say whether it means the well or a replicate set
    that shares its token.
    """
    grouped = _members._keys_are_replicate_sets(ctx, scope)
    out: dict = {}
    for key in _members.plotted_member_keys(ctx, scope):
        out[key] = (ControlRef.for_replicate_set(key) if grouped
                    else ControlRef.for_well(key))
    return out


# ── the window ───────────────────────────────────────────────────────────────

def _window_attr(scope: str) -> str:
    return f"_fc_pairing_window_{scope}"


def open_pairing_editor(ctx, scope: str) -> QDialog | None:
    """Show (building on first use) the editor for *scope*."""
    win = ctx.widgets.get_or_none(_window_attr(scope))
    if win is None:
        win = build_pairing_window(ctx, scope)
        if win is None:
            return None
        ctx.widgets.register(_window_attr(scope), win)
    refresh_pairing_window(ctx, scope)
    win.show()
    win.raise_()
    return win


def refresh_pairing_window(ctx, scope: str) -> None:
    """Rebuild the table from state, if the window exists."""
    win = ctx.widgets.get_or_none(_window_attr(scope))
    if win is None:
        return
    populate = getattr(win, "_aw_populate", None)
    if populate is not None:
        populate()


def build_pairing_window(ctx, scope: str) -> QDialog | None:
    """Build the non-modal editor. Never shown modally — see the module docstring."""
    parent = getattr(ctx, "window", None)
    win = QDialog(parent)
    win.setWindowTitle("Per-sample control")
    win.setModal(False)
    win.setWindowFlag(Qt.Window, True)

    root = QVBoxLayout(win)
    blurb = QLabel(
        "Each sample is divided by its own control at the same timepoint. "
        "‘Resolves to’ shows the wells actually used.", win)
    blurb.setWordWrap(True)
    root.addWidget(blurb)

    table = QTableWidget(0, 4, win)
    table.setHorizontalHeaderLabels(
        ["Sample", "Control", "Status", "Resolves to"])
    table.setMinimumSize(_TABLE_MIN_W, _TABLE_MIN_H)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    root.addWidget(table)

    unused_label = QLabel("", win)
    unused_label.setWordWrap(True)
    root.addWidget(unused_label)

    footer = QWidget(win)
    frow = QHBoxLayout(footer)
    frow.setContentsMargins(0, 0, 0, 0)
    set_all_btn = QPushButton("Set all to ▾", footer)
    auto_btn = QPushButton("Auto-pair ▾", footer)
    frow.addWidget(set_all_btn)
    frow.addWidget(auto_btn)
    frow.addStretch(1)
    close_btn = QPushButton("Close", footer)
    frow.addWidget(close_btn)
    root.addWidget(footer)
    close_btn.clicked.connect(win.hide)

    def _write(mapping: dict) -> None:
        from well_viewer.core.fold_change_controls_view import set_fold_change_state
        set_fold_change_state(ctx, control_map=mapping, initiating_scope="")
        _populate()

    def _current_map() -> dict:
        return dict(getattr(ctx.state.plot, "fc_control_map", {}) or {})

    def _on_row_control_changed(member: str, combo: QComboBox) -> None:
        mapping = _current_map()
        ref = combo.currentData()
        if ref is None:
            mapping.pop(member, None)
        else:
            mapping[member] = ref
        _write(mapping)

    def _populate() -> None:
        rows = pairing_rows(ctx, scope)
        table.blockSignals(True)
        try:
            table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                _fill_row(ctx, scope, table, r, row, _on_row_control_changed)
        finally:
            table.blockSignals(False)
        unused = unused_rows(ctx, scope)
        unused_label.setText(
            f"Unused assignments ({len(unused)}): "
            + ", ".join(f"{k} → {v.display_name}" for k, v in unused)
            if unused else ""
        )
        unused_label.setVisible(bool(unused))
        win.setWindowTitle(f"Per-sample control — {summary_text(_controls(ctx, scope))}")

    def _on_set_all() -> None:
        menu = QMenu(win)
        for text, ref in _control_choices(ctx):
            if ref is None:
                continue
            menu.addAction(text).setData(ref)
        chosen = menu.exec(set_all_btn.mapToGlobal(set_all_btn.rect().bottomLeft()))
        if chosen is None:
            return
        keys = _members.plotted_member_keys(ctx, scope)
        mapping = _current_map()
        mapping.update({k: chosen.data() for k in keys})
        _write(mapping)

    def _on_auto_pair() -> None:
        menu = QMenu(win)
        for size in AUTO_PAIR_SIZES:
            for position, word in ((CONTROL_FIRST, "first"), (CONTROL_LAST, "last")):
                act = menu.addAction(
                    f"Every {size} samples — {word} is the control")
                act.setData((size, position))
        chosen = menu.exec(auto_btn.mapToGlobal(auto_btn.rect().bottomLeft()))
        if chosen is None:
            return
        size, position = chosen.data()
        mapping = _current_map()
        mapping.update(auto_pair(
            _members.plotted_member_keys(ctx, scope),
            member_control_refs(ctx, scope),
            size=size, position=position,
        ))
        _write(mapping)

    set_all_btn.clicked.connect(_on_set_all)
    auto_btn.clicked.connect(_on_auto_pair)
    win._aw_populate = _populate
    _populate()
    return win


def _controls(ctx, scope: str):
    plot = ctx.state.plot
    return build_per_member_controls(
        ctx, _members.plotted_member_keys(ctx, scope),
        dict(getattr(plot, "fc_control_map", {}) or {}),
        member_wells=_members.member_wells(ctx, scope),
        member_rsets=_members.member_replicate_sets(ctx),
    )


def _control_choices(ctx) -> list[tuple[str, object]]:
    """``(text, ref)`` for a row combo — the same entries the tab's combo has."""
    from well_viewer.core.fold_change_controls_view import _all_member_entries
    return [(NONE_LABEL, None), *_all_member_entries(ctx)]


def _fill_row(ctx, scope: str, table: QTableWidget, r: int, row: dict,
              on_change) -> None:
    member = row["member"]
    chip = QTableWidgetItem(f"{_CHIP}  {member}")
    with swallow("pairing row chrome"):
        from PySide6.QtGui import QColor
        from well_viewer.domain.selections_model import color_for_well
        chip.setForeground(QColor(color_for_well(member)))
    table.setItem(r, COL_SAMPLE, chip)

    combo = QComboBox(table)
    choices = _control_choices(ctx)
    saved = row["control"]
    # A retained assignment whose control no longer resolves keeps its slot,
    # labelled. Dropping it back to "—" would make the table agree with itself
    # and disagree with what the user set.
    if saved is not None and not any(ref == saved for _t, ref in choices):
        choices.append((f"{saved.display_name}{MISSING_SUFFIX}", saved))
    for text, ref in choices:
        combo.addItem(text, ref)
    idx = 0
    for i in range(combo.count()):
        if combo.itemData(i) == saved:
            idx = i
            break
    combo.setCurrentIndex(idx)
    combo.currentIndexChanged.connect(
        lambda _i, m=member, c=combo: on_change(m, c))
    table.setCellWidget(r, COL_CONTROL, combo)

    status = row["status"]
    if row["inherited_from"]:
        status = f"{status} (from {row['inherited_from']})"
    status_item = QTableWidgetItem(status)
    if row["outcome"] not in (ControlOutcome.OK, ControlOutcome.SELF):
        with swallow("pairing row chrome"):
            from PySide6.QtGui import QColor
            status_item.setForeground(QColor(theme.Colors.warn))
    table.setItem(r, COL_STATUS, status_item)

    table.setItem(r, COL_RESOLVES,
                  QTableWidgetItem(", ".join(row["resolves_to"]) or "—"))
