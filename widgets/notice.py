"""The one notice: something the reader should know about the page, at one of three severities.

The design language (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.6) has a single notice component:

- **information** — something true that changes how the page reads (a page borrowed by the Job Queue, an offer
  to switch to phone layout). The secondary ink.
- **attention** — something the reader should look at before going on (a channel that is not among the ones
  listed, a dataset with no completeness record). Amber.
- **failure** — something that went wrong. Red.

Each is a card with a coloured rule down its left edge and a dot of the same colour beside the text, a **bold
lead** and plain text after it, and at most **one** button. A notice that can be dismissed carries a close
symbol (✕, from the drawn icon set) with a tooltip naming what closing means. Amber is never used for plain
information and never for "in progress"; colour is never the only signal, which is why the lead says it in words.

The sentence wraps, and the button moves under it when the host is too narrow for both on one line — measured
from the sentence as it wraps in the width it would get, through height-for-width, so a font that settles late
cannot leave it cut off.

The colours are written by :func:`qss`, which the application stylesheet includes (``ui.theme.qss``), keyed on
the ``severity`` property; nothing here caches a token.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ui import theme

INFORMATION = "information"
ATTENTION = "attention"
FAILURE = "failure"
SEVERITIES = (INFORMATION, ATTENTION, FAILURE)

#: The narrowest the sentence may be beside the button before the button moves under it.
_MIN_TEXT_W = 260
#: The dot's diameter.
DOT = 8


class Notice(QWidget):
    """A dot, a bold lead and its text, and at most one button; optionally a close symbol."""

    actionTriggered = Signal()
    closed = Signal()

    def __init__(self, lead: str = "", text: str = "", parent: QWidget | None = None, *,
                 severity: str = INFORMATION, action_text: str = "", close_tip: str = "") -> None:
        super().__init__(parent)
        if severity not in SEVERITIES:
            raise ValueError(f"{severity!r} is not a severity; the severities are {SEVERITIES}")
        self.setProperty("notice", True)
        self.setProperty("severity", severity)
        # Without this a QWidget subclass ignores its own background rule, and the notice would read as one more
        # line of the page rather than as a card.
        self.setAttribute(Qt.WA_StyledBackground, True)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        # The content lives on a child: Qt asks a widget that has a layout for that layout's height-for-width,
        # never the widget's own, and this one has to answer from the sentence. resizeEvent sizes the child.
        self._body = QWidget(self)
        self._body.setProperty("noticePart", True)
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(theme.Spacing.md, theme.Spacing.sm, theme.Spacing.md, theme.Spacing.sm)
        self._grid.setHorizontalSpacing(theme.Spacing.sm)
        self._grid.setVerticalSpacing(theme.Spacing.xs)

        self._dot = QLabel(self._body)
        self._dot.setProperty("noticeDot", True)
        self._dot.setProperty("severity", severity)
        self._dot.setFixedSize(DOT, DOT)
        self._grid.addWidget(self._dot, 0, 0, Qt.AlignVCenter)

        self._label = QLabel(self._body)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.RichText)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._grid.addWidget(self._label, 0, 1)
        self._grid.setColumnStretch(1, 1)

        self._buttons = QWidget(self._body)
        self._buttons.setProperty("noticePart", True)
        row = QHBoxLayout(self._buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)
        self._action_btn: QPushButton | None = None
        if action_text:
            from ui.buttons import default
            self._action_btn = default(action_text, self._buttons)
            self._action_btn.clicked.connect(self.actionTriggered)
            row.addWidget(self._action_btn)
        self._close_btn = None
        if close_tip:
            from widgets.icon_button import IconButton
            self._close_btn = IconButton("x", parent=self._buttons, tooltip=close_tip)
            self._close_btn.clicked.connect(self.closed)
            row.addWidget(self._close_btn)
        self._stacked = False
        self._grid.addWidget(self._buttons, 0, 2)
        self._buttons.setVisible(bool(action_text or close_tip))

        self._lead, self._text = "", ""
        self.setMessage(lead, text)

    # ── content ──────────────────────────────────────────────────────────────
    def setMessage(self, lead: str, text: str = "") -> None:  # noqa: N802 — Qt naming
        """The bold lead and the plain text after it."""
        self._lead, self._text = lead, text
        parts = []
        if lead:
            parts.append(f"<b>{html.escape(lead)}</b>")
        if text:
            parts.append(html.escape(text))
        self._label.setText(" ".join(parts))
        self._fit()

    def lead(self) -> str:
        return self._lead

    def text(self) -> str:
        return self._text

    def severity(self) -> str:
        return str(self.property("severity"))

    def setSeverity(self, severity: str) -> None:  # noqa: N802 — Qt naming
        if severity not in SEVERITIES:
            raise ValueError(f"{severity!r} is not a severity; the severities are {SEVERITIES}")
        for w in (self, self._dot):
            w.setProperty("severity", severity)
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    def action_button(self) -> QPushButton | None:
        return self._action_btn

    def close_button(self):
        return self._close_btn

    # ── fitting ──────────────────────────────────────────────────────────────
    def is_stacked(self) -> bool:
        """Whether the button sits under the sentence (a narrow host)."""
        return self._stacked

    def _layout_for(self, width: int) -> tuple[bool, int]:
        """Whether the button goes under the sentence at *width*, and the notice's height there — measured from
        the sentence as it wraps in the width it would actually get, with the fonts as they are now."""
        margins = self._grid.contentsMargins()
        gap = self._grid.horizontalSpacing()
        inner = width - margins.left() - margins.right() - DOT - gap
        buttons = self._buttons.sizeHint() if self._buttons.isVisibleTo(self) else QSize(0, 0)
        stacked = buttons.width() > 0 and inner - buttons.width() - gap < _MIN_TEXT_W
        text_w = max(1, inner if stacked or not buttons.width() else inner - buttons.width() - gap)
        text_h = self._label.heightForWidth(text_w)
        body = (text_h + self._grid.verticalSpacing() + buttons.height() if stacked
                else max(text_h, buttons.height()))
        return stacked, body + margins.top() + margins.bottom()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt naming
        return self._layout_for(width)[1]

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt naming
        width = self._body.sizeHint().width()
        return QSize(width, self.heightForWidth(max(width, self.width())))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt naming
        width = self._body.minimumSizeHint().width()
        return QSize(width, self.heightForWidth(max(width, self.width())))

    def _fit(self) -> None:
        stacked, _height = self._layout_for(self.width())
        if stacked != self._stacked:
            self._stacked = stacked
            self._grid.removeWidget(self._buttons)
            if stacked:
                self._grid.addWidget(self._buttons, 1, 1, Qt.AlignLeft)
            else:
                self._grid.addWidget(self._buttons, 0, 2)
            # Moving the button only schedules a relayout; apply it now, so the notice is never drawn for a frame
            # with the sentence squeezed beside a button that has already moved under it.
            self._grid.activate()
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._body.setGeometry(self.rect())
        self._fit()

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit()


def qss(c, r) -> str:
    """The notice's rules, from the token classes *c* (``Colors``) and *r* (``Radii``). Included by
    ``ui.theme.qss``."""
    ink = {INFORMATION: c.text_secondary, ATTENTION: c.warn, FAILURE: c.danger}
    rules = [f"""
QWidget[notice="true"] {{
    background-color: {c.card}; border: 1px solid {c.hairline}; border-left: 3px solid {c.text_secondary};
    border-radius: {r.md}px;
}}
QWidget[notice="true"] QLabel {{ color: {c.text_primary}; background: transparent; }}
QWidget[noticePart="true"] {{ background: transparent; }}
QLabel[noticeDot="true"] {{ border-radius: {DOT // 2}px; background-color: {c.text_secondary}; }}
"""]
    for severity, colour in ink.items():
        rules.append(f'QWidget[notice="true"][severity="{severity}"] {{ border-left-color: {colour}; }}\n'
                     f'QLabel[noticeDot="true"][severity="{severity}"] {{ background-color: {colour}; }}\n')
    return "".join(rules)
