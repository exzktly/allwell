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

from datasets._common.log_panel import LogPanelMixin  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pages():
    from datasets.concatenate.view import ConcatenatePage
    from datasets.import_loose.view import ImportLoosePage
    from datasets.merge_plates.view import MergePlatesPage
    from datasets.ome_import.view import OmeImportPage
    from datasets.subset.view import SubsetPage
    from datasets.validate.view import ValidatePage

    return {
        "concatenate": ConcatenatePage,
        "ome_import": OmeImportPage,
        "validate": ValidatePage,
        "subset": SubsetPage,
        "import_loose": ImportLoosePage,
        "merge_plates": MergePlatesPage,
    }


ALL_TOOLS = ["concatenate", "ome_import", "validate", "subset",
             "import_loose", "merge_plates"]


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


# ── the primary action names a distinct verb ─────────────────────────────────

def test_no_two_tools_share_a_primary_button_label(qapp):
    """Convert OME-TIFF and Zip Loose Folder both read "▶  Convert".

    Two different pages, same verb, and nothing else on the run row to tell
    them apart — so a screenshot, a support question or a half-remembered
    workflow could not distinguish them.
    """
    labels = {}
    for name, cls in _pages().items():
        page = cls()
        btn = getattr(page, "_run_btn", None)
        assert btn is not None, f"{name} has no _run_btn — the other five do"
        labels.setdefault(btn.text(), []).append(name)

    clashes = {text: tools for text, tools in labels.items() if len(tools) > 1}
    assert not clashes, f"these tools share a primary button label: {clashes}"


def test_every_page_explains_its_raw_log_toggle(qapp):
    """Concatenate was the only one of six that said what "raw log" meant."""
    for name, cls in _pages().items():
        assert cls()._raw_log_cb.toolTip(), f"{name} has no tooltip on its toggle"
