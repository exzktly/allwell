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
``pipeline/``, ``services/``, ``review/`` and ``process_microscopy`` all
import from here, and it pulls in nothing but the standard library and
``util.aw_safe``.

One specialised version remains on purpose, and it is not a copy of this rule:
``review.persistence._io.atomic_write_json`` wraps *both* the open and the
replace in a retry for Windows ``PermissionError`` (an antivirus scanner or a
cloud-sync client holding the handle), which is a different control flow rather
than an argument.

The bookkeeping records — the run journal in the output folder and the
suspended-run pointer in ``~/.allwell`` — need a return value rather than an
exception, because a run that cannot write its own bookkeeping must still
produce data. That is one line of difference, not a second rule, so it is
:func:`try_write_json_atomic` here rather than a copy in each of them. They had
a copy each once, and the copies diverged in both of the available directions:
one caught the ``TypeError`` a caller-supplied ``spec`` can raise and the other
did not, and one removed its staging file on failure and the other did not.
"""

from __future__ import annotations

import json
import os
import stat
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

    **The destination's permissions are preserved.** A truncating
    ``write_text`` keeps the mode of the file it opens; a replace brings the
    staging file's mode with it, and ``mkstemp`` creates at ``0600``. Silently
    swapping in an owner-only ``pipeline_info.json`` is how a plate on a shared
    lab volume stops being readable by the collaborator, or the remote worker,
    who could read it yesterday — and the ``<WELL>.zip`` files beside it would
    still be group-readable, so the folder would not even be consistently
    broken. When there is no destination yet the staging file is widened to the
    process umask, which is what ``open(path, "w")`` would have produced.

    The staging name carries the repo-wide ``__aw_tmp_`` prefix, so the sweeps
    that clean up after an interrupted run — ``datasets/validate/apply.
    _sweep_staging_files``, ``pipeline/io``'s own scans — can see a file left
    behind by a process that was killed mid-write. A staging file under a name
    nothing globs for is one nothing ever removes.

    ``durable=False`` skips the ``fsync``, which costs the bulk of a small
    write and, on a network share, is a visible pause. Everything a *reader*
    can observe is unchanged either way; only survival of a power loss is given
    up. Pass it for a debounced autosave on the GUI thread, whose value is
    rewritten within seconds anyway; leave it on for anything a run computed
    once.

    Raises ``OSError`` on a filesystem failure and ``TypeError`` /
    ``ValueError`` on a payload ``json.dump`` cannot serialise — both before
    the replace, so the destination is untouched. Callers decide whether to
    catch; a caller whose contract is that bookkeeping never fails a run should.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f"__aw_tmp_{path.name}.", suffix=".tmp")
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
        _carry_mode(path, tmp_name)
        os.replace(tmp_name, str(path))
        tmp_name = ""
    finally:
        # ``mkstemp`` created a file; a failure after that point would leave it
        # behind, and the ``__aw_tmp_`` prefix means a sweep can find it even
        # if this process is killed before reaching here.
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                swallowed(f"write_json_atomic: could not remove {tmp_name}")


def _carry_mode(dest: Path, staging: str) -> None:
    """Give *staging* the mode *dest* has, or the umask default if it is new."""
    try:
        mode = stat.S_IMODE(dest.stat().st_mode)
    except OSError:
        # No destination yet: match what ``open(dest, "w")`` would have made.
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    try:
        os.chmod(staging, mode)
    except OSError:
        # Some filesystems (a few network mounts, and Windows for the group and
        # other bits) refuse chmod. The write itself is still correct.
        swallowed(f"write_json_atomic: could not set the mode on {staging}")


def try_write_json_atomic(path: Path | str, data: Any, *,
                          what: str = "record", indent: int = 2,
                          durable: bool = True) -> bool:
    """:func:`write_json_atomic`, answering ``False`` instead of raising.

    For the two records that describe work already done — the run journal and
    the suspended-run pointer. Neither is the user's data: the outputs are the
    authoritative account of what a run produced, so a failed write must cost a
    log line and nothing more.

    *what* names the record in that log line, so a reader can tell a journal
    write from a suspend-record write without a traceback.

    The exception list is not defensive breadth. ``OSError`` is the filesystem;
    ``TypeError`` and ``ValueError`` are ``json.dump`` refusing a payload, which
    is a live case here rather than a hypothetical one — both records carry a
    ``spec`` handed in by the caller, and one unserialisable value in it raises
    from the serialiser with the disk never involved. The copy in
    ``suspend_record`` caught ``OSError`` alone, so such a spec raised out of a
    function whose whole contract is a boolean, past ``_suspend``'s outer
    handler and *before* ``PipelineRunner.suspend()`` was reached: the Suspend
    button did nothing at all and said nothing about it.
    """
    try:
        write_json_atomic(path, data, indent=indent, durable=durable)
        return True
    except (OSError, TypeError, ValueError):
        swallowed(f"could not write the {what} at {path}")
        return False
