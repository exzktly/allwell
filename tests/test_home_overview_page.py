"""Home › Overview as the design language draws it (tracker row D27), on the page the app builds.

The title is the dataset's name with a quiet Reload; the one thing that needs doing is a notice whose severity
follows the step and whose one button does it; a value the record does not hold reads "not recorded" in the quiet
ink rather than a dash. Built through ``build_home_overview`` against a loaded dataset (``shell_ctx``), so it runs
in the fast lane.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

from tests.test_home_phone_layout import _overview


def test_the_title_has_a_quiet_reload(shell_ctx, qtbot):
    ctx, _scroll = _overview(shell_ctx, qtbot, width=1200)
    title = ctx.widgets.get("_home_title")
    assert title.property("textRole") == "pageTitle"
    assert ctx.widgets.get("_home_path").property("textRole") == "number"
    reload = ctx.widgets.get("_home_action_reload")
    assert (reload.text(), reload.property("variant")) == ("Reload", "quiet")


def test_a_dataset_with_no_record_shows_one_attention_notice_with_one_button(shell_ctx, qtbot, monkeypatch):
    from review.features.home import collect as hc
    from review.features.home.controller import refresh_home
    from widgets.notice import Notice

    ctx, _scroll = _overview(shell_ctx, qtbot, width=1200)
    monkeypatch.setattr(hc, "record_state", lambda _ctx: "none")
    refresh_home(ctx)
    notice = ctx.widgets.get("_home_next_step")
    assert isinstance(notice, Notice)
    assert (notice.severity(), notice.lead()) == ("attention", "No completeness record.")
    assert notice.text() == "This dataset was produced before the check existed, or the check could not run."
    assert notice.action_button().text() == "Check it now"

    # And the step's severity, not a fixed one: nothing to do is information, never amber.
    monkeypatch.setattr(hc, "next_step", lambda _ctx: {
        "severity": "ok", "lead": "Nothing needs attention.", "text": "Nothing needs attention.",
        "action": "plot", "label": "Open Plotting"})
    refresh_home(ctx)
    assert (notice.severity(), notice.lead(), notice.text()) == ("information", "Nothing needs attention.", "")


def test_a_run_parameter_the_record_does_not_hold_reads_not_recorded(shell_ctx, qtbot, monkeypatch):
    from review.features.home import collect as hc
    from review.features.home.controller import refresh_home

    ctx, _scroll = _overview(shell_ctx, qtbot, width=1200)
    monkeypatch.setattr(hc, "_sidecar", lambda _ctx: {"qc_options": {"flatfield": "basic"}})
    refresh_home(ctx)
    receipt = ctx.widgets.get("_home_receipt")
    texts = {}
    for row in receipt.findChildren(QLabel):
        texts.setdefault(row.parentWidget(), []).append(row)
    pairs = {labels[0].text(): labels[1] for labels in texts.values() if len(labels) >= 2}
    for name in ("Segmentation", "Background subtraction"):
        assert pairs[name].text() == "not recorded", name
        assert pairs[name].property("unset") is True, name
    assert pairs["Flat-field"].text() == "basic"
