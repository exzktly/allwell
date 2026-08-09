"""Give a checkbox a width floor its own style agrees with.

``QCheckBox`` sizes itself from ``sizeHint()``, which budgets an indicator, the
``spacing`` gap and the label text. Where a platform style places the label
*further right* than that budget assumed, the tail of the label falls outside
the widget and is clipped -- and because a ``FlowLayout`` packs the next item at
``x + width + spacing``, the neighbouring control butts straight up against the
cut text rather than leaving a gap that would make the clipping obvious.

Reported on macOS for the Distribution tab's ``log x`` / ``log y`` boxes, at
every window width -- so it is the item's own size, not pressure from the row.
This repository has measured the same class of divergence before:
``widgets/flow_layout.py`` records ``QMacStyle`` answering ``layoutSpacing`` and
``-1`` from ``pixelMetric`` while Fusion does the exact reverse, which packed
macOS rows edge to edge while Linux and CI looked fine.

The fix is deliberately **not** a constant. ``theme.Metrics.checkbox_chrome_w``
exists for call sites that run before the stylesheet is applied and cannot use
``sizeHint()`` at all, and its own comment records why a constant cannot solve
this one: the shortfall is per-style and per-context, and an earlier attempt to
compensate with a fixed ``margin`` on the indicator was removed for stacking on
top of ``spacing`` everywhere it was not needed.

So the shortfall is asked of the style that will actually paint the widget.
``SE_CheckBoxContents`` is the rect the style reserves for the label; when it is
narrower than the text needs, the difference is exactly how much the widget is
short by, and the floor is raised by that much. Measured on Fusion the reserve
runs 6px wider than ``log x`` needs, so this is a no-op there -- it can only ever
add the space a style is already clipping away.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton


def effective_width(cb: QCheckBox) -> int:
    """The width a layout will give *cb*.

    ``FlowLayout`` places each item at ``sizeHint().expandedTo(minimumSize())``,
    so a floor already applied is part of the width the label gets — measuring
    at the bare ``sizeHint()`` would report a shortfall this helper has already
    closed.
    """
    return max(cb.sizeHint().width(), cb.minimumWidth())


def label_shortfall(cb: QCheckBox) -> int:
    """Pixels of *cb*'s label that its own width cannot show. <= 0 means it fits.

    Positive only when the style reserves less room for the label than the text
    needs at the width the widget will actually be given.
    """
    text = cb.text()
    if not text:
        # A text-less indicator (the WrapCheckBox composite draws its label in a
        # separate widget) has nothing to clip.
        return 0
    cb.ensurePolished()
    opt = QStyleOptionButton()
    cb.initStyleOption(opt)
    width = effective_width(cb)
    opt.rect = QRect(0, 0, width, cb.sizeHint().height())
    contents = cb.style().subElementRect(QStyle.SE_CheckBoxContents, opt, cb)
    return cb.fontMetrics().horizontalAdvance(text) - contents.width()


def fit_checkbox_width(cb: QCheckBox) -> int:
    """Raise *cb*'s minimum width so its label fits under the current style.

    Returns the number of pixels added (0 when the label already fits, which is
    every case on Fusion). Call after the widget has its final text; the floor
    is computed once and does not follow a later ``setText``.
    """
    short = label_shortfall(cb)
    if short <= 0:
        return 0
    cb.setMinimumWidth(effective_width(cb) + short)
    return short
