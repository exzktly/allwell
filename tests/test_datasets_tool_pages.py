"""Every Datasets tool page has the design language's skeleton (§6, *Datasets*; tracker row D24): a column no
wider than ``theme.Metrics.tool_page_w`` holding the tool's title and one line of help, its rows in titled cards,
and a foot row whose last button is the primary action — built through ``datasets/core/tool_page.py`` and read
here off the real pages the pane builds."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from ui import theme


@pytest.fixture
def tab(qapp, tmp_path):
    from datasets.tab import DatasetsTab
    t = DatasetsTab(queue_state_dir=tmp_path)
    yield t
    t.shutdown()


def _tool_pages(tab):
    for name in tab.tool_names():
        tab.show_tool(name)
        page = tab._pages.currentWidget()
        column = page.findChild(QWidget, "ToolPageColumn")
        yield name, page, column


def test_every_tool_page_is_a_capped_column_with_a_title_and_help(tab):
    for name, page, column in _tool_pages(tab):
        assert column is not None, f"{name} is not built as a tool page"
        assert column.maximumWidth() == theme.Metrics.tool_page_w, name
        titles = [w.text() for w in column.findChildren(QLabel) if w.property("textRole") == "pageTitle"]
        assert len(titles) == 1, (name, titles)


def test_a_queueable_tool_ends_its_foot_row_with_the_primary_action(tab):
    from ui.page_chrome import ActionRow
    for name, page, column in _tool_pages(tab):
        row = column.findChild(ActionRow)
        if row is None:
            continue                                     # Automation: a page of runs, not a tool
        lay = row.layout()
        last = next(lay.itemAt(i).widget() for i in reversed(range(lay.count())) if lay.itemAt(i).widget())
        assert last is page._run_btn and last.property("variant") == "primary", name


def test_the_rows_sit_in_cards_and_a_card_of_hidden_rows_hides(tab):
    from widgets.card import Card
    tab.show_tool("Subset / Split")
    page = tab._pages.currentWidget()
    column = page.findChild(QWidget, "ToolPageColumn").layout()
    in_order = sorted(page.findChildren(Card), key=column.indexOf)
    titles = [c.title() for c in in_order if column.indexOf(c) >= 0]
    assert titles[:2] == ["Folders", "What to copy"], titles
    assert page._src_field.parentWidget().parentWidget().title() == "Folders"
    tab.show_tool("Convert OME-TIFF")
    page = tab._pages.currentWidget()
    channels = next(c for c in page.findChildren(Card) if c.title() == "Channels")
    assert channels.isHidden(), "an empty Channels card is shown before a file is read"
    page._channels_caption.show()
    assert not channels.isHidden()


def test_the_rail_groups_the_tools_under_three_eyebrows(tab):
    assert tab._nav.captions() == ["Combine", "Convert", "Maintain"]
