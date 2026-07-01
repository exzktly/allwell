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
builds once; after that the limited cache persists (in the repo's ``.mplcache``)
and every launch skips the scan. To remove even the first-build cost, pre-warm
the cache during environment provisioning by launching the app once.
"""

from __future__ import annotations

import glob
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FONTS_DIR = os.path.join(_HERE, "assets", "fonts")


def _bundled_fonts() -> list[str]:
    out: list[str] = []
    for ext in ("*.ttf", "*.otf", "*.ttc"):
        out += glob.glob(os.path.join(_FONTS_DIR, ext))
    return out


def _set_cachedir() -> None:
    """Pin MPLCONFIGDIR to a stable local dir so the cache isn't rebuilt every
    session or written to slow/roaming storage. Respects a caller-set value."""
    if os.environ.get("MPLCONFIGDIR"):
        return
    cache = os.path.join(_HERE, ".mplcache")
    try:
        os.makedirs(cache, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = cache
    except OSError:
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

    # Never enumerate system fonts again — only the bundled set. matplotlib adds
    # its own mpl-data fonts (DejaVu, the default fallback) separately, so text
    # still renders even if a bundled family is missing a glyph.
    fm.findSystemFonts = lambda *a, **k: list(bundled)

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
