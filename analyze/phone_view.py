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
* **Progress** — the full dashboard (stacked) and the log, fitted to the page
  with no scrolling of the page itself. At full phone width the plate alone
  is ~540 px tall, more than a short screen has to spare once the dashboard's
  headline, the stage rail, the log and the run bar are counted, so the plate
  gives way: :meth:`AnalyzePhoneView._fit_progress` caps its width and height
  together, so its wells stay round, at whatever leaves the log at least
  :data:`LOG_MIN_LINES` lines. The log scrolls inside its own box; nothing
  else on the page does. Sideways the shell lays the log beside the dashboard
  and the stage rail beside the plate (``PhoneShell._apply_analyze_columns``,
  ``PlateProgress.setRailBeside``), and the same fit holds the plate inside
  the left column's height.

  Not a scroll area: a page that scrolls as a whole puts its scroll bar beside
  the log's own, where it reads as part of the log box, and the log's foot is
  then hidden under the run bar until the page is scrolled to its end.

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

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QSizePolicy, QSplitter, QWidget

from core import phone_layout
from core.panes import PANEL_IS_PAGE
from core.safe import swallowed

SETTINGS = "Settings"
PROGRESS = "Progress"
VIEWS = (SETTINGS, PROGRESS)

_QWIDGETSIZE_MAX = 16_777_215

#: The fewest log lines Progress keeps on screen when the plate has to shrink
#: to fit the page: enough to read the last few lines of a run's output.
LOG_MIN_LINES = 3

#: The smallest well the fit shrinks the plate to, as a fraction of the
#: plate's label line height: still a tappable, readable dot.
PLATE_MIN_CELL_LINES = 0.4


class _RefitOnLayout(QObject):
    """Refits Progress whenever the run column is resized or re-laid out."""

    def __init__(self, column: QWidget, view: AnalyzePhoneView) -> None:
        super().__init__(column)
        self._view = view

    def eventFilter(self, obj, ev) -> bool:  # Qt naming
        if isinstance(ev, QEvent) and ev.type() in (QEvent.Resize, QEvent.LayoutRequest):
            self._view.schedule_fit()
        return False


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
        self._fit_pending = False
        self._fitting = False
        self._watched: QWidget | None = None
        self._log_min: int | None = None
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
        """The form, the splitter, the run column and the run row."""
        tab = self._tab
        form = tab._left_sidebar
        splitter = form.parentWidget()
        if not isinstance(splitter, QSplitter):
            return None
        right = next((splitter.widget(i) for i in range(splitter.count())
                      if splitter.widget(i) is not form), None)
        run_row = tab._run_btn.parentWidget()
        return form, splitter, right, run_row

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
        self._release_fit()
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
        if self._watched is None:
            self._watched = right
            right.installEventFilter(_RefitOnLayout(right, self))
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
            self._fit_progress()

    # ── fitting Progress to the page ───────────────────────────────────────
    def _fitting_progress(self) -> bool:
        return self._on and self._view == PROGRESS

    def schedule_fit(self) -> None:
        """Refit on the next event-loop pass: a resize or re-layout arrives in
        bursts, and the fit itself re-lays the column."""
        if self._fit_pending or self._fitting or not self._fitting_progress():
            return
        self._fit_pending = True
        QTimer.singleShot(0, self._run_scheduled_fit)

    def _run_scheduled_fit(self) -> None:
        self._fit_pending = False
        try:
            self._fit_progress()
        except RuntimeError:
            swallowed()  # the pane went while the fit was queued

    def _log_reserve(self) -> int:
        """The log's minimum height in Progress: :data:`LOG_MIN_LINES` lines
        plus its frame, measured in its own font."""
        log = self._tab._log
        frame = 2 * log.frameWidth()
        margins = log.contentsMargins()
        return (log.fontMetrics().lineSpacing() * LOG_MIN_LINES + frame
                + margins.top() + margins.bottom()
                + round(2 * log.document().documentMargin()))

    def _fit_progress(self) -> None:
        """Cap the plate so the whole Progress page fits without scrolling.

        The plate's height follows its width (``heightForWidth``), and it holds
        that height as a floor (``WellPlateSelector.resizeEvent``), so a column
        shorter than the plate needs is not shared out: the plate keeps its
        height and everything under it is pushed off the page. So the plate is
        capped instead, by width *and* height at the same aspect — a height cap
        alone at full width squeezes the wells and runs the row and column
        labels into one another.

        The budget is read from the column's own minimum height, which counts
        every visible part at its floor: the log at :meth:`_log_reserve` (when
        it is under the plate; beside it, sideways, it does not add up), the
        headline, the stage rail, the plate at its present floor. What the
        column has beyond that minimum is what the plate may grow by; what it
        lacks, the plate gives up, down to :data:`PLATE_MIN_CELL_LINES` of the
        plate's own label height per well.
        """
        if not self._fitting_progress() or self._fitting:
            return
        parts = self._parts()
        plate = getattr(self._tab._dashboard, "_plate", None)
        if parts is None or plate is None or not plate.isVisible():
            return
        right = parts[2]
        log = self._tab._log
        self._fitting = True
        try:
            if self._log_min is None:
                self._log_min = log.minimumHeight()
            log.setMinimumHeight(self._log_reserve())
            full_w = self._tab._dashboard.plate_room_width()
            natural = plate.heightForWidth(full_w)
            floor = plate.heightForWidth(self._narrowest(plate))
            for _ in range(3):
                self._relayout(plate, right)
                spare = right.height() - right.minimumSizeHint().height()
                budget = max(floor, min(natural, plate.minimumHeight() + spare))
                width = self._width_for_height(plate, budget, full_w)
                height = plate.heightForWidth(width)
                if (plate.maximumWidth(), plate.maximumHeight()) == (width, height):
                    break
                plate.setMaximumSize(width, height)
                plate.setMinimumHeight(min(plate.minimumHeight(), height))
            self._relayout(plate, right)
            # `_relayout` invalidates the column's layout, which posts the column a LayoutRequest that is delivered
            # after `_fitting` is down. The layout has just been activated, so the request asks for nothing, but
            # `_RefitOnLayout` read it as a re-layout and scheduled the next fit, which posted the next request: the
            # page re-fitted on every event-loop pass, at the same size, for as long as Progress was on screen.
            QCoreApplication.removePostedEvents(right, QEvent.LayoutRequest)
        finally:
            self._fitting = False

    @staticmethod
    def _relayout(plate: QWidget, column: QWidget) -> None:
        """Lay out every widget from the column down to the plate now, outermost
        first, so the next measurement reads the caps just set rather than the
        geometry of the pass before."""
        chain = []
        w = plate.parentWidget()
        while w is not None:
            chain.append(w)
            if w is column:
                break
            w = w.parentWidget()
        for w in reversed(chain):
            lay = w.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()

    @staticmethod
    def _narrowest(plate: QWidget) -> int:
        """The narrowest the fit makes the plate: its 12 columns at
        :data:`PLATE_MIN_CELL_LINES` of a label line each, plus the row labels.
        Below that a well is a speck, and the page scrolls nothing anyway."""
        line = plate.fontMetrics().height()
        return round(line * (1 + 12 * PLATE_MIN_CELL_LINES))

    @staticmethod
    def _width_for_height(plate: QWidget, height: int, full_w: int) -> int:
        """The widest plate, up to *full_w*, whose height fits *height*."""
        lo, hi = 1, max(1, full_w)
        if plate.heightForWidth(hi) <= height:
            return hi
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if plate.heightForWidth(mid) <= height:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _release_fit(self) -> None:
        """Hand the plate and the log back their desk sizes."""
        tab = self._tab
        if self._log_min is not None:
            tab._log.setMinimumHeight(self._log_min)
            self._log_min = None
        dash = tab._dashboard
        plate = getattr(dash, "_plate", None)
        if plate is None:
            return
        if dash.isStacked():
            plate.setMaximumSize(_QWIDGETSIZE_MAX, _QWIDGETSIZE_MAX)
        else:
            from widgets.plate_geometry import apply_plate_geometry
            apply_plate_geometry(plate, role="status")
