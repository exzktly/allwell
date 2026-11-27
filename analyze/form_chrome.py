"""The Analyze form's own chrome — sections, rows, entries, info markers.

``AnalyzeTabFormMixin`` inherits :class:`AnalyzeFormChromeMixin` for the small
vocabulary every ``_build_*`` section is written in: a collapsible titled
section, a two-column label+widget row, a fixed-width entry, and the hover ``ⓘ``
marker that keeps long explanatory notes off the form. Split out of
``analyze/form.py`` for its line budget; the methods bind to ``self``
exactly as before, so this is a pure relocation.

The optional sections fold by default so the required fields lead the form.
:meth:`AnalyzeFormChromeMixin.reveal_section` is the counterpart for the case
that discipline gets wrong: a control changed on the user's behalf, inside a
folded section, leaves no visible trace of having happened at all.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget,
)

from ui import text_roles
from ui import theme
from widgets.collapsible_section import CollapsibleSection

_QWIDGETSIZE_MAX = 16_777_215   # Qt's QWIDGETSIZE_MAX: a label column with no ceiling


class _WidthWatcher(QObject):
    """Calls back whenever the widget it is installed on changes width.

    A folded section's summary is cut to the room its header leaves it, and that
    room is not settled when the text is first set: the header is laid out, a
    scroll bar appears, the window is resized. Elided once, the text kept the
    cut made for the earlier width and ran past a label that had since narrowed
    (98 px of text in 86 px, on the slow lane's runner). The label's size policy
    ignores its text, so setting the text again cannot change its width and
    start over.

    An event filter rather than a ``QLabel`` subclass, so the label stays the
    plain ``QLabel#Muted`` every style rule and layout record names.
    """

    def __init__(self, label, on_width_changed) -> None:
        super().__init__(label)
        self._on_width_changed = on_width_changed
        label.installEventFilter(self)

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.Resize and event.size().width() != event.oldSize().width():
            self._on_width_changed()
        return False


class AnalyzeFormChromeMixin:
    """Section / row / entry / info-marker builders for the Analyze form."""

    def _section(self, parent: QWidget, title: str, info: str = "",
                 *, collapsible: bool = False, collapsed: bool = False,
                 switch: QWidget | None = None,
                 summary=None) -> QWidget:
        """Add a labelled section to *parent* and return its body widget.

        When *info* is given, a small ``ⓘ`` marker is placed next to the title
        and the text is shown as its hover tooltip — keeping long explanatory
        notes available on demand instead of cluttering the form inline.

        When *collapsible*, the header carries a ▾/▸ caret and clicking it
        shows/hides the body; *collapsed* sets the initial state. The optional
        sections fold by default so the required ones lead the form.

        *switch* is a section's own on/off control, put in the header beside
        the title so it can be read and changed with the section folded; its
        clicks are its own and do not fold the section. *summary* is a
        callable returning a short muted line shown after the title while the
        section is folded — what the fold is hiding (see
        ``analyze.section_summaries``).
        """
        # A card: the app's one collapsible section (``widgets.collapsible_section``), unanimated so a caller
        # reading the body's visibility straight after a toggle reads the new state. A section that does not
        # fold is the same card with no chevron.
        container = CollapsibleSection(title, parent, expanded=not collapsed, collapsible=collapsible,
                                       animated=False)
        # Preferred, not the widget's Maximum: a section's wrapping rows are taller at the form's width than the
        # section's plain size hint, and Maximum caps it at that hint, clipping them.
        container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        body = container.body()
        caret = container.chevron()
        if info:
            container.addHeaderWidget(self._info_icon(container.header(), info))
        if switch is not None:
            container.addHeaderWidget(switch)
        summary_lbl = QLabel("", container.header())
        _WidthWatcher(summary_lbl, lambda _title=title: self._refresh_section_summary(_title))
        summary_lbl.setProperty("textRole", "help")
        summary_lbl.setMinimumWidth(0)
        summary_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        summary_lbl.hide()
        # The summary takes the header's spare width when it shows (a hidden widget takes no part in the layout).
        container.addHeaderWidget(summary_lbl, 20)

        if collapsible:
            container.toggled.connect(lambda _open, _title=title: self._refresh_section_summary(_title))
            # Recorded so a caller that changes a control inside a folded
            # section can show what it did — see reveal_section.
            if not hasattr(self, "_collapsible_sections"):
                self._collapsible_sections = {}
            self._collapsible_sections[title] = (body, caret)
            if not hasattr(self, "_section_cards"):
                self._section_cards = {}
            self._section_cards[title] = container

        if summary is not None:
            if not hasattr(self, "_section_summaries"):
                self._section_summaries = {}
            self._section_summaries[title] = (summary, summary_lbl, body)

        # Every section, collapsible or not, is recorded in build order so
        # current_help_section can say which one the user has scrolled to.
        if not hasattr(self, "_section_widgets"):
            self._section_widgets = {}
        self._section_widgets[title] = container

        parent.layout().addWidget(container)
        return body

    def _refresh_section_summary(self, title: str) -> None:
        """Redraw *title*'s folded summary: shown while folded, hidden open."""
        entry = getattr(self, "_section_summaries", {}).get(title)
        if entry is None:
            return
        summary, label, body = entry
        text = ""
        card = getattr(self, "_section_cards", {}).get(title)
        if card is not None and not card.isExpanded():
            from core.safe import swallow
            with swallow(f"summarising the {title} section"):
                text = summary() or ""
        # Elided to the width the header leaves it: a schema or a well list
        # can be longer than the panel, and a clipped word reads as a typo.
        # The room is the label's contents rectangle, not its width: the label
        # carries a left margin, and text elided to the full width overran it.
        width = label.contentsRect().width()
        shown = (label.fontMetrics().elidedText(text, Qt.ElideRight, width)
                 if text and width > 40 else text)
        label.setText(shown)
        label.setToolTip(text)
        label.setVisible(bool(text))

    def refresh_section_summaries(self) -> None:
        """Redraw every folded section's summary."""
        for title in list(getattr(self, "_section_summaries", {})):
            self._refresh_section_summary(title)

    def current_help_section(self) -> str:
        """Title of the form section the user is working in, or ``""``.

        The Analyze pane is one tall scrolling form rather than a stack of
        pages, so there is no "current page" to read off a stack. Two signals
        answer it instead, in order:

        1. **Keyboard focus.** If a control inside a section has focus, that is
           the section, whatever the scroll position says. This is the only
           signal that can name the short sections packed at the foot of the
           form — see the limit below.
        2. **The section at the top of the viewport**: among sections with any
           part of themselves on screen, the one whose *header* is nearest the
           viewport top. Nearest, not "the first one showing": a section whose
           body is three screens tall is still on screen by its last few pixels
           long after the user has scrolled past everything it says, and naming
           it there is the same defect as naming the last header above the top.

        The limit worth knowing: the scrollbar's travel is the form's height
        minus one viewport, so the final screenful can never be scrolled to the
        top. Every section in it shares whichever of them is topmost, unless
        focus says otherwise. Geometry cannot separate sections the user is
        looking at simultaneously.

        Returns ``""`` — meaning "fall back to the mode overview" — before the
        form is built, when the pane has no scroll area, or when the sidebar is
        collapsed.
        """
        sections = getattr(self, "_section_widgets", None)
        scroll = getattr(self, "_left_sidebar", None)
        if not sections or scroll is None:
            return ""
        viewport = scroll.viewport() if hasattr(scroll, "viewport") else None
        if viewport is None:
            return ""

        focused = self._section_holding_focus(sections)
        if focused:
            return focused

        best_title, best_distance = "", None
        for title, container in sections.items():
            if container is None or not container.isVisible():
                continue
            # mapTo across the widget tree rather than reading y() directly:
            # each section sits inside the form widget, not the viewport.
            top = container.mapTo(viewport, container.rect().topLeft()).y()
            if top + container.height() <= 0 or top >= viewport.height():
                continue                      # entirely above / below the fold
            distance = abs(top)
            if best_distance is None or distance < best_distance:
                best_title, best_distance = title, distance
        return best_title

    @staticmethod
    def _section_holding_focus(sections: dict) -> str:
        """Title of the section containing the focused widget, or ``""``.

        Walks up from the focus widget rather than down from each section: the
        form nests rows inside cells inside bodies, so ``isAncestorOf`` on the
        way up is one pass whatever the depth.
        """
        from PySide6.QtWidgets import QApplication

        focus = QApplication.focusWidget()
        if focus is None:
            return ""
        for title, container in sections.items():
            if container is not None and container.isAncestorOf(focus):
                return title
        return ""

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
        self._section_cards[title].setExpanded(True)
        self._refresh_section_summary(title)

    def _info_icon(self, parent: QWidget, info: str) -> QLabel:
        """A small ``ⓘ`` marker whose hover tooltip carries *info*.

        Keeps long explanatory notes available on demand instead of cluttering
        the form with inline paragraphs. Delegates to ``widgets.info_icon`` so
        the Review pane's tabs raise the same marker rather than a lookalike.
        """
        from widgets.info_icon import info_icon
        return info_icon(parent, info)

    # Control widths for the Analyze form. The form is one column — every
    # label sits above its field — so a field takes the panel's whole width
    # and nothing here has to be squeezed beside a label column. Two widths
    # remain (``_normalise_field_widths``): the full width for text, paths and
    # dropdowns, and ``_NUM_FIELD_W`` for a number.
    _ADD_BTN_W = 130
    _AUTO_THRESHOLD_METHOD_CB_W = 165
    _CB_W = 160
    _NUM_FIELD_W = 80
    _REMOVE_BTN_W = 36
    _RIGHT_MIN_W = 300
    _SCHEMA_STR_EDIT_MIN_W = 165
    _SEG_METHOD_CB_W = 165
    _SHAPE_CHIPS_MAX_W = 230

    def _row(self, parent: QWidget, label: str, *, tooltip: str = "") -> QWidget:
        """Append a form-grid row to *parent* — its label in the label column, the field beside it — and return
        the field cell.

        The design language's form grid (§6, *Analyze*): at the desk the label sits in a column
        ``theme.Metrics.form_label_w`` wide, beside the field, in the form column ``Metrics.form_column_w``; in
        phone layout the row turns to label-above-field, the one arrangement a phone's width holds. The row says
        so where it is built (``widgets.form_registry.register``), so phone layout needs to recognise nothing.

        *tooltip* is set on the row **and its label**, not only on whatever the
        caller puts in the field cell. The label is the word the user reads and
        the larger target; a tooltip that answers only over a narrow entry box
        is one most people never see.
        """
        from widgets import form_registry

        row = QWidget(parent)
        # Named so ``_normalise_field_widths`` and the pane's own builders can find the form's rows.
        row.setObjectName("AnalyzeFormRow")
        rl = QBoxLayout(QBoxLayout.LeftToRight, row)
        rl.setContentsMargins(0, theme.Spacing.xs, 0, theme.Spacing.xs)

        lbl = QLabel(label, row)
        lbl.setProperty("textRole", "help")
        lbl.setWordWrap(True)
        if tooltip:
            row.setToolTip(tooltip)
            lbl.setToolTip(tooltip)
        rl.addWidget(lbl)

        right = QWidget(row)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rr = QHBoxLayout(right)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(theme.Spacing.xs)
        rl.addWidget(right, 1)

        def _arrange(phone: bool, _rl=rl, _lbl=lbl) -> None:
            _rl.setDirection(QBoxLayout.TopToBottom if phone else QBoxLayout.LeftToRight)
            _rl.setSpacing(theme.Spacing.xs if phone else theme.Spacing.md)
            if phone:
                _lbl.setMinimumWidth(0)
                _lbl.setMaximumWidth(_QWIDGETSIZE_MAX)
            else:
                _lbl.setFixedWidth(theme.Metrics.form_label_w)
            _rl.setAlignment(_lbl, Qt.AlignLeft | (Qt.AlignBottom if phone else Qt.AlignVCenter))

        _arrange(False)
        form_registry.register(row, _arrange)
        parent.layout().addWidget(row)
        return right

    def _normalise_field_widths(self, host: QWidget) -> None:
        """Give every row's fields one of two widths, after the form is built.

        Dropdowns and text or path fields take the row's whole width; a number
        field takes ``_NUM_FIELD_W``. Builders were written for a label column
        and pinned widths one by one (160, 165, 106, 118, 183…), so the right
        edges never lined up; doing it here, once, keeps the rule in one place
        rather than in forty call sites. A field narrower than 40 px (the
        one-character separator box) keeps its width, and a combo inside a
        composite widget (the schema picker's position combos) is reached the
        same way, since each sits in a row of its own.
        """
        from PySide6.QtWidgets import QComboBox, QLineEdit, QSpacerItem

        for row in host.findChildren(QWidget, "AnalyzeFormRow"):
            lay = row.layout()
            if lay is None or lay.count() < 2:
                continue
            cell = lay.itemAt(1).widget()
            cell_lay = cell.layout() if cell is not None else None
            if cell_lay is None:
                continue
            widened = False
            for i in range(cell_lay.count()):
                w = cell_lay.itemAt(i).widget()
                if isinstance(w, QComboBox):
                    w.setMinimumWidth(0)
                    w.setMaximumWidth(16_777_215)
                    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                    w.setMinimumContentsLength(4)
                    cell_lay.setStretch(i, 1)
                    widened = True
                elif isinstance(w, QLineEdit) and w.minimumWidth() == w.maximumWidth():
                    fixed = w.maximumWidth()
                    if fixed < 40:
                        continue
                    if fixed <= 90:
                        w.setFixedWidth(self._NUM_FIELD_W)
                    else:
                        w.setMinimumWidth(0)
                        w.setMaximumWidth(16_777_215)
                        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                        cell_lay.setStretch(i, 1)
                        widened = True
            if widened:
                for i in reversed(range(cell_lay.count())):
                    item = cell_lay.itemAt(i)
                    if isinstance(item, QSpacerItem):
                        cell_lay.takeAt(i)

    @staticmethod
    def _set_status(label: QLabel, text: str, *, error: bool = False,
                    quiet: bool = True) -> None:
        """Show *text* on a form status *label*, styled for what it says.

        An empty label is hidden, not left blank: a blank label still takes its
        line and margins, which is how the Folders section came to carry two
        unexplained gaps. A label carrying a problem is help text in the
        failure tone, and anything else help text (*quiet*) or body text,
        because a refusal set in the same grey as the help paragraph beside it
        reads as more help (``ui/text_roles.py``).
        """
        label.setText(text)
        label.setVisible(bool(text))
        role = text_roles.HELP if (quiet or error) else text_roles.BODY
        text_roles.set_role(label, role, tone=text_roles.FAILURE if error else None)

    def _subheading(self, sec: QWidget, text: str, *, info: str = "") -> QWidget:
        """A heading inside a section, above the rows it groups ("Output",
        "Performance", "Channels in the filenames"). One style for every such
        heading, so a group inside a section never reads as a section."""
        head = QWidget(sec)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, theme.Spacing.sm, 0, theme.Spacing.xs)
        hl.setSpacing(theme.Spacing.xs)
        lbl = QLabel(text, head)
        lbl.setObjectName("FormSubheading")
        hl.addWidget(lbl)
        if info:
            hl.addWidget(self._info_icon(head, info))
        hl.addStretch(1)
        sec.layout().addWidget(head)
        return head

    @staticmethod
    def _field_note(cell: QWidget, text: str) -> QLabel:
        """A short muted note after a field in a ``_row`` cell — a unit, or what
        ``0`` means. It sits after the field rather than in the label, so the
        label stays a name and does not wrap mid-parenthesis at the rail width."""
        note = QLabel(text, cell)
        note.setProperty("textRole", "help")
        cell.layout().addWidget(note)
        return note

    def _entry(self, parent: QWidget, default: str = "", width: int = 80) -> QLineEdit:
        """Create a QLineEdit, add it to *parent*'s layout, and return it."""
        e = QLineEdit(default, parent)
        e.setFixedWidth(width)
        parent.layout().addWidget(e)
        return e
