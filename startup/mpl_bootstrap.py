"""Give matplotlib a persistent, local cache without importing its font manager.

AllWell renders every plot with its own bundled fonts (``assets/fonts`` — Inter
/ JetBrains Mono) plus matplotlib's built-in DejaVu fallback. It never uses a
system font. But matplotlib, on its first cache build, parses *every* font file
installed on the machine to build ``fontlist-v*.json`` — hundreds of files on a
Windows box, and on a Windows 365 Cloud PC (networked/roaming storage) that scan
can take minutes on first launch.

``configure()`` pins the cache to a stable local directory.  Crucially, it does
*not* import ``matplotlib.font_manager``: that module creates its global
``FontManager`` while it is being imported, before a caller can replace
``findSystemFonts``.  The former bootstrap imported it during every application
launch and only patched discovery after the expensive scan had already happened.
On a cold or unusable cache that made the splash-less launch appear hung for
minutes on both Windows and macOS.

Call this once before matplotlib's first plotting import (from ``allwell.main``).
The first plot may still pay matplotlib's own cold-cache cost, but application
startup no longer does.  A warm cache is reused on subsequent plotting imports.
"""

from __future__ import annotations

import os
import sys as _sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_root() -> str:
    """Where the pinned font cache lives — and it has to survive the launch.

    From source that is ``.mplcache`` at the repository root, which is stable
    across launches and easy to pre-warm during provisioning. In a **frozen**
    build it cannot be: PyInstaller unpacks the bundle into ``sys._MEIPASS``, a
    fresh temp tree per launch, and ``_ROOT`` points inside it — so the
    "stable" cache was thrown away and rebuilt every single time the app
    started, which is exactly the cost this module exists to remove. A frozen
    build uses the per-user state directory instead, the same one the boot log
    goes to.
    """
    if getattr(_sys, "frozen", False) or hasattr(_sys, "_MEIPASS"):
        from startup.boot_diag import user_state_dir
        return os.path.join(str(user_state_dir()), "mplcache")
    return os.path.join(_ROOT, ".mplcache")


def _set_cachedir() -> None:
    """Pin MPLCONFIGDIR to a stable local dir so the cache isn't rebuilt every
    session or written to slow/roaming storage. Respects a caller-set value."""
    if os.environ.get("MPLCONFIGDIR"):
        return
    try:
        cache = _cache_root()
        os.makedirs(cache, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = cache
    except (OSError, ImportError):
        pass  # fall back to matplotlib's default cachedir


def configure() -> None:
    """Pin matplotlib's cache without importing its eager font manager."""
    _set_cachedir()
