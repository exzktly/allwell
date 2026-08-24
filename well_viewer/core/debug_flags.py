"""Shared runtime debug toggles for the All-Well app.

Flags are grouped by UI tab so debug output can be enabled per-tab.
"""
from __future__ import annotations

import inspect
import os
from typing import Any

# Master switches by tab
REVIEW_TAB_DEBUG: bool = False
ANALYZE_TAB_DEBUG: bool = False

# Review tab debug switches
REVIEW_BAR_DEBUG: bool = False
REVIEW_SCATTER_DEBUG: bool = False
REVIEW_IMAGE_DEBUG: bool = False
REVIEW_IMAGE_LOAD_DEBUG: bool = False
# Focused channel-switch trace. It shipped ON — "kept ON for targeted
# diagnosis" outlived the diagnosis, and every user's log then carried a trace
# nobody had asked for while the documentation said the flags are off by
# default. ``tests/test_debug_flags_default_off.py`` holds the whole roster to
# False so the next targeted session has to turn its flag back off.
REVIEW_IMAGE_CHANNEL_SWITCH_DEBUG: bool = False

# Movie Montage tab debug switches
MOVIE_MONTAGE_DEBUG: bool = False
MOVIE_MONTAGE_LOAD_DEBUG: bool = False

# Cell Gating tab debug switch (covers GatingWorker progress/start/finish logs).
CELL_GATING_DEBUG: bool = False

#: Every toggle this module exposes, in declaration order. ``allwell.main``
#: copies its own top-of-file constants onto these by name; it used to name four
#: of them one at a time, so the other six could only be changed by editing this
#: file — and two of those six shipped ON.
FLAG_NAMES: tuple[str, ...] = (
    "REVIEW_TAB_DEBUG",
    "ANALYZE_TAB_DEBUG",
    "REVIEW_BAR_DEBUG",
    "REVIEW_SCATTER_DEBUG",
    "REVIEW_IMAGE_DEBUG",
    "REVIEW_IMAGE_LOAD_DEBUG",
    "REVIEW_IMAGE_CHANNEL_SWITCH_DEBUG",
    "MOVIE_MONTAGE_DEBUG",
    "MOVIE_MONTAGE_LOAD_DEBUG",
    "CELL_GATING_DEBUG",
)


def review_bar_debug_enabled() -> bool:
    """Return whether Review tab bar-plot debug output is enabled."""
    return REVIEW_TAB_DEBUG or REVIEW_BAR_DEBUG


def review_scatter_debug_enabled() -> bool:
    """Return whether Review tab scatter debug output is enabled."""
    return REVIEW_TAB_DEBUG or REVIEW_SCATTER_DEBUG


def review_image_load_debug_enabled() -> bool:
    """Return whether Review Image image-loading debug output is enabled."""
    return REVIEW_TAB_DEBUG or REVIEW_IMAGE_LOAD_DEBUG


def review_image_channel_switch_debug_enabled() -> bool:
    """Return whether focused Review Image channel-switch tracing is enabled."""
    return REVIEW_IMAGE_CHANNEL_SWITCH_DEBUG


def movie_montage_debug_enabled() -> bool:
    """Return whether Movie Montage tab debug output is enabled."""
    return REVIEW_TAB_DEBUG or MOVIE_MONTAGE_DEBUG


def movie_montage_load_debug_enabled() -> bool:
    """Return whether Movie Montage image-loading debug output is enabled."""
    return REVIEW_TAB_DEBUG or MOVIE_MONTAGE_LOAD_DEBUG


def cell_gating_debug_enabled() -> bool:
    """Return whether Cell Gating tab debug output is enabled."""
    return CELL_GATING_DEBUG


def debug_with_source(logger: Any, message: str, *args: Any) -> None:
    """Emit a debug log with source file + line of the call site.

    Output prefix example: [runtime_app.py:4621]
    """
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    if caller is None:
        logger.debug(message, *args)
        return
    filename = os.path.basename(caller.f_code.co_filename)
    lineno = caller.f_lineno
    logger.debug("[%s:%d] " + message, filename, lineno, *args)
