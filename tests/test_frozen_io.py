"""``util.frozen_io.ensure_std_streams`` rebinds nulled stdio in a frozen build.

A frozen windowed (``console=False``) PyInstaller build leaves ``sys.stdout`` /
``sys.stderr`` set to ``None`` in the pipeline / zipper subprocess even though
the parent piped fd 1 / fd 2. Without a repair every ``print`` is a silent
no-op, so the grouping/processing output the GUI parses never arrives. These
tests simulate that state (streams ``None``, descriptors live) and assert the
streams are restored onto the live descriptors.
"""

import os
import sys

from util.frozen_io import ensure_std_streams


def test_rebinds_nulled_streams_onto_live_descriptors(tmp_path):
    capture = tmp_path / "piped.txt"
    saved_out, saved_err = sys.stdout, sys.stderr
    saved_fd1, saved_fd2 = os.dup(1), os.dup(2)
    try:
        with open(capture, "wb") as f:
            # Point fd 1 / fd 2 at a real file, then null the Python streams —
            # exactly the frozen-windowed state the shim exists to repair.
            os.dup2(f.fileno(), 1)
            os.dup2(f.fileno(), 2)
            sys.stdout = None
            sys.stderr = None

            ensure_std_streams()

            assert sys.stdout is not None
            assert sys.stderr is not None
            print("hello-stdout")
            print("hello-stderr", file=sys.stderr)
            sys.stdout.flush()
            sys.stderr.flush()
    finally:
        os.dup2(saved_fd1, 1)
        os.dup2(saved_fd2, 2)
        os.close(saved_fd1)
        os.close(saved_fd2)
        sys.stdout, sys.stderr = saved_out, saved_err

    text = capture.read_text()
    assert "hello-stdout" in text
    assert "hello-stderr" in text


def test_stderr_falls_back_to_stdout_when_fd2_dead(tmp_path):
    capture = tmp_path / "merged.txt"
    saved_out, saved_err = sys.stdout, sys.stderr
    saved_fd1, saved_fd2 = os.dup(1), os.dup(2)
    try:
        with open(capture, "wb") as f:
            os.dup2(f.fileno(), 1)
            # Close fd 2 so _fd_ok(2) is False; stderr must alias stdout.
            os.close(2)
            sys.stdout = None
            sys.stderr = None

            ensure_std_streams()

            assert sys.stderr is sys.stdout is not None
            print("only-stream", file=sys.stderr)
            sys.stderr.flush()
    finally:
        os.dup2(saved_fd1, 1)
        os.dup2(saved_fd2, 2)
        os.close(saved_fd1)
        os.close(saved_fd2)
        sys.stdout, sys.stderr = saved_out, saved_err

    assert "only-stream" in capture.read_text()


def test_noop_when_streams_already_writable():
    before_out, before_err = sys.stdout, sys.stderr
    ensure_std_streams()
    # A writable stream is left untouched (identity preserved).
    assert sys.stdout is before_out
    assert sys.stderr is before_err
