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

**The restriction has to be in place before ``matplotlib.font_manager`` is
imported, and a patch cannot be.** The module builds its global ``FontManager``
as the last statement of its own import: it loads ``fontlist-v<N>.json`` from the
cache directory and, when there is none, constructs one — which is the full
system-font scan. A ``findSystemFonts`` replaced after the import therefore came
too late on every cold cache: the first launch, a fresh per-user state
directory, and every matplotlib upgrade (``<N>`` changes with the version, so
the old file no longer counts). So on a cold cache — no list, or one that is
truncated, empty or for another version — ``configure()`` points the import at a
placeholder font list in a private temporary directory: an empty
``FontManager`` that the import loads instead of scanning. It then patches
discovery, builds the real bundled-only list and writes it into the shared cache
atomically. The placeholder never enters the shared cache, because any other
process that imports matplotlib against that directory without ``configure()``
would accept an empty list as valid and have no fonts. ``<N>`` is read from the
module's code object, not its source, because a frozen build carries no ``.py``
files; if it cannot be read, no placeholder is used and the import scans as
matplotlib would on its own.

The cache lives in the repo's ``.mplcache`` when running from source, and in the
per-user state directory in a frozen build — ``sys._MEIPASS`` is a fresh temp
tree per launch and cannot hold a cache.
"""

from __future__ import annotations

import dis
import glob
import json
import os
import shutil
import sys as _sys
import tempfile
import types
from contextlib import contextmanager, nullcontext

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
    if _frozen():
        from startup.boot_diag import user_state_dir
        return os.path.join(str(user_state_dir()), "mplcache")
    return os.path.join(_ROOT, ".mplcache")


def _frozen() -> bool:
    return bool(getattr(_sys, "frozen", False) or hasattr(_sys, "_MEIPASS"))


def _set_cachedir() -> None:
    """Pin MPLCONFIGDIR to a stable local dir so the cache isn't rebuilt every
    session or written to slow/roaming storage.

    From source a caller-set value is respected. A frozen build overrides it:
    PyInstaller's matplotlib runtime hook (``pyi_rth_mplconfig``) sets
    ``MPLCONFIGDIR`` to a fresh temporary directory on every launch, before any
    of the app's code runs, and deletes it at exit — so no value a user set can
    survive to here, and deferring to it made every frozen launch a cold cache.
    The hook exists because a onefile bundle unpacks to a new path each launch
    and a persisted list would name fonts that are gone; this build is onedir,
    and ``configure()`` rebuilds any list naming a font outside the current
    bundle's font directories, so a persisted list cannot point at a stale path.
    """
    if os.environ.get("MPLCONFIGDIR") and not _frozen():
        return
    try:
        cache = _cache_root()
        os.makedirs(cache, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = cache
    except (OSError, ImportError):
        pass  # fall back to matplotlib's default cachedir


def _font_list_version() -> int | None:
    """``FontManager.__version__``, without importing ``matplotlib.font_manager``.

    Importing the module is the scan this module exists to avoid, so the number is
    read from the module's compiled code: the class body of ``FontManager`` stores
    an integer constant under ``__version__``. ``get_code`` works for a source
    install and for PyInstaller's frozen importer alike, and executes nothing.
    ``None`` when it cannot be read; the caller then leaves the import alone.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("matplotlib.font_manager")
        code = spec.loader.get_code(spec.name)
    except (ImportError, AttributeError, OSError, SyntaxError, ValueError):
        return None
    if code is None:
        return None
    for const in code.co_consts:
        if isinstance(const, types.CodeType) and const.co_name == "FontManager":
            ins = list(dis.get_instructions(const))
            for load, store in zip(ins, ins[1:]):
                if (store.opname == "STORE_NAME" and store.argval == "__version__"
                        and load.opname == "LOAD_CONST"
                        and isinstance(load.argval, int)):
                    return load.argval
    return None


def _usable_font_list(path: str, version: int) -> bool:
    """Whether *path* holds a font list the import would load and keep.

    A missing file, one that does not parse (matplotlib writes its own lists
    non-atomically, so a crash can truncate one), one written for another
    matplotlib, and an empty one all send the import to the full system scan, or
    leave it with no fonts at all. Each counts as a cold cache here.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict) and data.get("_version") == version
            and bool(data.get("ttflist")))


@contextmanager
def _import_reads_a_placeholder(matplotlib, version: int):
    """While active, the font-manager import loads an empty list and scans nothing.

    The placeholder lives in a private temporary directory and
    ``matplotlib.get_cachedir`` points there only for the duration, which is the
    one lookup ``font_manager``'s import makes. It never sits in the shared
    cache directory, because a process that imports matplotlib there without
    ``configure()`` — a child that inherits ``MPLCONFIGDIR``, a script run from
    source — would accept an empty list as a valid cache and have no fonts.
    """
    placeholder = {"_version": version, "ttflist": [], "afmlist": [],
                   "defaultFamily": {"ttf": "DejaVu Sans", "afm": "Helvetica"},
                   "default_size": None, "__class__": "FontManager"}
    real_cachedir = matplotlib.get_cachedir
    tmp = None
    try:
        try:
            tmp = tempfile.mkdtemp(prefix="allwell-mplfonts-")
            from util.json_write import write_json_atomic
            write_json_atomic(os.path.join(tmp, f"fontlist-v{version}.json"),
                              placeholder, durable=False)
            matplotlib.get_cachedir = lambda: tmp
        except OSError:
            pass  # no placeholder: the import scans, as matplotlib would on its own
        yield
    finally:
        matplotlib.get_cachedir = real_cachedir
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


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
    except ImportError:
        return
    importing = nullcontext()
    if "matplotlib.font_manager" not in _sys.modules:
        version = _font_list_version()
        if version is not None and not _usable_font_list(
                os.path.join(matplotlib.get_cachedir(), f"fontlist-v{version}.json"),
                version):
            importing = _import_reads_a_placeholder(matplotlib, version)
    try:
        with importing:
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
        _limited_find._allwell_original = _orig_find
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
            # matplotlib's json_dump writes in place; a crash mid-write would
            # leave a truncated list. Write beside it and rename over it.
            staged = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
            fm.json_dump(fm.fontManager, staged)
            try:
                os.replace(staged, cache_file)
            except OSError:
                pass  # json_dump already logged why it could not write
    except Exception:
        # A functioning (if unlimited) font manager beats a crash — and beats the
        # empty placeholder, which would leave text with no font to resolve.
        if not list(getattr(getattr(fm, "fontManager", None), "ttflist", []) or []):
            fm.findSystemFonts = getattr(fm.findSystemFonts, "_allwell_original",
                                         fm.findSystemFonts)
            fm.fontManager = fm._load_fontmanager(try_read_cache=False)
    # The import bound these two module functions to whichever manager it
    # loaded, so replacing ``fontManager`` alone leaves ``findfont`` answering
    # from the old one — the placeholder, or the unrestricted cold-cache build.
    fm.findfont = fm.fontManager.findfont
    fm.get_font_names = fm.fontManager.get_font_names
