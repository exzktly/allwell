"""Tracking engine registry.

Engines register a name, a display label, an availability flag (False when the
backing library isn't installed), and a ``run`` callable. The GUI engine picker
and the headless ``--track`` path both resolve through :func:`get_engine` /
:func:`available_engines`. ``greedy`` is always available (no heavy deps).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from . import greedy, btrack_engine, trackastra_engine
from .base import (  # re-export the public types
    Detection, EngineParams, FrameDetections, TrackResult, UNTRACKED,
)


@dataclass(frozen=True)
class EngineInfo:
    name: str
    display_name: str
    available: bool
    requires: tuple
    run: Callable[[List[FrameDetections], EngineParams], TrackResult]


_ENGINES: Dict[str, EngineInfo] = {}


def _register(mod) -> None:
    _ENGINES[mod.NAME] = EngineInfo(
        name=mod.NAME, display_name=mod.DISPLAY_NAME,
        available=bool(getattr(mod, "AVAILABLE", True)),
        requires=tuple(getattr(mod, "REQUIRES", ())),
        run=mod.run,
    )


_register(greedy)
_register(btrack_engine)
_register(trackastra_engine)

DEFAULT_ENGINE = greedy.NAME


def all_engines() -> List[EngineInfo]:
    """Every registered engine, in registration order."""
    return list(_ENGINES.values())


def available_engines() -> List[EngineInfo]:
    """Only engines whose backing library is installed."""
    return [e for e in _ENGINES.values() if e.available]


def get_engine(name: str) -> EngineInfo:
    """Look up an engine by name (raises ``KeyError`` if unknown)."""
    return _ENGINES[name]


def run_tracking(name: str, frames: List[FrameDetections], params: EngineParams) -> TrackResult:
    """Resolve *name* and run it; clear error when the engine is unavailable."""
    info = _ENGINES.get(name)
    if info is None:
        raise KeyError(f"Unknown tracking engine {name!r}. "
                       f"Available: {[e.name for e in all_engines()]}")
    if not info.available:
        raise RuntimeError(
            f"Tracking engine {name!r} is unavailable — install: "
            f"{', '.join(info.requires) or '(n/a)'}.")
    return info.run(frames, params)


__all__ = [
    "Detection", "EngineParams", "FrameDetections", "TrackResult", "UNTRACKED",
    "EngineInfo", "DEFAULT_ENGINE", "all_engines", "available_engines",
    "get_engine", "run_tracking",
]
