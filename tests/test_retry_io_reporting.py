"""What ``retry_io`` says, and cleans up, when the budget runs out.

Three defects in one helper.

The exhausted-budget failure was raised as ``OSError(str)``, whose ``.errno``
is None — so every caller that branches on the errno stopped being able to at
exactly the point the failure became permanent. The ENOSPC rewrite a dozen
lines above in the same function is itself such a caller.

Both messages said "write" unconditionally, and this helper serves the read
call sites too (``pipeline.io`` wraps it for both). A failed read reported as
a failed write sends the reader looking at disk space and permissions on the
wrong end of the operation.

``cleanup`` is documented as running after every failed attempt, and it exists
so a partial staging file is never left behind. It ran only for ``OSError``, so
a ``BadZipFile``, a ``MemoryError`` or a ``KeyboardInterrupt`` mid-write left
the half-written file on disk under its real name.
"""

from __future__ import annotations

import errno

import pytest

from util.retry_io import retry_io  # noqa: E402


def _raiser(exc):
    def _fn():
        raise exc
    return _fn


def test_the_exhausted_failure_keeps_the_errno():
    with pytest.raises(OSError) as ei:
        retry_io(_raiser(OSError(errno.EIO, "io error")),
                 what="the archive", attempts=2, base_delay=0)
    assert ei.value.errno == errno.EIO
    assert isinstance(ei.value.__cause__, OSError)


def test_a_read_call_site_is_reported_as_a_read():
    with pytest.raises(OSError) as ei:
        retry_io(_raiser(OSError(errno.EIO, "io error")),
                 what="the archive", attempts=2, base_delay=0, action="read")
    assert "Failed to read the archive" in str(ei.value)


def test_write_is_still_the_default_wording():
    with pytest.raises(OSError) as ei:
        retry_io(_raiser(OSError(errno.EIO, "io error")),
                 what="the archive", attempts=2, base_delay=0)
    assert "Failed to write the archive" in str(ei.value)


def test_cleanup_runs_for_a_failure_that_is_not_an_oserror():
    ran: list[int] = []

    class _BadZip(Exception):
        pass

    with pytest.raises(_BadZip):
        retry_io(_raiser(_BadZip("truncated")), what="the archive",
                 cleanup=lambda: ran.append(1), attempts=3, base_delay=0)
    assert ran == [1], (
        "the staging file is left on disk when the failure is not an OSError"
    )


def test_cleanup_still_runs_on_every_oserror_attempt():
    ran: list[int] = []
    with pytest.raises(OSError):
        retry_io(_raiser(OSError(errno.EIO, "io error")), what="the archive",
                 cleanup=lambda: ran.append(1), attempts=3, base_delay=0)
    assert len(ran) == 3


def test_a_permanent_errno_is_re_raised_untouched():
    original = OSError(errno.EACCES, "permission denied")
    with pytest.raises(OSError) as ei:
        retry_io(_raiser(original), what="the archive", attempts=3, base_delay=0)
    assert ei.value is original


def test_a_full_volume_still_gets_the_rewritten_message(tmp_path):
    with pytest.raises(OSError) as ei:
        retry_io(_raiser(OSError(errno.ENOSPC, "no space")),
                 what="the archive", attempts=3, base_delay=0, path=tmp_path)
    assert ei.value.errno == errno.ENOSPC


def test_success_does_not_run_cleanup():
    ran: list[int] = []
    assert retry_io(lambda: "ok", what="the archive",
                    cleanup=lambda: ran.append(1), attempts=3, base_delay=0) == "ok"
    assert ran == []
