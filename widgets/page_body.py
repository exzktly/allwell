"""The standard body of a main page: a control band, then the page's content.

Every main page in the app — each Review page, the Analyze pane's output side,
the Datasets pane — is built the same way:

* a :class:`~widgets.control_band.ControlBand` flush against the very top of the
  page, one row tall (:meth:`ControlBand.band_height`), and
* the page's own content directly beneath it.

The band is added whether or not the page has anything to put in it. That is the
point of this helper: a page with no controls reserves the band's height rather
than starting its content higher than its neighbours, so the top edge of a page's
first row is drawn at the same y wherever the user navigates. The mode band above
already works this way — ``app_shell_view.build_page_tabs_host`` keeps its height
on pages that have no sub-tab bar for exactly the same reason.

The top margin is not a parameter. Left, right and bottom padding are a page's
own business (a plot canvas wants a gutter, a table does not), but the top is
what alignment is made of, so it is always zero and the band always sits against
it. A page that sets its own top inset is the drift this helper exists to remove.

No All-Well coupling — a plain reusable helper, usable from the Review feature
packages, ``analyze/log_pane.py`` and ``datasets/tab.py`` alike.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from widgets.control_band import ControlBand


def build_page_body(
    parent: QWidget,
    *,
    content_margins: tuple[int, int, int] = (0, 0, 0),
    horizontal: bool = False,
) -> tuple[ControlBand, QWidget]:
    """Lay *parent* out as a page body and return its ``(band, content host)``.

    *parent*'s own layout is created if it has none, and is stripped of margins
    and spacing either way — the band paints its own padding, and anything the
    page adds above or around it is what makes one page start lower than the
    next.

    *content_margins* is ``(left, right, bottom)`` for the content host; its top
    is always zero. Pass *horizontal* for a page whose content is a row rather
    than a column (the Playground's canvas + property panel, for instance).

    Add the page's controls to the returned band and its content to the returned
    host's layout.
    """
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    band = ControlBand(parent)
    layout.addWidget(band)

    host = QWidget(parent)
    left, right, bottom = content_margins
    host_layout = QHBoxLayout(host) if horizontal else QVBoxLayout(host)
    host_layout.setContentsMargins(left, 0, right, bottom)
    layout.addWidget(host, 1)
    return band, host
