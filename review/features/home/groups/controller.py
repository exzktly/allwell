"""Groups tab behaviour: build a replicate set, and push it onto the rail plate.

The pure halves of what used to sit beside these live in
``domain/selections_model.py`` — this module is the part that opens a dialog,
reads a dropdown, or writes to a widget, so it stays in the feature.
"""

from __future__ import annotations

from typing import NamedTuple

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from core.safe import swallowed
from review.domain.batch_models import ReplicateSet
from review.domain.plate_layout import PLATE_COLS, PLATE_ROWS
from review.domain.viewer_state import extract_well_token as _extract_well_token


# Pixel geometry for the New Replicate Set dialog. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_DLG_MIN_W = 420
_DLG_MIN_H = 480


class RepAddDialog(NamedTuple):
    """The New Replicate Set dialog and the two controls a caller drives.

    ``dialog`` is what :func:`rep_add` execs; ``name_edit`` and ``plate`` are
    handed over rather than looked up, so nothing has to describe the dialog's
    layout in order to reach them.
    """

    dialog: QDialog
    name_edit: QLineEdit
    plate: object          # a ``widgets.WellPlateSelector``, imported lazily


def make_replicate_pairs(toks, size=2):
    """Chunk adjacent tokens into ReplicateSets of ``size`` wells each.

    Consecutive wells are grouped ``size`` at a time; a trailing remainder
    smaller than ``size`` becomes its own (smaller) set. Each set holds one
    condition's replicate wells — a group of N wells, not just pairs. ``size=1``
    yields one solo set per well; ``size=2`` reproduces the historical pairing.
    """
    size = max(1, int(size))
    sets = []
    for i in range(0, len(toks), size):
        chunk = list(toks[i:i + size])
        if chunk:
            sets.append(ReplicateSet("/".join(chunk), chunk))
    return sets


def name_sets_from_variables(ctx, sets) -> None:
    """Rename *sets* from the sample variables their wells hold, in place.

    A replicate group's generated name is the tokens of the wells in it —
    ``A01/A02`` — which says where the group is on the plate and nothing about
    what it *is*. Once the user has declared what was varied across the plate,
    the plate already knows: the wells of one group hold one cell line and one
    dose, and ``HeLa · 10 µM`` is the name they would have typed.

    Only where the variables answer. A plate with no variables, or a group whose
    wells disagree about every one of them, keeps the tokens
    :func:`make_replicate_pairs` gave it — :func:`sample_variables.name_for_wells`
    refuses rather than guessing, and the tokens are a true statement about a
    group nothing else can describe.

    ``name_is_verbatim`` rides along because a one-variable plate composes a name
    that *is* the value: a `Dose` of ``10`` with no unit typed matches
    :func:`replicate_display_label`'s auto-generated-name pattern exactly, and
    without the flag every axis would draw the well tokens this function exists
    to replace.
    """
    from review.domain.sample_variables import names_for_well_sets

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or not vset.variables:
        return
    names = names_for_well_sets(vset, [list(s.wells) for s in sets])
    for rset, name in zip(sets, names):
        if name:
            rset.name = name
            rset.name_is_verbatim = True


def _variable_name_for(ctx, wells) -> str:
    """What *wells* are in sample-variable terms, or ``""`` — ctx-shaped.

    One line over :func:`sample_variables.name_for_wells`, plus the one thing
    that function cannot know: whether a group already on the plate is called
    that. It earns its own name by being the single place the two
    group-creating flows agree on both answers. ``getattr`` because a context
    built for a headless path carries a selection slice without the variables
    field.

    **The collision is settled here rather than left to ``_unique_name``.**
    :func:`sample_variables.names_for_well_sets` disambiguates a Quick
    Replicates batch by appending the wells, on the stated grounds that
    ``1 µM_v2`` says only that something else was called ``1 µM`` while the
    wells say which replicates these are. This flow adds one group at a time,
    so it has no batch to clash within and used to fall through to exactly the
    ``_v2`` that argument rejects — two flows, one plate, two different answers,
    and a manual sentence that described only one of them.
    """
    from review.domain.sample_variables import name_for_wells

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or not vset.variables:
        return ""
    return name_for_wells(
        vset, wells,
        taken={s.get("name") for s in (ctx.state.selection.selections or [])})


def rep_quick_pairs(ctx) -> None:
    """Generate quick replicate pairs using current dropdown selections."""
    pair_dir = ctx.runtime.groups.pair_dir or "row"
    iter_order = ctx.runtime.groups.iter_order or "row"
    size = ctx.runtime.groups.rep_size or 2
    well_paths = ctx.state.dataset.well_paths
    sel_state = ctx.state.selection
    new_sets = []

    if pair_dir == "row":
        if iter_order == "row":
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in well_paths]
                new_sets.extend(make_replicate_pairs(loaded, size))
        else:
            by_col = {}
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in well_paths]
                row_sets = make_replicate_pairs(loaded, size)
                for s in row_sets:
                    if s.wells:
                        col = _extract_well_token(s.wells[0])
                        if col and len(col) > 1:
                            col = col[1:]
                            by_col.setdefault(col, []).append(s)
            for col in PLATE_COLS:
                if col in by_col:
                    new_sets.extend(by_col[col])
    else:
        if iter_order == "col":
            for col in PLATE_COLS:
                loaded = [f"{row_ltr}{col}" for row_ltr in PLATE_ROWS
                          if f"{row_ltr}{col}" in well_paths]
                new_sets.extend(make_replicate_pairs(loaded, size))
        else:
            by_row = {}
            for col in PLATE_COLS:
                loaded = [f"{row_ltr}{col}" for row_ltr in PLATE_ROWS
                          if f"{row_ltr}{col}" in well_paths]
                col_sets = make_replicate_pairs(loaded, size)
                for s in col_sets:
                    if s.wells:
                        tok = _extract_well_token(s.wells[0])
                        if tok and len(tok) > 0:
                            row = tok[0]
                            by_row.setdefault(row, []).append(s)
            for row in PLATE_ROWS:
                if row in by_row:
                    new_sets.extend(by_row[row])

    if not new_sets:
        return
    name_sets_from_variables(ctx, new_sets)
    if sel_state.selections and not _confirm_replace(ctx, len(sel_state.selections)):
        return
    replace_groups(ctx, new_sets)


def _confirm_replace(ctx, n_groups: int) -> bool:
    """Ask before a generated set of groups replaces the user's *n_groups*."""
    resp = QMessageBox.question(
        ctx.window, "Replace groups?",
        f"This will replace the current {n_groups} group(s). Continue?",
        QMessageBox.Yes | QMessageBox.No,
    )
    return resp == QMessageBox.Yes


def replace_groups(ctx, sets) -> None:
    """Make *sets* the plate's replicate groups, replacing whatever was there.

    The one writer for both generators on this page — Quick Replicates and
    *Make replicates from variables* — so a generated group is the same kind of
    thing whichever button made it: same source tag, same colour sequence,
    same ``name_is_verbatim`` carried through, and editable afterwards exactly
    like a group drawn by hand.
    """
    from domain.selections_format import make_selection
    sel_state = ctx.state.selection
    used_names: set = set()
    used_ids: set = set()
    # A group is one condition; its wells are implicitly its replicate set, so
    # no explicit ``replicates`` partition is stored (see
    # ``domain/selections_format.py``'s ``make_selection``).
    sels = [make_selection(name=s.name, wells=list(s.wells),
                           replicates=None,
                           name_is_verbatim=s.name_is_verbatim,
                           source="rep_set", used_names=used_names, used_ids=used_ids,
                           fallback_color_idx=i)
            for i, s in enumerate(sets)]
    sel_state.selections = sels
    sel_state.current_selection_id = sels[0]["id"] if sels else None
    ctx.coordinator.rebuild_all()


# ── replicates from the sample variables ────────────────────────────────────

def replicates_from_variables(ctx):
    """``(sets, wells left out)``: one group per distinct variable assignment.

    Wells whose assignments are identical across every variable are replicates
    of one condition, so they become one group — the rule is
    :func:`sample_variables.replicate_groups`, and the names are
    :func:`sample_variables.names_for_well_sets`', so a group reads
    ``HeLa · 10 µM`` and two conditions can never share a name. Loaded wells
    only, in plate order: a group can hold only what this load can plot.
    """
    from review.domain.sample_variables import names_for_well_sets, replicate_groups
    from review.domain.viewer_state import parse_rc

    vset = getattr(ctx.state.selection, "variables", None)
    wells = sorted(ctx.state.dataset.well_paths or {}, key=parse_rc)
    if vset is None or not vset.variables:
        return [], wells
    groups, left_out = replicate_groups(vset, wells)
    names = names_for_well_sets(vset, groups)
    sets = [ReplicateSet(name or "/".join(g), list(g), name_is_verbatim=bool(name))
            for g, name in zip(groups, names)]
    return sets, left_out


def rep_replicates_from_variables(ctx) -> None:
    """*Make replicates from variables*: replace the groups with one per condition.

    Says what it did in the sentence the user reads next — see
    :func:`_made_replicates_sentence`.
    """
    sets, left_out = replicates_from_variables(ctx)
    if not sets:
        ctx.notify.set_status(
            "No well has a variable value yet. Fill values on the Variables "
            "tab first.")
        return
    sel_state = ctx.state.selection
    if sel_state.selections and not _confirm_replace(ctx, len(sel_state.selections)):
        return
    replace_groups(ctx, sets)
    ctx.notify.set_status(_made_replicates_sentence(ctx, sets, left_out))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _made_replicates_sentence(ctx, sets, left_out) -> str:
    """How many groups, which are single wells, which wells are in none.

    A group of one well is still a condition but has no replicates, so no
    spread can be drawn for it; saying so here is cheaper than the user finding
    a bar with no whisker. A well with no value at all is in no group, and a
    well missing from every group is missing from every plot. And when the
    plotting band is grouped by a variable the new groups appear on no plot
    until it is set back, which would otherwise look like nothing happened.
    """
    from review.domain.plot_members import variable_grouping_in_force

    parts = [f"Made {_plural(len(sets), 'replicate group')} from the variables."]
    singles = [s.name for s in sets if len(s.wells) == 1]
    if singles:
        shown = ", ".join(singles[:3]) + ("…" if len(singles) > 3 else "")
        parts.append(f"{_plural(len(singles), 'group')} "
                     f"{'has' if len(singles) == 1 else 'have'} a single well ({shown}).")
    if left_out:
        shown = ", ".join(left_out[:4]) + ("…" if len(left_out) > 4 else "")
        parts.append(f"{_plural(len(left_out), 'well')} with no values "
                     f"{'is' if len(left_out) == 1 else 'are'} in no group ({shown}).")
    if variable_grouping_in_force(ctx):
        parts.append("The plots are still grouped by a variable — set Group by "
                     "to Wells / groups on the plotting band to plot these groups.")
    return " ".join(parts)


def refresh_replicates_button(ctx) -> None:
    """Enable *Make replicates from variables* only once a variable exists.

    Disabled with its reason rather than hidden: the button says what it would
    do, and the tooltip says what it is waiting for.
    """
    button = ctx.widgets.get_or_none("_rep_from_var_btn")
    if button is None:
        return
    vset = getattr(ctx.state.selection, "variables", None)
    on = bool(getattr(vset, "variables", ()))
    button.setEnabled(on)
    button.setToolTip(
        (button.property("enabledTip") or "") if on else
        "Define a variable on Home → Sample Map → Variables first.")


def build_rep_add_dialog(ctx) -> RepAddDialog:
    """Build the New Replicate Set dialog, fully wired, without showing it.

    Wells are picked on the same graphical plate the "+ wells…" popover uses to
    edit an existing group's membership — not a text list of well names.
    Configured identically to ``SavedSelectionsList._open_compose_popover`` so
    the two flows look and feel the same (rubber-band rectangle select,
    row/column headers, multi-select).

    The name field tracks the plate while it still holds the name this dialog
    put there: pick the wells of one condition and the box fills in with what
    the sample variables say they are, ``HeLa · 10 µM`` in place of ``Rep 3``.
    One keystroke in the field ends that — the text is then the user's and is
    never overwritten, which is the whole of the rule and the reason the last
    offered name is remembered rather than the field being re-derived on every
    change.

    **Separate from** :func:`rep_add` **so that rule can be driven.** Every
    behaviour above lives in a closure connected to a signal, and a closure
    reachable only from inside ``QDialog.exec`` is reachable from no test at
    all: a modal blocks for ever under the offscreen QPA. Building and exec'ing
    apart is what lets a test select wells on the real plate and read the real
    name box, rather than calling the naming rule directly one step past the
    connection that is the thing most likely to be wrong (§7.6).

    Returns a :class:`RepAddDialog` — the dialog with its name box and its
    plate beside it — because a caller that has to *find* those two has to
    describe the layout to do it, and the description goes stale the first time
    a row moves. Handing them back is neither an object name (which
    ``tests/test_theme_selectors`` rightly reads as a request for appearance,
    and these are not) nor a ``ctx.widgets`` registration (a transient dialog's
    children outliving it in the app-wide registry is a stale handle waiting to
    be read). The Create button is deliberately **not** among them: a test given
    the button by the thing it is testing cannot notice a button that was never
    connected, so it is found by its text.
    """
    from review.core.selection_controller import sel_add
    from review.domain.viewer_state import parse_rc
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector
    sel_state = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle("New Replicate Set")
    dlg.setModal(True)
    dlg.setMinimumSize(_DLG_MIN_W, _DLG_MIN_H)
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel("Name:"))
    fallback_name = f"Rep {len(sel_state.selections)+1}"
    name_edit = QLineEdit(fallback_name)
    v.addWidget(name_edit)
    v.addWidget(QLabel("Choose wells for this group:"))

    # The last name this dialog offered, and whether it came from the variables.
    # A one-element list because ``_offer_name`` closes over it and rebinding a
    # plain name inside a nested function would make it local to that function.
    offered = [fallback_name, False]

    enabled = sorted(well_paths.keys(), key=lambda l: parse_rc(l))
    plate = WellPlateSelector()
    try:
        plate.setEnabledWells(enabled)
        plate.setRowColumnSelectable(True)
        plate.setSingleSelectionMode(False)
        plate.setDragMode("rect")   # rubber-band rectangle, not freeform paint
        # The app-wide plate geometry (widgets/plate_geometry.py): same caps,
        # same cell scale and same centring as every other plate map in the app.
        # The "rail" role hides the built-in action row — this dialog has its own
        # Create / Cancel buttons and no use for All / Invert / Clear.
        apply_plate_geometry(plate, role="rail")
    except Exception:
        swallowed()
    v.addWidget(plate, 1, PLATE_ALIGNMENT)

    def _offer_name(*_a) -> None:
        """Fill the name box from what the picked wells are, unless it was typed in.

        An **empty** field counts as untyped, not as a name of its own. Clearing
        the box is how a user says "not that one"; treating the empty string as
        a name they had chosen meant no later pick ever refilled it, the field
        sat visibly blank while wells were being selected, and Create then
        stored ``Rep 1`` on a fully annotated plate.
        """
        typed = name_edit.text().strip()
        if typed and typed != offered[0]:
            return
        picked = [w for w in plate.selectedWellIds() if w in well_paths]
        name = _variable_name_for(ctx, picked)
        offered[0] = name or fallback_name
        offered[1] = bool(name)
        name_edit.setText(offered[0])

    plate.selectionChanged.connect(_offer_name)

    btn_row = QHBoxLayout()
    v.addLayout(btn_row)
    ok_btn = QPushButton("Create")
    ok_btn.setProperty("variant", "primary")
    cancel_btn = QPushButton("Cancel")
    btn_row.addWidget(ok_btn)
    btn_row.addWidget(cancel_btn)
    btn_row.addStretch(1)

    def _ok():
        picked = sorted((w for w in plate.selectedWellIds() if w in well_paths),
                        key=parse_rc)
        if not picked:
            QMessageBox.warning(dlg, "No wells", "Select at least one well.")
            return
        name = name_edit.text().strip() or offered[0]
        # Verbatim only while the field still holds the name the variables
        # composed. A name the user typed over it is theirs, and a `Dose` group
        # they chose to call ``3`` is exactly the auto-generated-looking name the
        # relabelling heuristic is for.
        #
        # ``offered[0]`` rather than ``fallback_name`` for the empty field: with
        # the box cleared, ``_offer_name`` has kept it up to date with the plate,
        # so the composed name is what Create should store — falling back to
        # ``Rep 1`` there would discard a name the dialog had already worked out.
        verbatim = offered[1] and name == offered[0]
        dlg.accept()
        # sel_add enforces well-exclusivity (the new group takes the wells).
        # A group is one condition; its wells are implicitly its replicate set.
        sel_add(ctx, name, list(picked), replicates=None, source="rep_set",
                name_is_verbatim=verbatim)

    ok_btn.clicked.connect(_ok)
    cancel_btn.clicked.connect(dlg.reject)
    return RepAddDialog(dlg, name_edit, plate)


def rep_add(ctx) -> None:
    """Open the New Replicate Set dialog and run it.

    The whole of what this adds to :func:`build_rep_add_dialog` is the ``exec``,
    which is the one line a test cannot run.
    """
    build_rep_add_dialog(ctx).dialog.exec()



# A8 teardown: the display-label leaves live in the domain layer (shared by core
# and every plot renderer without a feature import), and callers reach them
# there directly — the re-export this comment used to describe went with
# ``features/grouping``.


def rep_refresh_map(ctx) -> None:
    """Push selection state onto the GROUPS-panel plate (a WellPlateSelector):
    every well takes its **group's** colour (so all wells in a group read as one
    condition and an explicit recolour is honoured — matching the group row's
    swatch and the main sidebar's group mode); the *current* selection's wells
    are shown as the plate's selection (sunken/accent), which is also what a drag
    on the plate edits."""
    from review.domain.selections_model import color_for_selection
    plate = ctx.widgets.get_or_none("_rep_map_plate")
    if plate is None:
        return
    sel_state = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths
    plate.setEnabledWells(list(well_paths.keys()))
    colors: dict[str, str] = {}
    for s in sel_state.selections:
        group_color = color_for_selection(s)
        for tok in (s.get("wells") or []):
            if tok in well_paths:
                colors[tok] = group_color
    plate.clearWellColors()
    plate.setWellColors(colors)
    cur_id = sel_state.current_selection_id
    cur = next((s for s in sel_state.selections if s.get("id") == cur_id), None)
    cur_wells = [w for w in (cur.get("wells") or []) if w in well_paths] if cur else []
    plate.setSelectedWellIds(cur_wells)
    # Variables are defined on a sibling tab and this is the refresh every
    # definition edit already reaches, so the button's enabled state follows
    # the variables being created or deleted.
    refresh_replicates_button(ctx)


def rep_quick_pairs_from_dropdowns(ctx) -> None:
    """Read dropdown values and update state, then call rep_quick_pairs()."""
    grp = ctx.runtime.groups
    pair_cb = ctx.widgets.get_or_none("_rep_quick_pair_dir_var")
    iter_cb = ctx.widgets.get_or_none("_rep_quick_iter_order_var")
    size_sb = ctx.widgets.get_or_none("_rep_quick_size_var")
    if pair_cb is None or iter_cb is None:
        return
    # Map dropdown display values to internal values
    grp.pair_dir = "row" if "Rows" in pair_cb.currentText() else "col"
    grp.iter_order = "row" if "Across" in iter_cb.currentText() else "col"
    grp.rep_size = size_sb.value() if size_sb is not None else 2
    rep_quick_pairs(ctx)
