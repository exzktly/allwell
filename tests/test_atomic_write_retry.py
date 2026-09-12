"""Regression tests for the atomic-write retry-on-PermissionError loop.

User report: a Windows traceback inside `atomic_write_text` mid
Apply-Global-Threshold — `[WinError 5] Access is denied` on the temp
CSV. Root cause is the classic Windows file-handle race: between the
moment the writer creates the tmp file and the moment it does
`os.replace`, an antivirus scanner (or OneDrive/Dropbox/Excel viewer)
briefly holds a handle on the file, and the OS rejects the access.
The same failure class can hit the JSON sidecar writer too.

The fix is twofold:

1. **Uniquified tmp filename** — `<path>.<pid>.<rand>.tmp` instead of a
   fixed `<path>.tmp`, so two concurrent writers (different processes,
   or a leftover from a previous crash) never collide.

2. **Retry with exponential backoff on `PermissionError`** — both the
   initial `open(tmp, "w")` and the final `os.replace(tmp, path)`.
   Five attempts at 100/200/400/800/1600 ms — long enough to outlast
   a normal AV scan, short enough that a real lock (file genuinely
   held open by Excel) still surfaces an error promptly.

These tests use `unittest.mock` to inject `PermissionError` on a
configurable number of attempts and verify the retry loop absorbs
them. No Windows-only behaviour is exercised — the retry contract is
OS-agnostic, so the tests run uniformly on Linux/macOS/Windows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from review.persistence import _io as _atomic_io
from review.persistence._io import atomic_write_json, atomic_write_text


def test_atomic_write_text_succeeds_on_happy_path(tmp_path):
    path = tmp_path / "well.csv"
    atomic_write_text(path, "header\n1\n2\n")
    assert path.read_text() == "header\n1\n2\n"


def test_atomic_write_json_succeeds_on_happy_path(tmp_path):
    path = tmp_path / "sidecar.json"
    atomic_write_json(path, {"key": "value"})
    assert json.loads(path.read_text()) == {"key": "value"}


def test_atomic_write_text_tmp_file_is_uniquified(tmp_path):
    """The tmp filename must include pid + random segment so concurrent
    writers (different processes, retry-cleanup races) don't collide.
    Previously it was a fixed ``<path>.tmp`` — two processes writing the
    same well at the same time would clobber each other's tmp."""
    path = tmp_path / "well.csv"
    tmp = _atomic_io._unique_tmp_path(path)
    # Same path → different tmps because of uuid.
    tmp2 = _atomic_io._unique_tmp_path(path)
    assert tmp != tmp2, "tmp filenames must be unique per call"
    # And the tmp filename must end with .tmp so the cleanup loop / log
    # filters can still recognise it.
    assert str(tmp).endswith(".tmp")
    # And it must encode the pid so cross-process collisions are
    # impossible (different pids → different segments).
    assert f".{os.getpid()}." in str(tmp)


def test_replace_retries_on_transient_permission_error(tmp_path, monkeypatch):
    """Simulates the AV-scanner race: ``os.replace`` raises PermissionError
    on the first attempt, then succeeds. The retry loop must absorb the
    error transparently."""
    path = tmp_path / "well.csv"
    real_replace = os.replace
    attempts = {"n": 0}

    def flaky_replace(src, dst):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(src, dst)

    # Speed up the test — don't actually sleep 100ms between attempts.
    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    with patch.object(os, "replace", side_effect=flaky_replace):
        atomic_write_text(path, "payload\n")

    assert attempts["n"] == 2  # one failure then success
    assert path.read_text() == "payload\n"


def test_replace_retries_exhaust_then_propagate(tmp_path, monkeypatch):
    """If ``PermissionError`` persists for the entire retry budget, the
    helper must propagate the final exception — callers (e.g. the
    smFISH writer) get to log it and surface a message, instead of
    silently writing nothing."""
    path = tmp_path / "well.csv"

    def always_fails(src, dst):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    with patch.object(os, "replace", side_effect=always_fails):
        with pytest.raises(PermissionError):
            atomic_write_text(path, "payload\n")


def test_write_tmp_retries_on_permission_error(tmp_path, monkeypatch):
    """The same retry shield wraps the initial ``open(tmp, 'w')`` call —
    AV scanners can also lock the tmp filename (e.g. if it was created
    and quarantined by a previous run)."""
    path = tmp_path / "well.csv"
    real_open = _atomic_io.open if hasattr(_atomic_io, "open") else open
    attempts = {"n": 0}

    def flaky_open(file, mode="r", **kwargs):
        # Only intercept writes to .tmp files; reads / non-tmp writes
        # pass through.
        if "w" in str(mode) and str(file).endswith(".tmp"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise PermissionError("[WinError 5] Access is denied")
        return real_open(file, mode, **kwargs)

    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    # `open` is a builtin reference inside the _io module; patch the
    # builtin instead of the module attribute.
    with patch("builtins.open", side_effect=flaky_open):
        atomic_write_text(path, "payload\n")

    assert attempts["n"] == 2
    assert path.read_text() == "payload\n"


def test_atomic_write_json_inherits_retry_shield(tmp_path, monkeypatch):
    """Same retry contract applies to atomic_write_json — same failure
    class hits both writers."""
    path = tmp_path / "sidecar.json"
    real_replace = os.replace
    attempts = {"n": 0}

    def flaky_replace(src, dst):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    with patch.object(os, "replace", side_effect=flaky_replace):
        atomic_write_json(path, {"a": 1})

    assert attempts["n"] == 2
    assert json.loads(path.read_text()) == {"a": 1}


def test_cleanup_unlink_never_propagates(tmp_path, monkeypatch):
    """If the post-replace tmp cleanup fails (e.g. AV is still scanning
    the tmp), the helper must NOT surface that error — the primary
    write already succeeded, the leftover tmp is cosmetic. Previously
    a leftover tmp wasn't even possible because replace consumed it;
    now with retry loops a partial-failure path can leave one behind."""
    path = tmp_path / "well.csv"
    # Force the write_tmp step to fail on the FIRST replace attempt so
    # the finally arm runs against a leftover tmp; then make unlink
    # fail too. The whole call must still raise the original error
    # from the replace attempt, not from unlink.
    def always_fails_replace(src, dst):
        raise PermissionError("[WinError 5] Access is denied")

    real_unlink = Path.unlink
    unlink_calls = {"n": 0}

    def flaky_unlink(self, *args, **kwargs):
        unlink_calls["n"] += 1
        raise PermissionError("[WinError 5] unlink denied")

    monkeypatch.setattr(_atomic_io, "_RETRY_BASE_DELAY_S", 0.0)
    with patch.object(os, "replace", side_effect=always_fails_replace), \
         patch.object(Path, "unlink", flaky_unlink):
        # Must raise the replace error, not the unlink error.
        with pytest.raises(PermissionError, match="Access is denied"):
            atomic_write_text(path, "payload\n")

    # The cleanup attempt happened (so the finally arm ran) but its
    # error was swallowed.
    assert unlink_calls["n"] > 0
