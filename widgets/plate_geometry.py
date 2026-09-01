"""One sizing/alignment rule for every :class:`~widgets.well_plate_selector.WellPlateSelector`.

Thirteen places in the app build a plate map — Review's three rail pickers
(shared, Segmentation, Image Table), the Statistics comparison-group editor,
Batch Export's group editor, Home's plate panel and its Variables canvas, the
Sample-Definitions replicate-set panel and its "New Replicate Set" dialog, the
saved-selections compose popover, the OME import and Subset tools, and the
status map inside every run dashboard (``widgets/plate_progress.py``). Counted
from the ``apply_plate_geometry`` call sites rather than remembered: this
sentence has been stale twice, and the count is the one part of it a reader
takes on trust. They differ in what the *host* does
with the plate (which signals it wires, which wells it enables, whether it
paints run states onto it), and they used to differ in how big the plate was
allowed to be, because each one set its own caps and its own alignment. Three
sizing regimes and two alignments had accumulated that way, and the same plate
control rendered at a visibly different cell size depending on which pane the
user was looking at.

This module is the single home for that geometry. Everything about *size* and
*placement* is identical for every plate in the app; a ``role`` decides only two
behavioural things:

===========  ==========================  ===============================
``role``     All / Invert / Clear row    Selection
===========  ==========================  ===============================
``"rail"``   hidden (host has its own)   interactive (``"select"``)
``"picker"`` shown                       interactive (``"select"``)
``"status"`` hidden                      read-only (``"passive"``)
``"editor"`` hidden (host has its own)   interactive (``"select"``)
``"canvas"`` hidden (host has its own)   interactive (``"select"``)
===========  ==========================  ===============================

``"canvas"`` behaves exactly as ``"editor"`` does here and differs only in its
size caps. The rail picker is exempt from them (see below); an
editor plate sitting among other controls on a page takes them; a canvas plate
— one that *is* its page's primary surface rather than one control on it —
takes the larger :data:`PLATE_CANVAS_WIDTH` / :data:`PLATE_CANVAS_HEIGHT`
instead.

Why a maximum *width* as well as a maximum height: ``WellPlateSelector`` paints
its panel background and border across its entire allocated widget box, while
the grid inside it sizes cells to ``min(available_width / 12,
available_height / 8)`` (``well_plate_selector._PlateGrid._metrics``). Once the
height cap fixes the cell size, extra width does not grow the grid — it only
stretches the panel into a wide, mostly-empty card with a small grid floating in
the middle. The width cap makes the card hug its content.

Why the height cap is not one number: a plate showing its action row needs room
for the grid *and* for that row. Callers used to add the difference by hand —
one of them carried a comment explaining that its cap was "the dashboard's 210
plus ~48 for the action row, matched empirically". :func:`apply_plate_geometry`
measures the row from the widget instead, so the allowance follows the real
button height at the current font and DPI and nobody has to re-derive a number.

The caps are applied when the helper runs, from the plate's layout as it stands
at that moment; a host that later changes the action row's visibility itself
(rather than through a role) should call the helper again.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy

#: Widest a plate card may render — a shade over the Review rail's own width
#: (``well_viewer.core.app_shell_view.RAIL_W``, which a sidebar plate spans
#: edge to edge), so the rail plate is effectively the reference size and every
#: other plate in the app is capped to it. Not imported from there: ``widgets/``
#: must not import ``well_viewer``, and the cap is a plate-map property rather
#: than a restatement of the rail's width.
PLATE_MAX_WIDTH = 330

#: Narrowest a plate may be squeezed to by a host that has to share its width.
#: Also the app's statement of how small a well may get before a plate stops
#: being something you can drag on: twelve columns in this width is about 18 px
#: of well, and ``PLATE_MIN_WIDTH / 12`` is the figure to compare a plate's own
#: cell size against. There is deliberately no matching height floor — a host
#: that cannot give a plate its height should scroll, and the one place this
#: came up (the Statistics comparison-group editor) does exactly that, which
#: made a ``setMinimumHeight`` beside it measurably dead.
#: Only relevant to a plate laid out *beside* something else — the rail's is
#: alone in its slot and an ``editor`` plate on a full-width page never reaches
#: this. The grid sizes its cells to ``min(width / 12, height / 8)``, so 12
#: columns in this width resolve to about 18 px of well; below that the app
#: already judged a plate too small to work on, which is the argument
#: :data:`PLATE_CANVAS_WIDTH` was introduced by. A host that would go narrower
#: should stack rather than shrink.
PLATE_MIN_WIDTH = 220

#: Tallest a plate card may render with its action row hidden — grid, headers and
#: the selector's own layout margins. Together with :data:`PLATE_MAX_WIDTH` this
#: is what fixes the cell size: at the maximum width the height is the binding
#: constraint, so every plate in the app resolves to the same cell scale.
PLATE_MAX_HEIGHT = 210

#: The caps for a ``"canvas"`` plate — one that is not a control on its page but
#: the surface the page exists to operate. The Variables tab's is the case:
#: every gesture it offers is "pick wells here, then say what they are", and its
#: plate carries three independent marks at once (a value's colour, a ring for
#: an unannotated well, a second variable's glyph). At the editor caps that
#: resolves to roughly 16.5 px of well and a 10 px glyph inside it.
#:
#: Not the rail's exemption, which is uncapped. Reusing that role here would
#: also inherit an argument written about Review's rail specifically, and would
#: let this plate grow to whatever width the page happens to have — the failure
#: the width cap exists to prevent. A named ceiling keeps the card hugging its
#: content and keeps the reason for the number attached to it.
PLATE_CANVAS_WIDTH = 470
PLATE_CANVAS_HEIGHT = 300

#: How every plate is placed in its container. Pass it as the alignment argument
#: of ``addWidget`` (``layout.addWidget(plate, 0, PLATE_ALIGNMENT)``) so a
#: container wider than the width cap centres the card instead of stranding it
#: against one edge.
PLATE_ALIGNMENT = Qt.AlignHCenter

#: Qt's QWIDGETSIZE_MAX — "no ceiling", used to exempt the rail picker.
_UNCAPPED = 16_777_215

_ROLES = ("rail", "picker", "status", "editor", "canvas")

#: ``objectName`` of the grid child inside a ``WellPlateSelector``. Everything
#: else in the selector's layout is chrome around the grid.
_GRID_OBJECT_NAME = "PlateGrid"


def _action_row_allowance(plate) -> int:
    """Extra height the plate needs for rows sitting *beside* the grid.

    Everything in the selector's layout other than the grid itself — in practice
    only the All / Invert / Clear row — costs the layout spacing above it plus
    its own size hint, and only while it is visible. Hidden rows cost nothing,
    so a ``"rail"`` or ``"status"`` plate gets the bare
    :data:`PLATE_MAX_HEIGHT` and a ``"picker"`` gets that plus the room its row
    actually needs. Measured from the widget rather than assumed, so the
    allowance tracks the real button metrics at the current font and DPI.
    """
    layout = plate.layout()
    if layout is None:
        return 0
    total = 0
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget is None or widget.objectName() == _GRID_OBJECT_NAME:
            continue
        if widget.isHidden():
            continue
        total += layout.spacing() + widget.sizeHint().height()
    return total


def apply_plate_geometry(plate, *, role: str = "rail") -> None:
    """Give *plate* the app-wide plate-map geometry for *role*.

    *role* is ``"rail"``, ``"picker"``, ``"status"`` or ``"editor"`` (see the
    module docstring): it selects whether the built-in action row is shown and
    whether the plate owns an interactive selection. It does **not** affect
    size, alignment or panel treatment — those are the same for all of them,
    which is the point of the helper.

    ``"editor"`` is an interactive plate whose host supplies its own actions,
    so the built-in All / Invert / Clear row would operate on a model the host
    does not use: Convert OME-TIFF's acquisition-path plate, whose model is an
    ordered route with its own Generate / Clear buttons beneath it. It takes
    the size caps rather than the rail's exemption — it sits in a page, not in
    the rail, and uncapped it grows to the scroll area's full width and pushes
    the run button below the fold.

    Everything else about the plate stays the caller's business: which wells are
    enabled, drag behaviour, tooltips, colour overrides, and any later switch of
    selection mode (Review's rail flips its plate to ``"passive"`` while a
    replicate set is being edited, for instance).

    Callers must add the plate to their layout with :data:`PLATE_ALIGNMENT` and
    must not put a horizontal inset around it: the grid paints from the widget's
    own width, so a gutter on one host shrinks that host's plate in both
    dimensions and it alone renders smaller than its peers.
    """
    if role not in _ROLES:
        raise ValueError(f"unknown plate role {role!r}; expected one of {_ROLES}")

    plate.setActionsVisible(role == "picker")
    plate.setSelectionMode("passive" if role == "status" else "select")
    # A status plate reports and accepts nothing: no corner select-all, no
    # pointing-hand cursor. "passive" alone does not say that — the corner is
    # deliberately live in passive mode for the group plates whose host wires
    # ``cornerToggled``. Kept separate from mouse *transparency*, which would
    # also take the hover tooltip, and a dashboard's per-well counts are
    # readable nowhere else.
    plate.setReportOnly(role == "status")

    # Preferred (not Expanding) with heightForWidth: the plate scales down to a
    # container narrower than the caps and keeps its aspect ratio, instead of
    # forcing horizontal overflow or rendering a squashed grid.
    policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    policy.setHeightForWidth(True)
    plate.setSizePolicy(policy)

    if role == "rail":
        # Review's left-rail well picker is EXEMPT from the caps. It is the
        # app's primary interaction surface, not a status readout, and it has
        # its own sizing story: it scales to whatever width the rail gives it
        # and keeps its aspect, with no ceiling. Capping it to the dashboards'
        # budget shrank its cells from 23.7px to 20.8px — unifying the two by
        # making the one people actually click on smaller, which is the wrong
        # direction. The size policy above is shared because that part IS the
        # same rule; only the ceiling differs.
        plate.setMaximumWidth(_UNCAPPED)
        plate.setMaximumHeight(_UNCAPPED)
        return
    if role == "canvas":
        plate.setMaximumWidth(PLATE_CANVAS_WIDTH)
        plate.setMaximumHeight(PLATE_CANVAS_HEIGHT + _action_row_allowance(plate))
        return
    plate.setMaximumWidth(PLATE_MAX_WIDTH)
    plate.setMaximumHeight(PLATE_MAX_HEIGHT + _action_row_allowance(plate))
