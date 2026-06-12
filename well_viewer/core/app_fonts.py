"""Bundled application fonts (Inter UI + JetBrains Mono numeric).

The app previously inherited each platform's default UI face
(``QApplication.font().family()`` — Segoe UI / SF / DejaVu), so it looked
different on every OS and the weakest on Linux. Bundling the fonts makes the
chrome identical everywhere and lets the matplotlib plots match the UI (which
also removes the legacy ``Font family 'Helvetica' not found`` fallback).

Both faces are SIL OFL licensed (see ``assets/fonts/*-OFL.txt``), so they can be
redistributed with the app. Inter and JetBrains Mono ship as variable fonts —
Qt registers the family and resolves weights from the ``wght`` axis.
"""
from __future__ import annotations

import sys
from pathlib import Path

UI_FONT_FILE = "Inter.ttf"
MONO_FONT_FILE = "JetBrainsMono.ttf"


def fonts_dir() -> Path:
    """Locate ``assets/fonts`` both in-repo and inside a PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "assets" / "fonts"


def register_qt_fonts() -> tuple[str, str]:
    """Register the bundled fonts with Qt's font database.

    Returns ``(ui_family, mono_family)`` — the registered family names, or an
    empty string for any file that is missing / failed to load so the caller
    can fall back to the platform default.
    """
    from PySide6.QtGui import QFontDatabase

    d = fonts_dir()
    families = {"ui": "", "mono": ""}
    for fname, key in ((UI_FONT_FILE, "ui"), (MONO_FONT_FILE, "mono")):
        fpath = d / fname
        if not fpath.exists():
            continue
        fid = QFontDatabase.addApplicationFont(str(fpath))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            families[key] = fams[0]
    return families["ui"], families["mono"]


def register_mpl_ui_font() -> str:
    """Register the bundled UI font with matplotlib's font manager.

    Returns the family name (``"Inter"``) on success, else ``""``. Called
    lazily from the plot-styling layer so matplotlib stays out of the startup
    path. Idempotent-friendly: matplotlib dedupes repeated ``addfont`` calls.
    """
    fpath = fonts_dir() / UI_FONT_FILE
    if not fpath.exists():
        return ""
    try:
        from matplotlib import font_manager as _fm

        _fm.fontManager.addfont(str(fpath))
        from matplotlib.font_manager import FontProperties

        return FontProperties(fname=str(fpath)).get_name()
    except Exception:
        return ""
