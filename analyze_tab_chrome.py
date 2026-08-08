"""The Analyze form's own chrome — sections, rows, entries, info markers.

``AnalyzeTabFormMixin`` inherits :class:`AnalyzeFormChromeMixin` for the small
vocabulary every ``_build_*`` section is written in: a collapsible titled
section, a two-column label+widget row, a fixed-width entry, and the hover ``ⓘ``
marker that keeps long explanatory notes off the form. Split out of
``analyze_tab_form.py`` for its line budget; the methods bind to ``self``
exactly as before, so this is a pure relocation.

The optional sections fold by default so the required fields lead the form.
:meth:`AnalyzeFormChromeMixin.reveal_section` is the counterpart for the case
that discipline gets wrong: a control changed on the user's behalf, inside a
folded section, leaves no visible trace of having happened at all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget,
)


class AnalyzeFormChromeMixin:
    """Section / row / entry / info-marker builders for the Analyze form."""

    def _section(self, parent: QWidget, title: str, info: str = "",
                 *, collapsible: bool = False, collapsed: bool = False) -> QWidget:
        """Add a labelled section to *parent* and return its body widget.

        When *info* is given, a small ``ⓘ`` marker is placed next to the title
        and the text is shown as its hover tooltip — keeping long explanatory
        notes available on demand instead of cluttering the form inline.

        When *collapsible*, the header carries a ▾/▸ caret and clicking it
        shows/hides the body; *collapsed* sets the initial state. Used (C3) to
        fold the optional/advanced sections (QC, Shape, Output, Compute,
        Scratch) by default so the required fields lead the form.
        """
        container = QWidget(parent)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 4, 0, 4)
        cl.setSpacing(2)

        header = QWidget(container)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        caret = None
        if collapsible:
            caret = QLabel("▸" if collapsed else "▾", header)
            caret.setObjectName("SectionTitle")
            caret.setContentsMargins(12, 6, 0, 2)
            hl.addWidget(caret)
        title_lbl = QLabel(title, header)
        title_lbl.setObjectName("SectionTitle")
        title_lbl.setContentsMargins(2 if collapsible else 12, 6, 0, 2)
        hl.addWidget(title_lbl)
        if info:
            hl.addWidget(self._info_icon(header, info))
        hl.addStretch(1)
        cl.addWidget(header)

        rule = QFrame(container)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        cl.addWidget(rule)

        body = QWidget(container)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 4, 12, 8)
        bl.setSpacing(3)
        cl.addWidget(body)

        if collapsible:
            body.setVisible(not collapsed)

            def _toggle(_ev=None, _body=body, _caret=caret) -> None:
                vis = not _body.isVisible()
                _body.setVisible(vis)
                _caret.setText("▾" if vis else "▸")

            header.setCursor(Qt.PointingHandCursor)
            header.mousePressEvent = _toggle  # type: ignore[assignment]
            # Recorded so a caller that changes a control inside a folded
            # section can show what it did — see reveal_section.
            if not hasattr(self, "_collapsible_sections"):
                self._collapsible_sections = {}
            self._collapsible_sections[title] = (body, caret)

        parent.layout().addWidget(container)
        return body

    def reveal_section(self, title: str) -> None:
        """Unfold the collapsible section named *title*, if it is folded.

        The optional sections start collapsed so the required fields lead the
        form, which is right for a user filling it in and wrong for a control
        something else just changed on their behalf: Validate & Repair's
        "Complete this run in Analyze" ticks *Incremental* inside **Compute
        Options**, and with that section folded the handoff left no visible
        trace at all — a correctly configured completion run looked exactly
        like an untouched Analyze tab.
        """
        entry = getattr(self, "_collapsible_sections", {}).get(title)
        if entry is None:
            return
        body, caret = entry
        body.setVisible(True)
        caret.setText("▾")

    def _info_icon(self, parent: QWidget, info: str) -> QLabel:
        """A small ``ⓘ`` marker whose hover tooltip carries *info*.

        Keeps long explanatory notes available on demand instead of cluttering
        the form with inline paragraphs. Delegates to ``widgets.info_icon`` so
        the Review pane's tabs raise the same marker rather than a lookalike.
        """
        from widgets.info_icon import info_icon
        return info_icon(parent, info)

    #: Row label column width, narrow enough to leave room for a field at
    #: RAIL_W; word-wrap below lets an overflowing label wrap instead of clip.
    _ROW_LABEL_W = 108
    # Control widths for the Analyze form, alongside the label column above.
    # The whole set is one decision: ARCHITECTURE's rail-head section explains
    # that the form was narrowed to fit ``RAIL_W`` by shrinking this label
    # column and capping the two widest combos, so a later change to that fit
    # has to be able to see all of them at once.
    _ADD_BTN_W = 130
    _AUTO_THRESHOLD_METHOD_CB_W = 165
    _CB_W = 160
    _REMOVE_BTN_W = 36
    _RIGHT_MIN_W = 300
    _SCHEMA_STR_EDIT_MIN_W = 165
    _SEG_METHOD_CB_W = 165
    _SHAPE_CHIPS_MAX_W = 230

    def _row(self, parent: QWidget, label: str, *, tooltip: str = "") -> QWidget:
        """Append a two-column label+widget row to *parent*; return the right cell.

        *tooltip* is set on the row **and its label**, not only on whatever the
        caller puts in the right-hand cell. The label is the word the user reads
        and the larger target; a tooltip that answers only over a narrow entry
        box is one most people never see.
        """
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        lbl = QLabel(label, row)
        lbl.setObjectName("Muted")
        lbl.setFixedWidth(self._ROW_LABEL_W)
        lbl.setWordWrap(True)
        if tooltip:
            row.setToolTip(tooltip)
            lbl.setToolTip(tooltip)
        rl.addWidget(lbl, 0, Qt.AlignTop)

        right = QWidget(row)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rr = QHBoxLayout(right)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(4)
        rl.addWidget(right, 0, Qt.AlignTop)

        parent.layout().addWidget(row)
        return right

    def _entry(self, parent: QWidget, default: str = "", width: int = 80) -> QLineEdit:
        """Create a QLineEdit, add it to *parent*'s layout, and return it."""
        e = QLineEdit(default, parent)
        e.setFixedWidth(width)
        parent.layout().addWidget(e)
        return e
