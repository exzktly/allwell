"""Pure test for the Analyze 'Run on:' default-selection helper.

The combo is ``Local`` (index 0) then one item per configured remote; the run
should default to the first configured remote when any exist, else Local.
"""

from __future__ import annotations

from services.remote_config import default_remote_index


def test_only_local_defaults_to_local():
    # Combo has just "Local" → no remotes configured → stay on Local (index 0).
    assert default_remote_index(1) == 0


def test_one_or_more_remotes_default_to_first_remote():
    assert default_remote_index(2) == 1   # Local + 1 remote
    assert default_remote_index(4) == 1   # Local + 3 remotes → first remote


def test_empty_combo_is_local():
    # Defensive: an empty combo (shouldn't happen — Local is always added) → 0.
    assert default_remote_index(0) == 0
