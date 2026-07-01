"""All-Well viewer package."""

from .core import debug_flags


def __getattr__(name):  # PEP 562 lazy import so pure-service modules don't pull GUI
    if name == "WellViewerApp":
        from .runtime_app import WellViewerApp
        return WellViewerApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["WellViewerApp", "debug_flags"]
