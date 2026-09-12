"""Stop matplotlib enumerating every system font.

AllWell renders every plot with its own bundled fonts (``assets/fonts`` — Inter
/ JetBrains Mono) plus matplotlib's built-in DejaVu fallback. It never uses a
system font. But matplotlib, on its first cache build, parses *every* font file
installed on the machine to build ``fontlist-v*.json`` — hundreds of files on a
Windows box, and on a Windows 365 Cloud PC (networked/roaming storage) that scan
can take minutes on first launch.

``configure()`` restricts matplotlib's font discovery to the bundled fonts and
pins the cache to a stable local directory, so the system-font enumeration never
happens. Call it **once, before matplotlib is first imported** (from
``allwell.main``). It is defensive: any failure leaves matplotlib's default
behaviour untouched rather than breaking startup.

Note: on a *cold* cache the very first ``import matplotlib.font_manager`` still
builds once; after that the limited cache persists (in the repo's ``.mplcache``
when running from source, in the per-user state directory in a frozen build —
``sys._MEIPASS`` is a fresh temp tree per launch and cannot hold a cache)
and every launch skips the scan. To remove even the first-build cost, pre-warm
the cache during environment provisioning by launching the app once.
"""

from __future__ import annotations

import glob
import os
import sys as _sys

# Both paths below are resolved against the *repository root* (the bundle root
# in a frozen build), not against this file's own directory: `assets/fonts` and
# `.mplcache` are repository-level, and this module lives one level down in
# `startup/`. Deriving them from `__file__` alone would put the font directory
# at `startup/assets/fonts` — which does not exist, so `_bundled_fonts()` would
# return nothing and `configure()` would silently leave matplotlib unrestricted,
# the exact cost this module exists to remove.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FONTS_DIR = os.path.join(_ROOT, "assets", "fonts")


def _bundled_fonts() -> list[str]:
    out: list[str] = []
    for ext in ("*.ttf", "*.otf", "*.ttc"):
        out += glob.glob(os.path.join(_FONTS_DIR, ext))
    return out


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


def _under(path: str, roots: list[str]) -> bool:
    if not path:
        return False
    p = os.path.normcase(os.path.abspath(path))
    return any(p.startswith(os.path.normcase(os.path.abspath(r))) for r in roots)


def configure() -> None:
    """Restrict matplotlib to the bundled (+ DejaVu) fonts. Call before any
    matplotlib import; safe to call more than once."""
    _set_cachedir()
    bundled = _bundled_fonts()
    if not bundled:
        return  # nothing bundled → leave matplotlib alone
    try:
        import matplotlib
        import matplotlib.font_manager as fm
    except Exception:
        return

    # Suppress only the system-wide font enumeration — the expensive call
    # matplotlib makes with no explicit paths. Calls that pass ``fontpaths``
    # (matplotlib loading its own mpl-data fonts — DejaVu, the default fallback)
    # are honoured, so text still renders. Idempotent: don't double-wrap.
    if not getattr(fm.findSystemFonts, "_allwell_limited", False):
        _orig_find = fm.findSystemFonts

        def _limited_find(fontpaths=None, fontext="ttf"):
            if fontpaths is not None:
                return _orig_find(fontpaths, fontext)   # explicit dirs (mpl-data)
            return list(bundled)                          # system-wide → bundled only

        _limited_find._allwell_limited = True
        fm.findSystemFonts = _limited_find

    try:
        allowed = [_FONTS_DIR, os.path.join(matplotlib.get_data_path(), "fonts")]
        mgr = getattr(fm, "fontManager", None)
        ttf = list(getattr(mgr, "ttflist", []) or [])
        # Rebuild (limited) when the loaded cache is empty or still carries any
        # system font — otherwise the existing limited cache is reused as-is.
        if (not ttf) or any(not _under(getattr(e, "fname", ""), allowed) for e in ttf):
            fm.fontManager = fm.FontManager()
            from pathlib import Path
            cache_file = Path(matplotlib.get_cachedir(),
                              f"fontlist-v{fm.FontManager.__version__}.json")
            fm.json_dump(fm.fontManager, cache_file)
    except Exception:
        pass  # a functioning (if unlimited) font manager beats a crash
