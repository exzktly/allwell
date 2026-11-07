"""Removing a folder on this machine only when it is inside the folder it is meant to be in.

The local half of the delete layer (``services.remote_delete`` is the remote one). ``shutil.rmtree`` removes whatever
path it is handed, and a path built from a setting, a record or a user's pick can resolve -- through ``..``, a symlink
or a Windows junction -- somewhere else. :func:`safe_rmtree` resolves both the folder and the root it must be inside,
and refuses unless the folder is strictly inside the root, is not itself a link or junction, and the root is neither a
filesystem root nor the user's home folder.

Qt-free, stdlib only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class DeleteRefused(ValueError):
    """A folder :func:`safe_rmtree` would not remove, and why."""


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows a junction or other reparse point (``os.path.islink`` misses a junction)."""
    if os.path.islink(path):
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x400)          # FILE_ATTRIBUTE_REPARSE_POINT


def refusal(path, inside) -> str:
    """Why *path* must not be removed as a folder inside *inside*, or ``""``."""
    root = Path(os.path.realpath(inside))
    if root == Path(root.anchor) or root == Path(os.path.realpath(Path.home())):
        return f"refused to remove anything under {inside}: it is a filesystem root or the home folder"
    p = Path(path)
    if _is_link(p):
        return f"refused to remove {path}: it is a link, not a folder of its own"
    target = Path(os.path.realpath(p))
    if target == root or root not in target.parents:
        return f"refused to remove {path}: it is not inside {inside}"
    return ""


def safe_rmtree(path, *, inside) -> None:
    """Remove the folder *path* if it is strictly inside *inside* (see :func:`refusal`); raise :class:`DeleteRefused`
    otherwise. A *path* that does not exist is a no-op, as there is nothing to remove."""
    if not os.path.lexists(path):
        return
    why = refusal(path, inside)
    if why:
        raise DeleteRefused(why)
    shutil.rmtree(os.path.realpath(path))
