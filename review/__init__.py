"""All-Well viewer package."""


def __getattr__(name):  # PEP 562 lazy import so pure-service modules don't pull GUI
    if name == "ReviewApp":
        from .runtime_app import ReviewApp
        return ReviewApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ReviewApp"]
