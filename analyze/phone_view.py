"""Analyze in phone layout: the form is the page, the run is a strip above it.

At the desk the Analyze pane is a splitter: the form on the left, the run
dashboard and the log on the right. Phone layout used to lend the form to the
☰ drawer, so Analyze opened on an idle dashboard and an empty log with nothing
on screen saying where its settings were, and the Run button sat at the foot of
a ~3,500 px column inside the drawer.

In phone layout :class:`AnalyzePhoneView` rearranges the same widgets — nothing
is rebuilt — into two views, picked from the page button (``PhoneShell``):

* **Settings** — the run dashboard reduced to its headline and overall bar
  (``PlateProgress.setCompact``), then the form, filling the page;
* **Progress** — the full dashboard (stacked) and the log, in a scroll area.
  At full phone width the plate alone is ~540 px tall, so on a short screen
  the dashboard and log need more height than the page has. Without the scroll
  area the column was squeezed below its minimum: the dashboard was cut short
  and the log painted over the lower half of the plate, with no way to reach
  it. Scrolled, the plate keeps its size and the log follows it.

The run controls (Run, Suspend, + Queue, Stop) leave the foot of the form for
a bar along the bottom of the pane, in reach in both views. A run starting
shows Progress. Leaving phone layout puts every widget back where it was.

``AnalyzeTab`` builds the view (``tab._phone_view``) and the view listens for
phone layout itself (``core.phone_layout`` listeners), so the shell — which
imports no pane — only needs the view names and a way to show one
(``PhoneHandles.page_views`` and friends, which ``allwell.py`` reads off the
pane).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QSplitter, QWidget

from core import phone_layout
from core.panes import PANEL_IS_PAGE
from core.safe import swallowed

SETTINGS = "Settings"
PROGRESS = "Progress"
VIEWS = (SETTINGS, PROGRESS)

_QWIDGETSIZE_MAX = 16_777_215


class _RunStartWatcher(QObject):
    """Shows Progress when a run starts: the Stop button turning enabled is
    the one signal every way of starting a run shares (Run, the queue, a
    resumed job)."""

    def __init__(self, button: QWidget, view: AnalyzePhoneView) -> None:
        super().__init__(button)
        self._view = view

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if isinstance(ev, QEvent) and ev.type() == QEvent.EnabledChange \
                and obj.isEnabled():
            self._view.run_started()
        return False


class AnalyzePhoneView:
    """Analyze's phone form; see the module docstring."""

    def __init__(self, tab) -> None:
        self._tab = tab
        self._on = False
        self._view = SETTINGS
        self._home: dict | None = None
        # Holds the run column in the Progress view (see the module docstring).
        # Settings keeps the column directly in the splitter, sized to the
        # strip as before: a scroll area there would need a pinned height, and
        # the strip's height is only known after the shell's phone restyle.
        # Built up front, as the pane's child, so the shell's drag-to-scroll
        # sweep (``swipe_roots``) finds it like the form's own scroll area.
        self._scroll = QScrollArea(tab)
        self._scroll.setObjectName("PhoneRunScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.hide()
        stop = getattr(tab, "_stop_btn", None)
        if stop is not None:
            stop.installEventFilter(_RunStartWatcher(stop, self))
        phone_layout.add_listener(self._on_layout)
        tab.destroyed.connect(lambda *_: phone_layout.remove_listener(self._on_layout))

    # ── what the shell asks ─────────────────────────────────────────────────
    def views(self) -> list[str]:
        return list(VIEWS)

    def current(self) -> str:
        return self._view

    def show_view(self, name: str) -> None:
        if name not in VIEWS:
            return
        self._view = name
        if self._on:
            self._apply_view()

    def run_started(self) -> None:
        if self._on:
            self.show_view(PROGRESS)

    # ── the parts ──────────────────────────────────────────────────────────
    def _parts(self) -> tuple[QWidget, QSplitter, QWidget | None, QWidget] | None:
        """The form, the splitter, the run column and the run row.

        In the Progress view the splitter holds the scroll area rather than
        the run column; the column returned is still the one inside it.
        """
        tab = self._tab
        form = tab._left_sidebar
        splitter = form.parentWidget()
        if not isinstance(splitter, QSplitter):
            return None
        right = next((splitter.widget(i) for i in range(splitter.count())
                      if splitter.widget(i) is not form), None)
        if right is self._scroll:
            right = self._scroll.widget()
        run_row = tab._run_btn.parentWidget()
        return form, splitter, right, run_row

    def _mount(self, splitter: QSplitter, right: QWidget, scrolled: bool) -> None:
        """Put the run column first in *splitter*: inside the scroll area when
        *scrolled*, directly otherwise. A no-op when it is already there."""
        if scrolled:
            if self._scroll.widget() is not right:
                splitter.replaceWidget(splitter.indexOf(right), self._scroll)
                self._scroll.setWidget(right)
            right.show()
            self._scroll.show()
            splitter.insertWidget(0, self._scroll)
            return
        if self._scroll.widget() is right:
            # Out of the scroll area first, so the splitter takes back a free
            # widget rather than one the scroll area still owns.
            self._scroll.takeWidget()
            splitter.replaceWidget(splitter.indexOf(self._scroll), right)
            self._scroll.setParent(self._tab)
            self._scroll.hide()
            right.show()
        splitter.insertWidget(0, right)

    def _on_layout(self, on: bool) -> None:
        try:
            self.set_on(on)
        except RuntimeError:
            swallowed()  # the pane went before phone layout did

    def set_on(self, on: bool) -> None:
        on = bool(on)
        if on == self._on:
            return
        parts = self._parts()
        if parts is None or parts[2] is None:
            return
        form, splitter, right, run_row = parts
        if on:
            row_lay = run_row.parentWidget().layout()
            self._home = {
                "form_w": (form.minimumWidth(), form.maximumWidth()),
                "orientation": splitter.orientation(),
                "sizes": splitter.sizes(),
                "handle": splitter.handleWidth(),
                "row_parent": run_row.parentWidget(),
                "row_index": row_lay.indexOf(run_row) if row_lay is not None else -1,
                "row_policy": run_row.sizePolicy(),
            }
            form.setProperty(PANEL_IS_PAGE, True)
            form.setMinimumWidth(0)
            form.setMaximumWidth(_QWIDGETSIZE_MAX)
            splitter.setOrientation(Qt.Vertical)
            splitter.insertWidget(0, right)
            splitter.setHandleWidth(0)
            splitter.setChildrenCollapsible(False)
            run_row.setObjectName("PhoneRunBar")
            run_row.setAttribute(Qt.WA_StyledBackground, True)
            run_row.setSizePolicy(run_row.sizePolicy().horizontalPolicy(),
                                  QSizePolicy.Maximum)
            self._tab.layout().addWidget(run_row)
            self._on = True
            self._apply_view()
            return
        home, self._home = self._home, None
        self._on = False
        right.setMaximumHeight(_QWIDGETSIZE_MAX)
        self._mount(splitter, right, scrolled=False)
        self._tab._dashboard.setCompact(False)
        form.setProperty(PANEL_IS_PAGE, None)
        form.setVisible(True)
        self._tab._on_toggle_raw_log(self._tab._raw_log_cb.isChecked())
        self._tab._refresh_last_run_summary()
        if home is None:
            return
        splitter.setOrientation(home["orientation"])
        splitter.insertWidget(0, form)
        splitter.setHandleWidth(home["handle"])
        form.setMinimumWidth(home["form_w"][0])
        form.setMaximumWidth(home["form_w"][1])
        splitter.setSizes(home["sizes"])
        run_row.setObjectName("")
        run_row.setSizePolicy(home["row_policy"])
        lay = home["row_parent"].layout()
        if lay is not None:
            index = home["row_index"]
            lay.insertWidget(index if index >= 0 else lay.count(), run_row)

    def _apply_view(self) -> None:
        parts = self._parts()
        if parts is None:
            return
        form, splitter, right, _run_row = parts
        tab = self._tab
        settings = self._view == SETTINGS
        self._mount(splitter, right, scrolled=not settings)
        form.setVisible(settings)
        tab._dashboard.setCompact(settings)
        if settings:
            for name in ("_log", "_log_sep", "_last_run_card"):
                w = getattr(tab, name, None)
                if w is not None:
                    w.setVisible(False)
            if right.layout() is not None:
                right.layout().activate()
            strip = right.sizeHint().height()
            right.setMaximumHeight(strip)
            splitter.setSizes([strip, _QWIDGETSIZE_MAX])
        else:
            right.setMaximumHeight(_QWIDGETSIZE_MAX)
            tab._on_toggle_raw_log(tab._raw_log_cb.isChecked())
            if not getattr(tab, "_running", False):
                tab._refresh_last_run_summary()
