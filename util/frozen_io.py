"""Repair ``sys.stdout`` / ``sys.stderr`` when a frozen windowed build nulls them.

Both ``process_microscopy`` and ``well_plate_zipper`` run as subprocesses the
Analyze tab spawns with ``subprocess.Popen([sys.executable, "--run-pipeline" /
"--run-zipper", ...], stdout=PIPE, stderr=STDOUT)``. The parent attaches a pipe
to the child's fd 1 and dups fd 2 onto it, then reads that pipe line by line to
drive the GUI's log drawer and progress chips.

In source mode fds 1 / 2 carry valid Python text wrappers, so everything the
child ``print()``s (or logs) reaches the parent. In a frozen **windowed**
(``console=False``) PyInstaller build the bootloader detaches Python from the
absent terminal and leaves ``sys.stdout = None`` / ``sys.stderr = None`` even
though the underlying descriptors are perfectly fine. ``print`` to a ``None``
stream is a silent no-op (CPython drops it without error), so the child produces
no output at all: the parent's pipe stays empty until the process exits, the
grouping / processing progress freezes, and any diagnostic warning is lost.

:func:`ensure_std_streams` restores a working text wrapper around fd 1 / fd 2 so
both entry points' output reaches the parent again. It is deliberately Qt-free
and stdlib-only so the lightweight ``well_plate_zipper`` subprocess can call it
without importing the heavy pipeline stack.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from io import TextIOWrapper

import io
import os
import sys


def _fd_ok(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def _wrap_fd(fd: int) -> TextIOWrapper | None:
    # ``closefd=False`` is critical — the wrapper may outlive this call (a
    # logging StreamHandler holds it), but it must not close the parent's pipe
    # end when GC eventually drops it.
    try:
        raw = os.fdopen(fd, "wb", buffering=0, closefd=False)
        return io.TextIOWrapper(
            raw, encoding="utf-8", errors="replace",
            line_buffering=True, write_through=True,
        )
    except OSError:
        return None


def _probe(stream) -> bool:
    if stream is None:
        return False
    try:
        stream.write("")
        stream.flush()
        return True
    except Exception:  # broken streams raise anything
        return False


def ensure_std_streams() -> None:
    """Restore writable ``sys.stdout`` / ``sys.stderr`` if a frozen build nulled
    (or broke) them. A no-op when both streams are already writable."""
    if not _probe(sys.stdout):
        wrapper = _wrap_fd(1) if _fd_ok(1) else None
        if wrapper is not None:
            sys.stdout = wrapper
    if not _probe(sys.stderr):
        # Prefer fd 2 if it's live; otherwise alias to sys.stdout so callers
        # still have somewhere to write. The parent spawns us with
        # ``stderr=STDOUT``, so the two streams merge downstream anyway.
        wrapper = _wrap_fd(2) if _fd_ok(2) else None
        if wrapper is None:
            wrapper = sys.stdout
        sys.stderr = wrapper
