"""One atomic JSON write, for every layer that has to replace a live file.

Replacing a file the app or the pipeline will read again is the operation this
codebase gets wrong in the same way each time: ``path.write_text(json.dumps(…))``
truncates the destination the instant it opens, so a crash, a full disk or a
kill between that moment and the last byte leaves a file that is neither the old
content nor the new one. For ``pipeline_info.json`` that is the user's gating,
sample definitions, ratios and notes gone at once — the blocks the tools that
rewrite it exist to preserve.

Versions of the fix had accumulated in four places — ``persistence/_io``,
``services/run_journal``, ``pipeline/io``, and a tmp + ``replace`` pair written
out longhand in six more modules — while six other sites did not have it at all.
Sitting in ``util`` puts it where every layer can reach it: ``datasets/``,
``pipeline/``, ``services/``, ``well_viewer/`` and ``process_microscopy`` all
import from here, and it pulls in nothing but the standard library and
``util.aw_safe``.

Two specialised versions remain on purpose, and neither is a copy of this rule.
``well_viewer.persistence._io.atomic_write_json`` wraps *both* the open and the
replace in a retry for Windows ``PermissionError`` (an antivirus scanner or a
cloud-sync client holding the handle), which is a different control flow rather
than an argument. ``services.run_journal._atomic_write_json`` returns ``False``
instead of raising, because a run that cannot write its own bookkeeping must
still produce data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from util.aw_safe import swallowed


def write_json_atomic(path: Path | str, data: Any, *, indent: int = 2,
                      durable: bool = True) -> None:
    """Serialise *data* to *path* as JSON, atomically.

    Writes a staging file in the destination's own directory — the same
    filesystem, so the ``os.replace`` that follows is a rename rather than a
    copy — then replaces the destination with it. A reader never sees a partial
    file, and a failure anywhere before the replace leaves the destination
    exactly as it was.

    ``durable=False`` skips the ``fsync``, which costs the bulk of a small
    write. Everything a *reader* can observe is unchanged either way; only
    survival of a power loss is given up. Leave it on for anything holding a
    decision the user made.

    Raises ``OSError`` on a filesystem failure and ``TypeError`` / ``ValueError``
    on a payload ``json.dump`` cannot serialise — both before the replace, so
    the destination is untouched. Callers decide whether to catch; a caller
    whose contract is that bookkeeping never fails a run should.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent)
            fh.flush()
            if durable:
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # Not supported on every filesystem (some network mounts).
                    # The replace below is still atomic on POSIX and Windows.
                    swallowed("write_json_atomic: fsync unsupported on this fs")
        os.replace(tmp_name, str(path))
        tmp_name = ""
    finally:
        # ``mkstemp`` created a file; every failure after that point would leave
        # it behind, and these run per-well on some paths.
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                swallowed(f"write_json_atomic: could not remove {tmp_name}")
