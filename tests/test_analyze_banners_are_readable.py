"""The Analyze pane's two folder banners are objects, and their text has room.

Both strips at the head of the Analyze sidebar — the resume offer
(``analyze_suspend``) and the pre-run report (``analyze_preflight``) — were
built as a ``QHBoxLayout`` of one wrapping label followed by two buttons, and
given an object name that ``theme.qss()`` had no rule for. Two consequences,
neither of which any test could see:

**Nothing painted.** A bare ``QWidget`` with an object name and no matching
stylesheet rule has no fill and no edge, and a ``QWidget`` subclass ignores its
own background rule entirely unless ``WA_StyledBackground`` is set. So the
strips had no boundary against the form beneath them: an offer to resume
yesterday's run read as the first paragraph of the Analyze form.

**The text had no room.** The Analyze sidebar is ``app_shell_view.RAIL_W`` and
fixed at it, with 319 px of scrolling viewport inside that. Measured in a real
``AnalyzeTab`` at the app's default window size, ``Resume`` and ``Discard``
beside the label left the resume offer **139 px** and the pre-run report
**74 px** — the report's three sentences reflowed into 224 px of text two words
wide, running down the side of two buttons floating in the middle of them.
Stacked, both get 295 px.

The width assertions below are the ones that bite. They are geometry, not
inspection: they fail for a row layout however it is spelled, and they would
fail again for a third button squeezed in beside the text.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

import theme
from services import run_journal as J
from well_viewer.core.app_shell_view import RAIL_W

#: The share of the strip the offer's own words must get. Deliberately well
#: below what the stacked layout achieves (295/319 ≈ 0.92 in the real sidebar)
#: and well above what the row layout gave it (139/319 ≈ 0.44), so the number is
#: a statement about which of the two shapes is in force rather than a pin on
#: the current padding. The fixture's host is a plain widget rather than the
#: tab's scroll area, so the absolute widths it reads differ a little from the
#: sidebar's; the *share* is what this pins, and it moves the same way.
_MIN_TEXT_SHARE = 0.8


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    """The journal index goes to a temp file, never the developer's own."""
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")


@pytest.fixture
def strips(qtbot):
    """Both banners, laid out at the width the Analyze sidebar really has.

    Built through ``_build_banner_strips`` — the app's own entry point, holder
    included — rather than by calling each builder, because the holder is what
    puts the gap between the two cards and what must collapse to nothing when
    both are hidden.
    """
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    tab._build_banner_strips(host)
    lay.addStretch(1)
    host.setFixedWidth(RAIL_W)
    host.resize(RAIL_W, 600)
    host.show()
    qtbot.waitExposed(host)
    return tab, host


def _journal_offer(tab, tmp_path, *, wells=(("A01", "analysed"),)):
    """Put a real unfinished run on disk and show the strip that offers it."""
    out = tmp_path / "Plate_07_HCS" / "out"
    out.mkdir(parents=True)
    J.open_run(out, mode="local", input_dir=out.parent / "in", argv=[],
               spec={"opts": {}})
    for well, state in wells:
        J.record_well(out, well, state)
    J.close_run(out, outcome="error")
    tab.refresh_resume_banner(current_output_dir=str(out))
    return out


# ── the strips are objects on the page ──────────────────────────────────────

@pytest.mark.parametrize("name", ["ResumeBanner", "PreflightBanner"])
def test_the_banner_has_a_rule_in_the_stylesheet(name):
    """An object name with no rule behind it paints nothing at all."""
    qss = theme.qss()
    assert f"QWidget#{name}" in qss, (
        f"#{name} is named at its call site and styled nowhere, so it has no "
        f"fill and no edge and reads as loose page chrome")


@pytest.mark.parametrize("name", ["ResumeBanner", "PreflightBanner"])
def test_the_banner_paints_its_own_background(qtbot, name):
    """``WA_StyledBackground``, without which the fill above is ignored."""
    from PySide6.QtCore import Qt

    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    banner = tab.findChild(QWidget, name)
    assert banner is not None, f"#{name} was not built"
    assert banner.testAttribute(Qt.WA_StyledBackground), (
        f"#{name} is a plain QWidget subclass, which ignores its own "
        f"background-color rule unless this attribute is set")


# ── the text has room ───────────────────────────────────────────────────────

def test_the_resume_offer_gets_the_width_of_the_sidebar(strips, tmp_path):
    """Not a column beside two buttons. This is the defect, measured."""
    tab, _host = strips
    _journal_offer(tab, tmp_path)
    banner = tab._resume_banner
    share = tab._resume_label.width() / banner.width()
    assert share >= _MIN_TEXT_SHARE, (
        f"the offer's prose has {tab._resume_label.width()} px of the "
        f"{banner.width()} px strip; the buttons are beside the text again")


def test_the_pre_run_report_gets_the_width_of_the_sidebar(strips):
    """Its buttons are the wider pair, so it was the worse of the two."""
    tab, _host = strips
    tab._preflight_label.setText(
        "A run started 22 Aug 09:14 and was stopped. It recorded 8 analysed, "
        "1 failed. 6 of 24 well(s) are covered by the output already here.")
    tab._preflight_banner.setVisible(True)
    tab._preflight_banner.parentWidget().adjustSize()
    banner = tab._preflight_banner
    share = tab._preflight_label.width() / banner.width()
    assert share >= _MIN_TEXT_SHARE, (
        f"the report has {tab._preflight_label.width()} px of the "
        f"{banner.width()} px strip; 'Load these settings' is beside it again")


# ── what the offer says, and in what order ──────────────────────────────────

def test_the_offer_is_ranked_rather_than_run_together(strips, tmp_path):
    """Four questions, four labels, in the order they are asked.

    One wrapping paragraph renders the state, the plate, the prose and the
    tally in one weight, with nothing to say where any of them ends.
    """
    tab, _host = strips
    _journal_offer(tab, tmp_path,
                   wells=(("A01", "analysed"), ("A02", "failed")))
    assert tab._resume_kicker.text() == "Failed run"
    assert tab._resume_title.text().startswith("Plate_07_HCS")
    assert "stopped on an error" in tab._resume_label.text()
    assert tab._resume_tally.text() == "It recorded 1 finished, 1 failed."


def test_the_tally_line_goes_away_when_there_is_no_tally(strips, tmp_path):
    """An empty label still takes a line and the column's spacing with it —
    a gap under the prose with nothing in it reads as a rendering fault."""
    tab, _host = strips
    _journal_offer(tab, tmp_path, wells=())
    assert tab._resume_tally.text() == ""
    assert tab._resume_tally.isHidden()


def test_the_tally_stands_alone_as_a_sentence(strips, tmp_path):
    """``RunRecord.tally`` is written to be concatenated — it opens with a
    space so it reads on from whatever precedes it. On its own line that space
    is a visible indent, so the strip strips it; the sentence itself is left
    exactly as the journal wrote it, because the pre-run report still
    concatenates the same string."""
    tab, _host = strips
    _journal_offer(tab, tmp_path)
    text = tab._resume_tally.text()
    assert text == text.strip() and text.startswith("It recorded")


# ── the strips cost nothing when there is nothing to say ────────────────────

def test_hidden_strips_reserve_no_height(strips):
    """The normal state is both hidden. Their holder must not leave a gap at
    the head of the form for two things that are not there."""
    tab, host = strips
    tab._resume_banner.setVisible(False)
    tab._preflight_banner.setVisible(False)
    holder = tab._resume_banner.parentWidget()
    holder.adjustSize()
    assert holder.sizeHint().height() == 0, (
        f"the banner holder reserves {holder.sizeHint().height()} px with "
        f"both strips hidden")


# ── the vocabulary is complete ──────────────────────────────────────────────

def test_every_unfinished_outcome_has_a_kicker_and_a_phrase():
    """The strip names the state in two words above the sentence explaining
    it. An outcome the journal can record and the strip cannot spell falls
    into a generic fallback, and the user is told less than the app knows."""
    import analyze_suspend as A

    missing = [o for o in J.UNFINISHED_OUTCOMES
               if o not in A._UNFINISHED_KICKERS or o not in A._UNFINISHED_PHRASES]
    assert not missing, (
        f"run_journal.UNFINISHED_OUTCOMES carries {missing}, which the resume "
        f"strip has no words for")
    assert None in A._UNFINISHED_KICKERS, (
        "a run the app was killed during closed no record and has no outcome "
        "at all; it is keyed on None rather than reached as a fallback, so an "
        "outcome outside the roster is not described to the user as a crash")


def test_the_incremental_promise_is_made_once(strips, tmp_path):
    """The strip, the one-sentence headline and the log line all say what
    Resume does. Three separately-worded copies is how a user reads two
    different accounts of the same behaviour."""
    import analyze_suspend as A

    tab, _host = strips
    out = _journal_offer(tab, tmp_path)
    offer = tab._resumable(str(out))
    assert A._INCREMENTAL_NOTE in offer.detail
    assert A._INCREMENTAL_NOTE in offer.headline


def test_the_four_text_roles_are_styled(strips, tmp_path):
    """Each label carries an object name the stylesheet has a rule for.
    A tier with no rule falls back to body text and the ranking is gone."""
    tab, _host = strips
    _journal_offer(tab, tmp_path)
    qss = theme.qss()
    labels = [tab._resume_kicker, tab._resume_title, tab._resume_label,
              tab._resume_tally, tab._preflight_label]
    for label in labels:
        assert isinstance(label, QLabel)
        name = label.objectName()
        assert name, "a banner label with no object name cannot be styled"
        assert f"QLabel#{name}" in qss, f"#{name} has no rule in theme.qss()"
