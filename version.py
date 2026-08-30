"""Single source of truth for the All-Well application version.

Read by the GUI (the title-bar version pill in ``allwell.py``) and by the
PyInstaller build (``_Docs/_Installation/all_well.spec`` uses it for the
macOS bundle ``version`` / ``CFBundleShortVersionString`` / ``CFBundleVersion``).
Bump this one string on a release — nothing else carries the app version.

Kept as a plain module-level constant (not read from ``pyproject.toml`` /
``importlib.metadata``) so it resolves identically when run from source and
inside a frozen PyInstaller bundle, where package metadata is not available.
"""

from __future__ import annotations

__version__ = "9.1.4"
