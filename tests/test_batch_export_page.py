"""Batch Export's page as the design language draws it (tracker row D26), read off the real panels.

That the page carries no band strip is the every-page render test's ``no_empty_band`` check.

Every mode is built through the app's own constructor (``set_mode``), so a mode whose ``_build_output_panel`` raised,
or that kept the old band-and-columns layout, fails here. Auto-marked ``slow`` — it builds a full ``ReviewApp`` —
so run it as ``scripts/test.sh tests/test_batch_export_page.py -m ""``.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

_MODES = ("line", "bar", "scatter_cells", "scatter_agg", "response", "nanowell")


def _panel(app, mode):
    app._notebook.setCurrentByName("Batch Export")
    app._ctx.runtime.batch_export.set_mode(mode)
    return app._ctx.runtime.batch_export.inline_state["panels"][mode]


def _layout_widgets(layout) -> list[QWidget]:
    return [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget() is not None]


def _form_labels(form) -> list[str]:
    from widgets.form_grid import FormGrid
    assert isinstance(form, FormGrid)
    return [entry[1].text() for entry in form._entries if entry[0] == "row"]


def test_every_mode_is_a_title_two_cards_and_no_band(loaded_app):
    from ui.page_chrome import PageHeader
    from widgets.card import Card
    from widgets.form_grid import FormGrid

    titles = {"line": "Line", "bar": "Bar", "scatter_cells": "Scatter (cells)",
              "scatter_agg": "Scatter (aggregate)", "response": "Response", "nanowell": "Nanowells"}
    for mode in _MODES:
        panel = _panel(loaded_app, mode)
        header = panel.findChild(PageHeader)
        assert header is not None and header.title() == f"Batch Export · {titles[mode]}", mode
        commands = [w for w in _layout_widgets(header.layout()) if w.isVisibleTo(header)]
        assert [(b.text(), b.property("variant")) for b in commands] == [
            ("Sync from app", "quiet"), ("Export", "primary")], mode
        assert commands[-1] is panel._run_btn

        groups, output = panel._split_left, panel._split_right
        assert isinstance(groups, Card) and groups.title() == "Groups", mode
        heading = [w for w in _layout_widgets(groups.heading()) if w is not groups._title]
        assert [(b.text(), b.property("variant")) for b in heading] == [
            ("Clear all", "quiet"), ("Quick setup ▾", "quiet"), ("Add group", "default")], mode
        assert isinstance(output, Card) and output.title() == "Output", mode
        forms = output.findChildren(FormGrid)
        assert len(forms) == 2, mode
        out_labels, plot_labels = _form_labels(forms[0]), _form_labels(forms[1])
        expected_output = ["Write to", "Folder", "Format", "Style preset"]
        if mode != "nanowell":
            expected_output.append("Also write")
        assert out_labels == expected_output, (mode, out_labels)
        headings = [w.text() for w in output.findChildren(QLabel) if w.property("textRole") == "sectionHeading"]
        assert headings == ["Output", "Plot"], mode
        if mode in ("line", "bar", "response"):
            assert plot_labels[:2] == ["Channel", "Property"], (mode, plot_labels)
        if mode in ("line", "bar"):
            assert {"Error band", "Points"} <= set(plot_labels), (mode, plot_labels)


def test_the_groups_card_says_there_are_none_until_one_is_added(loaded_app):
    """The empty list is an information notice; adding a group replaces it with the group's row."""
    from widgets.notice import Notice

    panel = _panel(loaded_app, "line")
    panel._groups = []
    panel._active_grp = -1
    panel._refresh_group_list()
    # What the list holds now (a refresh hands the old rows to deleteLater, so children alone would count them).
    listed = _layout_widgets(panel._grp_inner_layout)
    assert [type(n).__name__ for n in listed] == ["Notice"]
    assert (listed[0].lead(), listed[0].severity()) == ("No export groups yet.", "information")

    panel._grp_add()
    rows = _layout_widgets(panel._grp_inner_layout)
    assert len(rows) == 1 and not isinstance(rows[0], Notice)
    texts = [b.text() for b in rows[0].findChildren(QWidget) if hasattr(b, "text") and b.property("variant")]
    assert texts[:2] == ["Rename…", "Clear"]
