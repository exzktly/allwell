"""Every Datasets tool page gets its status/log furniture from the shared mixin.

`append_log` and `clear_log` were byte-identical across all six pages and
`_on_toggle_raw_log` identical in four, which is how the six drifted apart in
the first place — each fix had to be applied six times, and twice was not. The
parametrised sweep below is the point of this file: a seventh tool, or a
re-hand-rolled seventh copy on an existing one, fails it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.core.log_panel import LogPanelMixin  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _tool_kinds() -> list[str]:
    """Every queueable tool, from the registry the app itself builds from.

    Driven off ``_FEATURES`` rather than a list written out here: a hardcoded
    roster makes these sweeps say "all six" while meaning "the six I knew
    about", which is exactly the drift they exist to prevent. A seventh tool
    added to the registry is swept from the moment it exists.
    """
    from datasets.tab import _FEATURES

    return [f.kind for f in _FEATURES if f.kind and f.kind != "job_queue"]


def _pages():
    """kind -> page class, resolved through each feature's own controller."""
    import importlib

    out = {}
    for kind in _tool_kinds():
        module = importlib.import_module(f"datasets.{kind}.view")
        page_cls = next(
            obj for name, obj in vars(module).items()
            if name.endswith("Page") and isinstance(obj, type)
            and obj.__module__ == module.__name__
        )
        out[kind] = page_cls
    return out


ALL_TOOLS = _tool_kinds()


@pytest.fixture(params=ALL_TOOLS)
def page(request, qapp):
    return _pages()[request.param]()


def test_every_page_uses_the_shared_mixin(page):
    assert isinstance(page, LogPanelMixin)


@pytest.mark.parametrize("member", ["append_log", "clear_log", "_on_toggle_raw_log"])
def test_no_page_re_hand_rolls_a_shared_member(member):
    """Inherited from the mixin, not redefined on the page.

    A page that redefines one of these is a page that can drift from the other
    five — which is exactly the state this replaced.
    """
    offenders = [name for name, cls in _pages().items() if member in vars(cls)]
    assert not offenders, f"{member} is re-defined on: {offenders}"


def test_the_raw_log_starts_hidden(page):
    """The dashboard is the summary view; the verbose log is opt-in."""
    assert page._status.isHidden()
    assert page._log_sep.isHidden()


def test_the_checkbox_reveals_and_re_hides_the_log(page):
    page._raw_log_cb.setChecked(True)
    assert not page._status.isHidden()
    assert not page._log_sep.isHidden()

    page._raw_log_cb.setChecked(False)
    assert page._status.isHidden()
    assert page._log_sep.isHidden()


def test_a_page_with_its_own_log_header_toggles_that_too(qapp):
    """Validate is the only page with a `_log_hdr`, and it must travel with
    the log — the single shared implementation covers it through `getattr`,
    which is why no page needs an override."""
    validate = _pages()["validate"]()
    assert hasattr(validate, "_log_hdr")
    assert validate._log_hdr.isHidden()
    validate._raw_log_cb.setChecked(True)
    assert not validate._log_hdr.isHidden()
    validate._raw_log_cb.setChecked(False)
    assert validate._log_hdr.isHidden()


def test_append_and_clear(page):
    page.append_log("first line")
    page.append_log("second line")
    assert "first line" in page._status.toPlainText()
    assert "second line" in page._status.toPlainText()

    page.clear_log()
    assert page._status.toPlainText() == ""


def test_the_log_document_is_capped(page):
    """A long run must not grow the document unbounded — the cap every one of
    these logs already carried, matching Analyze's raw log."""
    assert page._status.document().maximumBlockCount() == 10000


def test_the_log_is_read_only(page):
    assert page._status.isReadOnly()


def test_concatenate_keeps_its_toggle_tooltip(qapp):
    """Concatenate is the one page whose checkbox explains itself. Folding six
    copies into one must not have quietly dropped the only tooltip among them.
    """
    page = _pages()["concatenate"]()
    assert "merge log" in page._raw_log_cb.toolTip()


def test_every_page_explains_its_raw_log_toggle(qapp):
    """Concatenate was the only one of six that said what "raw log" meant."""
    for name, cls in _pages().items():
        assert cls()._raw_log_cb.toolTip(), f"{name} has no tooltip on its toggle"


def test_the_dashboard_does_not_stretch_to_fill_a_tall_viewport(page, qapp):
    """Every page needs a trailing stretch, and the shared refactor lost one.

    `QScrollArea.setWidgetResizable(True)` grows the content widget to the
    viewport. With the raw log hidden by default, the dashboard is the only
    widget in the outer layout willing to absorb the leftover height, and its
    own internal `center.addStretch(1)` turns that into a growing dead zone
    below the height-capped plate rather than the plate itself growing.

    Subset had this guard and Validate did not, which is why folding the six
    status sections together could delete Validate's trailing stretch — and
    its eight-line comment explaining it — with nothing going red. It measured
    1755px against a 305px sizeHint at 1300x2000. Swept over every tool now.
    """
    dashboard = getattr(page, "dashboard", None)
    if dashboard is None:
        pytest.skip("this tool reports with a progress bar, not a dashboard")
    dashboard.setVisible(True)
    page.resize(1300, 2000)          # far taller than the natural content
    page.show()
    qapp.processEvents()
    assert dashboard.height() <= dashboard.sizeHint().height() + 20, (
        f"dashboard is {dashboard.height()}px against a "
        f"{dashboard.sizeHint().height()}px sizeHint — the page is missing its "
        "trailing outer.addStretch(1)")
