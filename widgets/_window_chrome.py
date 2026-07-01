"""Window-chrome policy — should the app draw its own frameless title bar, or
defer to the OS window frame?

`should_use_frameless()` resolves the question with a fixed precedence (first
hit wins); `frameless_source()` reports *which* rule decided (handy for a
debug/about read-out). `set_frameless_preference()` lets a Preferences panel
override the platform default at runtime.

Precedence
----------
1. **env `ALLWELL_FRAMELESS`** — ``1/true/yes/on`` → frameless;
   ``0/false/no/off`` → native. (Anything else: ignored.)
2. **explicit preference** — whatever was last passed to
   :func:`set_frameless_preference` (``None`` clears it).
3. **accessibility probe** — if a screen reader / high-contrast mode looks
   active, prefer the **native** frame (custom chrome is harder for AT to
   navigate). Best-effort, per-platform; see :func:`accessibility_prefers_native`.
4. **platform default** — frameless on Windows/Linux; native on macOS (the
   native traffic lights + ``startSystemResize`` history make manual
   resize necessary).
"""

from __future__ import annotations

import os as _os
import sys as _sys

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_pref: bool | None = None   # set via set_frameless_preference()


def set_frameless_preference(value: bool | None) -> None:
    """Override the platform default at runtime (``None`` clears the override)."""
    global _pref
    _pref = None if value is None else bool(value)


def _env_choice() -> bool | None:
    raw = _os.environ.get("ALLWELL_FRAMELESS")
    if raw is None:
        return None
    raw = raw.strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def accessibility_prefers_native() -> bool:
    """Best-effort: does an assistive-tech / high-contrast environment look active?

    * Windows: ``SystemParametersInfoW(SPI_GETHIGHCONTRAST)`` HCF_HIGHCONTRASTON.
    * Any platform: ``QT_ACCESSIBILITY=1`` (Qt's own opt-in env) is treated as
      "AT active".
    Extend per-platform as needed; conservatively returns ``False`` on error.
    """
    if _os.environ.get("QT_ACCESSIBILITY", "").strip() == "1":
        return True
    if _sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            class _HIGHCONTRAST(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT),
                            ("dwFlags", wintypes.DWORD),
                            ("lpszDefaultScheme", wintypes.LPWSTR)]

            SPI_GETHIGHCONTRAST = 0x0042
            HCF_HIGHCONTRASTON = 0x00000001
            hc = _HIGHCONTRAST()
            hc.cbSize = ctypes.sizeof(_HIGHCONTRAST)
            ok = ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETHIGHCONTRAST, hc.cbSize, ctypes.byref(hc), 0)
            if ok:
                return bool(hc.dwFlags & HCF_HIGHCONTRASTON)
        except Exception:
            return False
    return False


def _platform_default_frameless() -> bool:
    return _sys.platform != "darwin"


def frameless_source() -> str:
    """Which rule decides `should_use_frameless()` right now."""
    if _env_choice() is not None:
        return "env:ALLWELL_FRAMELESS"
    if _pref is not None:
        return "preference"
    if accessibility_prefers_native():
        return "accessibility"
    return "platform-default"


def should_use_frameless() -> bool:
    env = _env_choice()
    if env is not None:
        return env
    if _pref is not None:
        return _pref
    if accessibility_prefers_native():
        return False
    return _platform_default_frameless()


if __name__ == "__main__":
    print("should_use_frameless():", should_use_frameless())
    print("frameless_source()    :", frameless_source())
    print("platform default      :", _platform_default_frameless())
    print("accessibility native  :", accessibility_prefers_native())
