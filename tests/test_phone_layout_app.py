"""Phone layout through the real window: the real button, the real panes.

Slow lane — this constructs the whole ``AllWellApp`` once for the file, and
each switch into or out of phone layout re-applies the application stylesheet
(about 6 s on a built app). ``tests/test_phone_layout.py`` pins the parts in
the fast lane; this file is the one that would notice the parts not being
wired together — a button connected to nothing, a pane whose sidebar comes
back on the next resize, a desktop that does not come back at all.

The offscreen screen is 800×800, narrower than the desk window's 1100 minimum,
so it is phone-shaped: the window must fit it in either layout. Its unit is
800 / 390 px per phone point, which every touch size is checked against.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QBoxLayout, QPushButton, QWidget

pytestmark = pytest.mark.slow


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture(scope="module")
def win(qapp):
    from allwell import AllWellApp
    w = AllWellApp(None)
    w.show()
    _pump(qapp)
    yield w
    from core import phone_layout
    from ui import theme
    phone_layout.set_on(False)
    theme.apply_ui_scale(1.0)
    w.close()


def _visible(w) -> bool:
    return w is not None and w.isVisibleTo(w.window())


def test_phone_layout_round_trip_through_the_real_controls(qapp, win):
    from core import phone_layout
    from ui import theme

    review = win._review
    shell = win._phone
    avail = win.screen().availableGeometry()

    # At the desk, on a phone-shaped screen: the window fits it and the shape
    # is offered — never acted on.
    assert win.minimumWidth() <= avail.width()
    assert shell._banner is not None and _visible(shell._banner)
    assert not phone_layout.is_on()
    # The way in is one of the small icon buttons at the top right, just
    # before help, styled as they are, in every mode.
    band_lay = review._top_bar.layout()
    assert band_lay.indexOf(shell.desk_button) == band_lay.indexOf(win._help_btn) - 1
    assert shell.desk_button.objectName() == win._help_btn.objectName() == "IconButton"
    assert not shell.desk_button.text() and _visible(shell.desk_button)

    shell.desk_button.click()
    _pump(qapp)
    u = 800 / 390
    assert phone_layout.is_on()
    assert theme.ui_scale() == pytest.approx(15 * u / 13)    # body at 15 pt
    assert theme.touch_unit() == pytest.approx(u)
    assert not _visible(win._header)
    assert not _visible(shell._banner)
    for w in (win._present_btn, win._reset_saves_btn, win._copy_analysis_btn,
              win._open_btn, win._reset_view_btn, win._refresh_btn,
              win._help_btn, win._statusbar, shell.desk_button):
        assert not _visible(w), w
    assert _visible(win._mode_seg) and _visible(shell.options_button)
    # Thumb-sized: 48 pt square ☰ and ⋯, a 52 pt bar, 44 pt everything else.
    assert shell.menu_button.height() == round(48 * u)
    assert review._top_bar.height() == round(52 * u)
    assert shell.page_button.height() >= round(44 * u)
    assert shell.controls_button.height() >= round(44 * u)
    assert all(not _visible(s._icon_lbl) for s in win._mode_seg._segments)
    assert not _visible(review._page_tabs_host)
    assert (win.minimumWidth(), win.minimumHeight()) <= (320, 400)
    drawer = review._ctx.widgets.get("_phone_drawer")
    # Content first: the plots, and the rail tucked away in the drawer.
    assert not drawer.isVisible()
    assert _visible(review._central_pane_stack)
    assert not shell.menu_button.isChecked()

    # ☰ slides the whole rail — sections and well plate — out over the page,
    # which stays where it is underneath.
    shell.menu_button.click()
    _pump(qapp)
    assert drawer.isOpen() and drawer.isVisible()
    assert shell.menu_button.text() == "✕"
    assert drawer.width() == win._review.width() - round(56 * u)
    assert review._sidebar_root.parentWidget() is drawer.contentWidget()
    assert _visible(review._section_nav) and _visible(review._sidebar_plate_slot)
    assert _visible(review._central_pane_stack)
    assert shell.menu_button.isChecked()
    # Right under the top bar, so ☰ and the mode bar stay in reach, and over
    # the strip, whose page menu and Controls belong to the page it hides.
    assert drawer.geometry().top() == review._top_bar.geometry().bottom() + 1
    assert drawer.geometry().top() < review._h_pane.geometry().top()

    # Code that used to put the desktop chrome back on its own: a resize
    # re-parks (and re-shows) the rail toggle, a mode switch re-shows Split.
    win.resize(win.width() - 10, win.height() - 10)
    _pump(qapp)
    assert not _visible(review._sidebar_toggle_btn)

    # The dimmed strip closes it, and the collapse flag and ☰ follow.
    drawer._backdrop.clicked.emit()
    _pump(qapp)
    assert not drawer.isOpen()
    assert review._sidebar_collapsed
    assert not shell.menu_button.isChecked()

    # A section tapped in the rail closes it too.
    shell.menu_button.click()
    _pump(qapp)
    key = next(k for k in review._section_nav.items() if k != review._section_nav.currentKey())
    review._section_nav._rows[key].clicked.emit(key)
    _pump(qapp)
    assert not drawer.isOpen()
    assert review._section_nav.currentKey() == key

    # ⚙ Controls: the page's band slides up in a sheet, flowing, and goes
    # back to its page when the sheet closes.
    shell.controls_button.click()
    _pump(qapp)
    band = shell._sheet_band
    assert band is not None and _visible(band) and band._stacked
    assert shell._sheet.isOpen()
    shell.controls_button.click()
    _pump(qapp)
    assert shell._sheet_band is None and not band._stacked
    assert band.parentWidget() is not shell._sheet._aw_holder
    assert not _visible(band)                      # hidden in its page again

    # Analyze: the run dashboard first, stacked; ☰ slides the form out.
    win._mode_seg.setCurrentIndex(1)
    _pump(qapp)
    analyze = win._analyze
    assert analyze._dashboard.isStacked()
    assert not drawer.isOpen()
    split_btn = getattr(review._ctx.runtime.split, "toggle_btn", None)
    shell.menu_button.click()
    _pump(qapp)
    assert drawer.isOpen()
    assert analyze._left_sidebar.parentWidget() is drawer.contentWidget()
    assert _visible(analyze._left_sidebar) and not _visible(review._sidebar_root)
    # Picking a mode with the rail out closes it.
    win._on_titlebar_mode_seg(0)
    _pump(qapp)
    assert not drawer.isOpen()
    assert split_btn is None or not _visible(split_btn)
    assert not _visible(review._sidebar_toggle_btn)

    # The ⋯ menu: text size as one row, reload, help, the log, and the way back.
    shell._fill_menu()
    texts = [a.text() for a in shell._menu.actions()]
    assert {"Reload dataset", "Help", "Desktop layout"} <= set(texts)
    assert any(t.startswith("App log") for t in texts)
    next(a for a in shell._menu.actions() if a.text() == "Desktop layout").trigger()
    _pump(qapp)

    assert not phone_layout.is_on()
    assert theme.ui_scale() == 1.0
    assert _visible(win._header)
    for w in (win._present_btn, win._reset_view_btn, review._section_nav,
              review._page_tabs_host, review._sidebar_root,
              review._central_pane_stack, review._sidebar_toggle_btn):
        assert _visible(w), w
    for w in (shell.menu_button, shell.page_button, shell.options_button,
              shell.controls_button, shell._strip):
        assert not _visible(w), w
    assert theme.touch_unit() is None
    for w in (win._refresh_btn, win._help_btn, win._statusbar, shell.desk_button):
        assert _visible(w), w
    # Outside Review's own cluster, so Analyze — watching a run, the commonest
    # reason to be on a phone — has it too.
    win._on_titlebar_mode_seg(1)
    _pump(qapp)
    assert _visible(shell.desk_button) and not _visible(win._present_btn)
    win._on_titlebar_mode_seg(0)
    _pump(qapp)
    # Every left panel is back in its own splitter, rail first.
    assert review._h_pane.indexOf(review._sidebar_root) == 0
    assert analyze._left_sidebar.parentWidget() is not drawer.contentWidget()
    assert not drawer.isVisible()
    assert (review._sidebar_root.minimumWidth(),
            review._sidebar_root.maximumWidth()) == (theme.Metrics.rail_w,) * 2
    assert all(_visible(s._icon_lbl) for s in win._mode_seg._segments)
    assert not analyze._dashboard.isStacked()
    assert split_btn is None or _visible(split_btn)
    # The desk minimum, as far as this screen allows it.
    assert (win.minimumWidth(), win.minimumHeight()) == (
        min(phone_layout.DESK_MIN_W, avail.width()),
        min(phone_layout.DESK_MIN_H, avail.height()))


def test_touch_details_in_the_real_window(qapp, win, monkeypatch):
    """The UX review's second round, each checked on the widget a thumb meets."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QPushButton, QTextEdit, QToolButton
    from core import phone_layout

    shell = win._phone
    review = win._review
    u = 800 / 390
    desk_hidden = {k for k, r in review._section_nav._rows.items() if r.isHidden()}
    shell.set_on(True)
    _pump(qapp)
    try:
        # Mode switch: every label shows whole, at one shared width.
        segs = win._mode_seg._segments
        assert len({s.minimumWidth() for s in segs}) == 1
        assert all(s.minimumWidth() >= s._text_lbl.sizeHint().width() for s in segs)
        # The strip: 52 pt, its two buttons 44 pt.
        assert shell._strip.height() == round(52 * u)
        assert shell.page_button.height() == round(44 * u)
        # Review CSV and Batch Export are desk work; the rest is a two-wide grid.
        nav = review._section_nav
        for key in shell._h.phone_hidden_sections:
            assert nav._rows[key].isHidden(), key
        shown = [r for k, r in nav._rows.items() if not r.isHidden()]
        assert shown and all(r.minimumHeight() >= round(48 * u) - 1 for r in shown)
        # The analysis log wraps rather than scrolling sideways.
        logs = shell._h.logs()
        assert logs and all(l.lineWrapMode() == QTextEdit.WidgetWidth for l in logs)
        # The status bar is hidden, so its log "!" is mirrored onto ⋯ — inset,
        # clear of the button's edge.
        log_badge = shell._h.log_badge
        log_badge.notify("warn")
        _pump(qapp)
        assert shell._options_badge.tone() == "warn"
        assert not shell._options_badge.isHidden()
        btn = shell.options_button
        assert shell._options_badge.geometry().right() <= btn.width() - round(2 * u)
        assert shell._options_badge.geometry().top() == round(2 * u)
        log_badge.clear()
        _pump(qapp)
        assert shell._options_badge.isHidden()
        # The rail carries the status bar's counts, live while it is open.
        win._status_lbl_app.setText("4 well(s) | 72 nuclei")
        shell.menu_button.click()
        _pump(qapp)
        assert shell._rail_status.text() == "4 well(s) | 72 nuclei"
        assert _visible(shell._rail_status)
        shell.menu_button.click()
        _pump(qapp)
        # The Controls sheet: 48 pt controls, and a "Done ✕" to close it —
        # on Plotting, whose band has controls with no dataset loaded (Home's
        # holds nothing tappable until one is).
        nav._rows["Plotting"].clicked.emit("Plotting")
        _pump(qapp)
        shell.controls_button.click()
        _pump(qapp)
        assert shell.controls_button.text() == "Done ✕"
        # At most 60 % of the window (Plotting's controls fill that on this
        # small screen), and no scrim: the plot above it is what the controls
        # are judged by.
        sheet = shell._sheet
        assert 0 < sheet.height() <= int(win.height() * 0.6)
        assert sheet._backdrop._alpha == 0
        # A drag scrolls the sheet and the log, as a thumb expects.
        from PySide6.QtWidgets import QScroller
        assert QScroller.hasScroller(logs[0].viewport())
        band = shell._sheet_band
        ctrls = [w for t in (QPushButton, QToolButton, QComboBox)
                 for w in band.findChildren(t) if _visible(w)]
        assert ctrls and all(w.height() >= round(48 * u) - 1 for w in ctrls), [
            (type(w).__name__, w.height()) for w in ctrls if w.height() < round(48 * u) - 1]
        shell.controls_button.click()
        _pump(qapp)
        assert shell.controls_button.text() != "Done ✕"
        # The run view drops what repeats the headline.
        dash = win._analyze._dashboard
        assert dash.isStacked() and dash._status.isHidden()
        # Sideways (this offscreen screen is square, so the shape is forced):
        # the page menu and Controls join the bar, and Analyze becomes two
        # columns with the plate capped to the screen's height.
        monkeypatch.setattr(shell, "is_landscape", lambda: True)
        shell._apply_sizes()
        _pump(qapp)
        from PySide6.QtWidgets import QBoxLayout
        assert shell.page_button.parentWidget() is review._top_bar
        bar_lay = review._top_bar.layout()
        for b in (shell.page_button, shell.controls_button):
            assert bar_lay.itemAt(bar_lay.indexOf(b)).alignment() == Qt.AlignVCenter
        assert shell._h.analyze_body.direction() == QBoxLayout.LeftToRight
        assert dash.maximumWidth() == round(300 * u)
        assert dash._plate.maximumHeight() == round(160 * u)
        monkeypatch.setattr(shell, "is_landscape", lambda: False)
        shell._apply_sizes()
        _pump(qapp)
        assert shell.page_button.parentWidget() is shell._strip
        assert shell._h.analyze_body.direction() == QBoxLayout.TopToBottom
    finally:
        shell.set_on(False)
        _pump(qapp)
    assert not phone_layout.is_on()
    assert all(l.lineWrapMode() == QTextEdit.NoWrap for l in shell._h.logs())
    assert {k for k, r in review._section_nav._rows.items()
            if r.isHidden()} == desk_hidden
    assert shell._options_badge.isHidden()


def test_closing_in_phone_layout_mid_run_asks_first(qapp, win, monkeypatch):
    import allwell
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(allwell, "_analyze_run_busy", lambda _a: True)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.Cancel)
    win._phone.set_on(True, restyle=False)
    try:
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert asked and not ev.isAccepted()
    finally:
        win._phone.set_on(False, restyle=False)


def _image_plate(root):
    """Two wells × DAPI/GFP, one field, real TIFFs in the pipeline's in/out
    layout — enough for the Image Table to draw a grid."""
    import csv
    import io
    import json
    import zipfile

    import numpy as np
    tifffile = pytest.importorskip("tifffile")

    def tif(a):
        b = io.BytesIO()
        tifffile.imwrite(b, a)
        return b.getvalue()

    (root / "in").mkdir(parents=True)
    (root / "out").mkdir()
    info = {"schema": "experiment:channel:well:fov:timepoint", "separator": "_",
            "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
            "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
            "nuclear_token": "dapi", "fluor_tokens": ["dapi", "gfp"],
            "available_timepoints": ["0h"], "available_fovs": ["F001"]}
    (root / "out" / "pipeline_info.json").write_text(json.dumps(info))
    lab = np.zeros((64, 64), np.int32)
    lab[10:24, 10:24] = 1
    lab[36:54, 30:50] = 2
    for well in ("A01", "A02"):
        rows = []
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as zin, \
                zipfile.ZipFile(root / "out" / f"{well}_out.zip", "w") as zout:
            for ch, peak in (("DAPI", 3000), ("GFP", 900)):
                img = np.where(lab > 0, peak, 100).astype(np.uint16)
                stem = f"exp_{ch}_{well}_F001_0h"
                zin.writestr(stem + ".tif", tif(img))
                zout.writestr(stem + "_tophat.tif", tif(img))
            zout.writestr(f"exp_DAPI_{well}_F001_0h_labels.tif", tif(lab))
        for i in (1, 2):
            rows.append({"filename": f"exp_DAPI_{well}_F001_0h.tif", "experiment": "exp",
                         "channel": "dapi", "well": well, "fov": "F001",
                         "timepoint": "0h", "timepoint_hours": 0.0, "nucleus_id": i,
                         "area_px": int((lab == i).sum()), "gfp_mean_intensity": 900.0,
                         "dapi_mean_intensity": 3000.0})
        with (root / "out" / f"measurements_{well}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return root


def _image_cell_widths(grid) -> list[int]:
    from PySide6.QtWidgets import QLabel
    host = grid.parentWidget()
    return sorted({lbl.pixmap().width() for lbl in host.findChildren(QLabel)
                   if lbl.pixmap() is not None and not lbl.pixmap().isNull()})


def test_the_image_table_opens_on_its_grid_in_phone_layout(qapp, win, tmp_path):
    """The Image Table's builder and action strip go behind ⚙ Controls, and its
    grid is fitted to the screen and drawn again when the layout changes.

    Reached the way a user reaches it: phone layout on first, then Image Browser
    picked from the side rail. The page builds lazily on that first visit, after
    the page change the strip refreshes on — which left ⚙ Controls hidden, and
    with it every control for building a table (a reported bug)."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea

    from core import phone_layout
    from review.features.image_browser.image_table import controller as C

    review, shell = win._review, win._phone
    if win._mode_seg.currentIndex() != 0:
        win._on_titlebar_mode_seg(0)
    review._load_path(_image_plate(tmp_path / "plate"))
    _pump(qapp)
    review._set_selected_wells({"A01", "A02"}, commit=True)
    ctx = review._ctx
    assert ctx.runtime.image_table.render_grid is None    # not built yet

    shell.set_on(True)
    _pump(qapp)
    try:
        shell.menu_button.click()
        _pump(qapp)
        review._section_nav._rows["Image Browser"].clicked.emit("Image Browser")
        QTest.qWait(50)
        _pump(qapp)
        # The page opens on the images; the builder is behind ⚙ Controls, and
        # the button to reach it is there on this first, lazily built visit.
        controls = review.findChild(QWidget, "ImageTableControls")
        assert controls is not None and not _visible(controls)
        assert _visible(shell.controls_button)
        empty = ctx.runtime.image_table.empty_state
        assert "⚙ Controls" in empty._hint.text()       # not "above"
        C.image_table_repopulate_dropdowns(ctx)
        review._app_state.image_table.active_channels = {"DAPI", "GFP"}
        C.image_table_apply_dims_preset(ctx, ["well"], ["channel"])
        C.image_table_generate(ctx)
        _pump(qapp)
        grid = ctx.runtime.image_table.render_grid
        # Two columns share the width: wider than the desk's cells, and the grid
        # inside the page's width — no sideways scroll.
        scroll = grid.parentWidget()
        while not isinstance(scroll, QScrollArea):
            scroll = scroll.parentWidget()
        phone = _image_cell_widths(grid)
        assert phone and min(phone) > 240                 # the desk's rule, exceeded
        assert grid.parentWidget().sizeHint().width() <= scroll.viewport().width()
        assert scroll.horizontalScrollBar().maximum() == 0
        # ⚙ Controls lends the builder and the action strip to the sheet,
        # action strip first, in one column.
        shell.controls_button.click()
        _pump(qapp)
        assert shell._sheet_band is controls and controls.isStacked()
        first = controls.layout().itemAt(0).widget()
        assert any(b.text() == "Generate" for b in first.findChildren(QPushButton))
        assert ctx.runtime.image_table.axes_row_layout.direction() == QBoxLayout.TopToBottom
        # No control squeezed below its own minimum: the touch floor made the
        # channel rows' ⚙ buttons 48 pt, and an evenly shared, floored zone
        # once laid those rows out at a third of that, with blank buttons.
        from PySide6.QtWidgets import QFrame
        pills = [p for p in controls.findChildren(QFrame, "ITBChan") if _visible(p)]
        assert pills and all(p.height() >= p.minimumSizeHint().height() for p in pills), [
            (p.height(), p.minimumSizeHint().height()) for p in pills]
        shell.controls_button.click()
        _pump(qapp)
        assert shell._sheet_band is None and not controls.isStacked()
        assert not _visible(controls)
    finally:
        shell.set_on(False)
        QTest.qWait(600)
        _pump(qapp)
    assert not phone_layout.is_on()
    assert _visible(controls)
    assert controls.layout().itemAt(0).widget().objectName() == "ITBShelf"
    desk = _image_cell_widths(grid)                       # redrawn at the desk's size
    assert desk and max(desk) <= 240 < min(phone)
    assert "above" in empty._hint.text()


def test_properties_from_the_controls_sheet_opens_a_full_width_sheet(qapp, win, mini_dataset):
    """Bar Plots → ⚙ Controls → Properties: the panel opens over the page at the
    window's width, with the Controls sheet closed — not in the plot's side dock
    under the sheet, 360 px wide on a 390 px screen — and its ✕ puts it back."""
    from PySide6.QtTest import QTest

    from widgets.icon_button import IconButton

    review, shell = win._review, win._phone
    if win._mode_seg.currentIndex() != 0:
        win._on_titlebar_mode_seg(0)
    review._load_path(mini_dataset)
    _pump(qapp)
    review._notebook.setCurrentByName("Plotting")
    review._plotting_notebook.setCurrentByName("Bar Plots")
    review._drain_pending_centre_builders()
    _pump(qapp)
    shell.set_on(True)
    QTest.qWait(600)
    _pump(qapp)
    try:
        win.resize(390, 780)
        _pump(qapp)
        shell.controls_button.click()
        _pump(qapp)
        props = [b for b in shell._sheet_band.findChildren(IconButton)
                 if "properties" in (b.toolTip() or "").lower() and b.isVisible()]
        assert props, "no Properties button in the Controls sheet"
        props[0].click()
        _pump(qapp)
        dock = review._bar_export_dock
        assert not shell._sheet.isOpen() and shell._sheet_band is None, \
            "the panel opened under the Controls sheet"
        sheet = getattr(win, "_aw_phone_panel_sheet", None)
        assert sheet is not None and sheet.isOpen(), \
            "Properties opened in the plot's side dock, not as a phone sheet"
        assert dock.parentWidget() is sheet.contentWidget()
        QTest.qWait(400)
        _pump(qapp)
        assert sheet.width() == win.width() and dock.width() == win.width()

        sidebar = next(iter(review._export_style_sidebars.values()))
        sidebar._close_dock()
        _pump(qapp)
        assert not sheet.isOpen() and dock.parentWidget() is not sheet.contentWidget()
    finally:
        shell.set_on(False)
        QTest.qWait(600)
        _pump(qapp)


def test_phone_layout_never_filters_every_event_in_the_application(qapp, win, monkeypatch):
    """Reported: a Windows access violation on ``_PhoneEventFilter.eventFilter``'s
    first line, called straight from ``app.exec()``. The filter was installed on
    the application, so every event of every object went through Python. It is
    now installed only on the widgets phone layout adapts, found by a sweep —
    including ones built after the switch, which must still be found."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog

    from ui.phone_shell import _P_WATCHED

    shell = win._phone
    on_app = []
    real = QApplication.installEventFilter

    def _spy(self, obj):
        if self is QApplication.instance():
            on_app.append(obj)
        return real(self, obj)

    monkeypatch.setattr(QApplication, "installEventFilter", _spy)
    shell.set_on(True)
    QTest.qWait(600)
    _pump(qapp)
    try:
        assert shell._filter not in on_app, "phone layout filtered the whole application"
        late = QDialog(win)
        late.show()
        QTest.qWait(500)                                  # past one sweep
        _pump(qapp)
        assert late.property(_P_WATCHED), "a dialog opened in phone layout was never adapted"
        late.close()
        late.deleteLater()
    finally:
        shell.set_on(False)
        QTest.qWait(600)
        _pump(qapp)
    assert not any(w.property(_P_WATCHED) for w in QApplication.allWidgets())
