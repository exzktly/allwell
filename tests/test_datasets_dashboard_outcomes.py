"""Every Datasets dashboard ends a run through the same three setters.

`set_done`, `set_failed` and `set_cancelled` were byte-identical copies on
`import_loose`, `subset` and `merge_plates`, with `concatenate` carrying the
same three plus a trailing `_refresh_rail()` and `validate` carrying two of
them. Five copies of one decision — the wording, the tone, and the rule that a
shortened headline keeps its full text as a tooltip — is how five dashboards
drift, the same way §6b describes the log furniture drifting before
`LogPanelMixin`.

They live on `widgets.plate_progress.PlateProgress` now, which every one of
these already subclasses. A dashboard with something extra to do on a terminal
state overrides `_on_outcome_changed`, not the setter — otherwise the
no-override rule below could not hold, and the copies would be back.

The roster is read from `datasets.tab._FEATURES`, so a seventh tool is swept
from the moment it is registered.
"""

from __future__ import annotations

import importlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
from widgets.plate_progress import PlateProgress  # noqa: E402

SHARED_MEMBERS = ("set_done", "set_failed", "set_cancelled")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dashboards() -> dict[str, type]:
    """kind -> dashboard class, for every registered tool that has one.

    Not every tool draws a plate dashboard (Convert OME-TIFF reports on the
    acquisition plate the page already carries), so a missing module is a
    legitimate answer rather than a failure.
    """
    from datasets.tab import _FEATURES

    out = {}
    for feature in _FEATURES:
        if not feature.kind:
            continue
        try:
            module = importlib.import_module(f"datasets.{feature.kind}.dashboard")
        except ModuleNotFoundError:
            continue
        cls = next((obj for name, obj in vars(module).items()
                    if isinstance(obj, type) and issubclass(obj, PlateProgress)
                    and obj is not PlateProgress), None)
        if cls is not None:
            out[feature.kind] = cls
    return out


ALL = sorted(_dashboards())


def test_the_sweep_found_the_dashboards():
    """A sweep that silently found nothing would pass every test below."""
    assert len(ALL) >= 5, f"only found dashboards for {ALL}"


@pytest.mark.parametrize("member", SHARED_MEMBERS)
def test_no_dashboard_re_hand_rolls_an_outcome_setter(member):
    offenders = [name for name, cls in _dashboards().items() if member in vars(cls)]
    assert not offenders, (
        f"{member} is re-defined on: {offenders}. Override `_on_outcome_changed` "
        "instead — the setter's wording and tone are one decision for every "
        "dashboard."
    )


@pytest.mark.parametrize("kind", ALL)
def test_every_dashboard_paints_the_same_terminal_states(qapp, kind):
    dash = _dashboards()[kind]()

    dash.set_done("Wrote 12 wells to /very/long/destination/path")
    assert theme.Colors.success in dash._headline.styleSheet()
    # The headline may be shortened for display; the full text stays reachable.
    assert dash._headline.toolTip() == "Wrote 12 wells to /very/long/destination/path"

    dash.set_done("short", tooltip="the whole story")
    assert dash._headline.toolTip() == "the whole story"

    dash.set_failed("disk full")
    assert dash._headline.text() == "Failed — disk full"
    assert theme.Colors.danger in dash._headline.styleSheet()
    # Same contract as set_done: a caller that shortens what it displays keeps
    # the whole of it reachable.
    assert dash._headline.toolTip() == "disk full"
    dash.set_failed("short", tooltip="the whole refusal, all 700 characters")
    assert dash._headline.toolTip() == "the whole refusal, all 700 characters"

    dash.set_cancelled()
    assert dash._headline.text() == "Cancelled"
    assert theme.Colors.warn in dash._headline.styleSheet()
    # A cancel carries no explanation, so it must not leave the failure's.
    assert dash._headline.toolTip() == ""


@pytest.mark.parametrize("kind", ALL)
def test_reset_forgets_the_previous_run_s_outcome(qapp, kind):
    """A dashboard that kept a finished run's terminal state would describe the
    last run while the next one is still going."""
    dash = _dashboards()[kind]()
    dash.set_failed("something went wrong")
    assert dash._outcome == "failed"
    dash.reset()
    assert dash._outcome == ""
    # The explanation goes with the state. A tooltip nobody clears outlives the
    # run it describes — hover a *successful* plate and read the previous run's
    # failure, which is the "degraded output indistinguishable from good output"
    # these outcome states exist to prevent.
    assert dash._headline.toolTip() == ""


# ── the wording itself ───────────────────────────────────────────────────────
#
# `apply_outcome` is the decision one step further out than the setters above:
# a page can report an outcome without owning a dashboard (Convert OME-TIFF),
# and it must read the same way there.


def test_a_message_that_already_names_the_state_does_not_say_it_twice(qapp):
    from PySide6.QtWidgets import QLabel

    from widgets.plate_progress import apply_outcome

    label = QLabel()
    # Every Datasets worker announces a stopped run this way.
    apply_outcome(label, "cancelled", "Cancelled — partial output was left.")
    assert label.text() == "Cancelled — partial output was left."


def test_a_failure_that_begins_with_its_state_survives_whole(qapp):
    """"Failed to open …" is a sentence, not a duplicated prefix."""
    from PySide6.QtWidgets import QLabel

    from widgets.plate_progress import apply_outcome

    label = QLabel()
    apply_outcome(label, "failed", "Failed to open the destination folder.")
    assert label.text() == "Failed — Failed to open the destination folder."


def test_an_empty_message_falls_back_to_the_state_word(qapp):
    from PySide6.QtWidgets import QLabel

    from widgets.plate_progress import apply_outcome

    label = QLabel()
    apply_outcome(label, "failed", "Failed")
    assert label.text() == "Failed"
    apply_outcome(label, "done", "")
    assert label.text() == "Done"
