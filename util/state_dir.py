"""The one per-user folder All-Well keeps its local working files in.

The startup and crash log (``startup.boot_diag``), the remote-bootstrap logs
(``services.remote_bootstrap``), matplotlib's font cache in a frozen build
(``startup.mpl_bootstrap``) and the CZI scan cache (``pipeline.scan_cache``) all
live here. They used to pick their folder with four separate rules: the boot log
went to ``%LOCALAPPDATA%\\allwell`` on Windows while the bootstrap logs went to
``~/.cache/allwell`` everywhere, so on Windows a user directed to the folder
holding the bootstrap logs found no crash log there. Every one of them now asks
this function, so they can no longer disagree.

``~/.cache/allwell`` comes first on every platform, because that is the folder
the documentation names and where people look. ``%LOCALAPPDATA%\\allwell`` (when
that variable is set) and then the system temp directory are fallbacks for a
home folder that cannot be written.

Stdlib-only and Qt-free: ``startup.boot_diag`` imports it before PySide6, and
``util/`` is shipped to the remote worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def candidate_dirs() -> list[Path]:
    """The folders :func:`user_state_dir` tries, in order."""
    out = [Path(os.path.expanduser("~/.cache")) / "allwell"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "allwell")
    out.append(Path(tempfile.gettempdir()) / "allwell")
    return out


def user_state_dir() -> Path:
    """The first folder of :func:`candidate_dirs` that exists or can be made,
    *and* can be written; the system temp directory when none can.

    ``mkdir(exist_ok=True)`` succeeds on a folder that exists but cannot be
    written (one left owned by another user, say), so writability is checked
    separately — otherwise the log's ``open`` fails later, silently, and the
    fallbacks are never reached.
    """
    for d in candidate_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(d, os.W_OK):
            return d
    return Path(tempfile.gettempdir())
