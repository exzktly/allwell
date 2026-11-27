"""Phone layout's host for the inspector: the Controls sheet.

A Review window has one inspector (``widgets.inspector.Inspector``). At the
desk it is a column beside the page; in phone layout it is what the Controls
sheet holds, in each of the sheet's three forms — a bottom drawer upright, a
right-hand panel sideways, a docked column on an unfolded foldable
(``PhoneShell._build_sheet``). The page's control band, laid out as a form, is
its **Data** tab; the page's Display and Style panels are the tabs beside it; a
sub-option is a page inside it. Nothing opens on top of the sheet.

The inspector outlives the sheets: a sheet is rebuilt when the phone turns or
folds, and the inspector is taken out of the old one (:func:`unmount`) before
that sheet is deleted and put in the new one (:func:`mount`). It is the same
inspector the desk column shows, so a layout switch moves it rather than
building another.

Qt-only and pane-free: the shell is handed the window's inspector through
``PhoneHandles.inspector`` and never imports the pane that owns it.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtWidgets import QVBoxLayout, QWidget

from widgets.inspector import Inspector

#: The tabs a shell's own inspector has when its window supplied none (a
#: window with no Review pane): the same four, so the sheet has one shape.
TABS = (("data", "Data"), ("display", "Display"), ("style", "Style"), ("detail", "Detail"))


def new_mount() -> QWidget:
    """The sheet's content widget: an empty holder an inspector is put in."""
    holder = QWidget()
    holder.setObjectName("PhoneInspectorMount")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    return holder


def own_inspector() -> Inspector:
    insp = Inspector()
    insp.setTabs(list(TABS))
    return insp


def mount(holder: QWidget, inspector: Inspector) -> None:
    """Put *inspector* in *holder* (taking it out of wherever it was)."""
    if inspector.parentWidget() is not holder:
        holder.layout().addWidget(inspector)
    inspector.show()


def unmount(inspector: Inspector | None) -> None:
    """Take *inspector* out of its sheet, so deleting the sheet leaves it."""
    if inspector is None or not shiboken6.isValid(inspector):
        return
    parent = inspector.parentWidget()
    if parent is not None and parent.objectName() == "PhoneInspectorMount":
        inspector.hide()
        inspector.setParent(None)


def sheet_height(inspector: Inspector, width: int, window_height: int, room: int,
                 share: float, pad: int) -> int:
    """How tall the bottom sheet stands: the whole room above the foot strip
    for a page that asked for it (an editor), and otherwise as tall as what it
    holds, up to *share* of the window — a sheet taller than its controls is
    dead space where the plot could be."""
    if inspector.wantedSize() == "full":
        return max(1, room)
    return min(int(window_height * share), inspector.contentHeight(width) + pad)
