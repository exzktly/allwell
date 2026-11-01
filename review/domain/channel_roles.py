"""Which of a dataset's channels is the phase (brightfield) channel, and what that means for a channel list.

A run that segments on a phase image writes a ``<PHASE>_mean_intensity`` column beside every fluorescence
channel's, and its sidecar lists the phase token among ``fluor_tokens``. A nanowell run always does. The number in
that column is the brightness of a brightfield image under a cell — not a fluorescence measurement — so a plot
that opens on it shows a meaningless value with a fluorescence title.

Three readers used to answer "which channel is phase?" in three ways: the frame loader read the sidecar's top-level
``phase_token``, the Nanowells page read ``execution_options.phase_token`` first, and the plot channel list did not
ask at all, so the phase channel became the default plotting channel whenever the sidecar happened to list it first.
This module is the one answer, and :func:`order_fluor_channels` / :func:`default_fluor_channel` are what the channel
list does with it: the phase channel stays selectable (a label-free plate may have nothing else) but is listed last
and is never the default while any other channel exists.

Qt-free and import-free, so the data layer, the core controllers and any feature may call it.
"""

from __future__ import annotations

from typing import Any


def phase_channel(pipeline_info: dict[str, Any] | None) -> str:
    """The dataset's phase / brightfield channel token, lower-cased, or ``""`` when the run named none.

    The sidecar's ``execution_options.phase_token`` is what the run was given; the top-level ``phase_token`` is
    the same value written beside it by every build that writes one. Either is accepted, the run's own option
    first, so a sidecar written by a build that recorded only one of them still names its phase channel.
    """
    info = pipeline_info if isinstance(pipeline_info, dict) else {}
    options = info.get("execution_options") if isinstance(info.get("execution_options"), dict) else {}
    token = options.get("phase_token") or info.get("phase_token") or ""
    return str(token).strip().lower()


def is_phase(channel: str, pipeline_info: dict[str, Any] | None) -> bool:
    """Whether *channel* (any case) is the dataset's phase channel."""
    phase = phase_channel(pipeline_info)
    return bool(phase) and str(channel or "").strip().lower() == phase


def order_fluor_channels(channels: list[str], pipeline_info: dict[str, Any] | None) -> list[str]:
    """*channels* with the phase channel moved to the end; every other channel keeps its order."""
    phase = phase_channel(pipeline_info)
    if not phase:
        return list(channels)
    rest = [c for c in channels if str(c).strip().lower() != phase]
    return rest + [c for c in channels if str(c).strip().lower() == phase]


def default_fluor_channel(channels: list[str], pipeline_info: dict[str, Any] | None) -> str:
    """The channel a plot opens on: the first that is not the phase channel, else the first, else ``""``."""
    for channel in channels:
        if not is_phase(channel, pipeline_info):
            return channel
    return channels[0] if channels else ""
