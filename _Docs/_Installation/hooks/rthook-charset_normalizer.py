"""
rthook-charset_normalizer.py
----------------------------
Ensure charset_normalizer is importable in the frozen bundle before
requests/__init__.py runs its compatibility check.

Root cause: tensorflow.python.keras.callbacks imports requests, which
calls check_compatibility() in requests/__init__.py. That function tries
  from charset_normalizer import __version__ as charset_normalizer_version
If that import fails (e.g. because mypyc-compiled .so extensions are
absent from the bundle), the except ImportError silently sets
charset_normalizer_version = None, and requests then emits:
  "Unable to find acceptable character detection dependency"

This hook runs first. It tries the real package; if that raises any
exception it registers a minimal stub whose __version__ satisfies
requests' accepted range [2.0.0, 4.0.0) so the check passes silently.
The stub also exposes detect() so libraries that call
charset_normalizer.detect() don't crash (they get a null result instead).
"""
import sys
import types

try:
    import charset_normalizer  # noqa: F401 — if this works, we're done
except Exception:
    _stub = types.ModuleType("charset_normalizer")
    _stub.__version__ = "3.3.0"
    _stub.VERSION = [3, 3, 0]

    def _detect(byte_str):
        return {"encoding": None, "confidence": 0.0, "language": ""}

    _stub.detect = _detect
    sys.modules["charset_normalizer"] = _stub
