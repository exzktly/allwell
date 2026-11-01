"""A run that lists its phase channel first must not open its plots on it (ARCHITECTURE §6.2l).

A nanowell run writes ``fluor_tokens: ["PHASE", "GFP"]``, and the channel list used to take the first token as the
default, so every plot opened on "PHASE Mean Intensity" — the brightness of a brightfield image, titled as if it
were fluorescence. ``review/domain/channel_roles`` is the one answer to "which channel is phase", and the list keeps
the phase channel (a label-free plate may have nothing else) but last and never the default.
"""

from __future__ import annotations

import pytest

from review.domain.channel_roles import default_fluor_channel, is_phase, order_fluor_channels, phase_channel

INFO = {"phase_token": "PHASE", "execution_options": {"phase_token": "PHASE"}}


def test_the_run_s_own_option_names_the_phase_channel_when_the_top_level_field_is_missing():
    assert phase_channel({"execution_options": {"phase_token": "BF"}}) == "bf"
    assert phase_channel({"phase_token": "BF"}) == "bf"
    assert phase_channel(None) == "" and not is_phase("gfp", None)


def test_the_phase_channel_is_listed_last_and_the_rest_keep_their_order():
    assert order_fluor_channels(["phase", "rfp", "gfp"], INFO) == ["rfp", "gfp", "phase"]
    assert order_fluor_channels(["phase", "gfp"], {}) == ["phase", "gfp"]      # no phase named: untouched


def test_the_default_skips_phase_but_a_phase_only_plate_still_has_a_channel():
    assert default_fluor_channel(["phase", "gfp"], INFO) == "gfp"
    assert default_fluor_channel(["phase"], INFO) == "phase"
    assert default_fluor_channel([], INFO) == ""


def test_a_nanowell_dataset_opens_on_its_fluorescence_channel(shell_qtbot_ctx):
    ctx = shell_qtbot_ctx
    # PHASE is first in the sidecar, as a nanowell run writes it.
    assert str(ctx.state.dataset.pipeline_info["fluor_tokens"][0]).lower() == "phase"
    assert ctx.state.channel.active_channel == "gfp"
    assert ctx.state.channel.fluor_channels == ["gfp", "phase"]


@pytest.fixture
def shell_qtbot_ctx(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, root / "out")
    return ctx
