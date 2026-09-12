"""Availability of the ``smFISH Count`` Property option.

Two independent defects kept the option unusable on datasets that carry
``<channel>_smfish_count`` columns:

1. ``recalculate_threshold`` published the freshly-detected smFISH channel set
   *after* ``update_channel_selector`` had already repopulated every Property
   combo, so the item stayed greyed out until some later event re-ran the
   refresh. Changing the channel and changing it back was the workaround.
2. Every combo that passes its channel as a *displayed* label — scatter, stats,
   batch export, all built from upper-cased names — compared that label against
   the normalised (lower-case) token set, so the item never enabled there at
   all, wiggle or no wiggle.

All Qt-free: the combo tests drive ``populate_metric_combo`` with a fake combo,
and the ordering test drives ``recalculate_threshold`` against typed state with
no dataset loaded.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from review.core.channel_state import ChannelState
from review.core.dataset_state import DatasetState
from review.domain.metric_registry import channel_supports_smfish


# ── the shared predicate ────────────────────────────────────────────────────

def test_channel_supports_smfish_is_case_insensitive():
    channels = {"cy5", "w3cy5"}
    assert channel_supports_smfish("cy5", channels) is True
    assert channel_supports_smfish("CY5", channels) is True   # displayed label
    assert channel_supports_smfish(" Cy5 ", channels) is True
    assert channel_supports_smfish("gfp", channels) is False


def test_channel_supports_smfish_handles_empty_inputs():
    assert channel_supports_smfish("", {"cy5"}) is False
    assert channel_supports_smfish("cy5", set()) is False
    assert channel_supports_smfish("cy5", None) is False


# ── the Property combo ──────────────────────────────────────────────────────

class _FakeItem:
    def __init__(self):
        self.enabled: bool | None = None

    def setEnabled(self, value):
        self.enabled = bool(value)


class _FakeModel:
    def __init__(self):
        self.items: dict[int, _FakeItem] = {}

    def item(self, idx):
        return self.items.setdefault(idx, _FakeItem())


class _FakeCombo:
    """Minimal QComboBox stand-in — enough surface for populate_metric_combo."""

    def __init__(self):
        self.items: list[str] = []
        self._cur = ""
        self._model = _FakeModel()

    def currentText(self):
        return self._cur

    def blockSignals(self, _b):
        return False

    def clear(self):
        self.items = []

    def addItem(self, text):
        self.items.append(text)

    def addItems(self, texts):
        self.items.extend(texts)

    def setEnabled(self, _b):
        pass

    def setCurrentText(self, text):
        self._cur = text

    def setCurrentIndex(self, idx):
        self._cur = self.items[idx] if 0 <= idx < len(self.items) else ""

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def model(self):
        return self._model

    def smfish_item(self) -> _FakeItem:
        return self._model.item(self.findText("smFISH Count"))


def _ctx_with_smfish(*channels: str):
    channel = ChannelState()
    channel.smfish_channels = set(channels)
    return SimpleNamespace(state=SimpleNamespace(channel=channel))


def test_smfish_item_enables_for_an_upper_case_channel_label():
    """The scatter / stats regression: those combos hold "CY5", the state holds
    "cy5". Comparing them raw left the item disabled on every channel."""
    from review.core.channel_state_controller import populate_metric_combo

    cb = _FakeCombo()
    populate_metric_combo(
        _ctx_with_smfish("cy5"), cb, channel_entry="CY5", smfish_channel="CY5",
    )

    assert "smFISH Count" in cb.items
    assert cb.smfish_item().enabled is True


def test_smfish_item_enables_for_the_normalised_token():
    """The global Property combo passes the token itself — still works."""
    from review.core.channel_state_controller import populate_metric_combo

    cb = _FakeCombo()
    populate_metric_combo(
        _ctx_with_smfish("cy5"), cb, channel_entry="CY5", smfish_channel="cy5",
    )

    assert cb.smfish_item().enabled is True


def test_smfish_item_stays_disabled_for_a_non_smfish_channel():
    from review.core.channel_state_controller import populate_metric_combo

    cb = _FakeCombo()
    populate_metric_combo(
        _ctx_with_smfish("cy5"), cb, channel_entry="GFP", smfish_channel="GFP",
    )

    assert cb.smfish_item().enabled is False


# ── recalculate_threshold ordering ──────────────────────────────────────────

def test_smfish_channels_are_published_before_the_combos_repopulate(monkeypatch):
    """``update_channel_selector`` ends in the Property-combo refresh, which
    reads ``smfish_channels`` to decide whether smFISH Count is selectable. The
    set therefore has to be current *before* that call — publishing it after
    left the item greyed out against columns that already existed on disk."""
    import review.core.channel_state_controller as csc

    seen: list[set[str]] = []
    monkeypatch.setattr(
        csc, "update_channel_selector",
        lambda ctx: seen.append(set(ctx.state.channel.smfish_channels)),
    )
    # No dataset is loaded, so the sample frame is empty and every detector
    # returns nothing — stub the one under test to report a channel.
    monkeypatch.setattr(csc, "detect_smfish_channels", lambda _df: ["Cy5"])

    ctx = SimpleNamespace(
        state=SimpleNamespace(channel=ChannelState(), dataset=DatasetState()),
        widgets=SimpleNamespace(get_or_none=lambda _name: None),
        features=None,
    )
    csc.recalculate_threshold(ctx)

    assert seen == [{"cy5"}], (
        "update_channel_selector saw the wrong smFISH channel set — it must run "
        "after the set is published, and the tokens must be normalised"
    )
    assert ctx.state.channel.smfish_channels == {"cy5"}


def test_recalculate_threshold_clears_smfish_channels_when_none_are_detected(monkeypatch):
    """The set is re-derived from the loaded CSVs each pass, so a dataset with
    no count columns must not inherit the previous dataset's channels."""
    import review.core.channel_state_controller as csc

    monkeypatch.setattr(csc, "update_channel_selector", lambda _ctx: None)
    monkeypatch.setattr(csc, "detect_smfish_channels", lambda _df: [])

    channel = ChannelState()
    channel.smfish_channels = {"cy5"}
    ctx = SimpleNamespace(
        state=SimpleNamespace(channel=channel, dataset=DatasetState()),
        widgets=SimpleNamespace(get_or_none=lambda _name: None),
        features=None,
    )
    csc.recalculate_threshold(ctx)

    assert ctx.state.channel.smfish_channels == set()


def test_recalculate_threshold_drops_a_stale_smfish_metric(monkeypatch):
    """Invariant kept from the reorder: a channel with no smFISH data must not
    be left holding the ``smfish_count`` metric."""
    import review.core.channel_state_controller as csc

    monkeypatch.setattr(csc, "update_channel_selector", lambda _ctx: None)
    monkeypatch.setattr(csc, "detect_smfish_channels", lambda _df: [])

    channel = ChannelState()
    channel.active_channel = "gfp"
    channel.active_metric = "smfish_count"
    ctx = SimpleNamespace(
        state=SimpleNamespace(channel=channel, dataset=DatasetState()),
        widgets=SimpleNamespace(get_or_none=lambda _name: None),
        features=None,
    )
    csc.recalculate_threshold(ctx)

    assert channel.active_metric == "mean_intensity"
    assert channel.active_val_col == "gfp_mean_intensity"


def test_detectors_tolerate_an_unloaded_dataset():
    """Guards the assumption the ordering tests above lean on: with no wells
    loaded the sample frame is empty and detection is a no-op, not a crash."""
    from review.data.data_loading import (
        detect_fluor_channels, detect_smfish_channels,
    )

    assert detect_fluor_channels(pd.DataFrame()) == []
    assert detect_smfish_channels(pd.DataFrame()) == []
