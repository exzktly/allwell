"""The pipeline subprocess is reaped whatever ends the read of its output.

`spawn_pipeline` starts the child with `start_new_session=True` so that a Stop
can kill the whole process group. That also makes it its own session leader:
nothing else on the machine will reap it. So the reader loop is the only thing
standing between a failed read and a pipeline that keeps running against the
user's plate while the GUI reports the run never started.

The loop was not inside a `try`, and the stream was opened `text=True` with no
`errors=`. A filename can carry any byte the filesystem allows and is echoed
back in log lines, so one undecodable byte raised `UnicodeDecodeError` out of
the iterator, skipped `proc.wait()`, and orphaned the child.

Both halves are fixed: the decode can no longer fail, and the reaping no longer
depends on it not failing.
"""
from __future__ import annotations

import ast
import inspect
import textwrap


def test_the_pipeline_stream_replaces_undecodable_bytes():
    from services import pipeline_service

    src = inspect.getsource(pipeline_service.spawn_pipeline)
    assert 'errors="replace"' in src, (
        "the pipeline's stdout is decoded strictly, so one byte from a "
        "filename the filesystem allowed raises out of the reader")
    assert 'encoding="utf-8"' in src


def test_a_replacing_decoder_really_does_not_raise():
    """The behaviour, not the keyword — a decoder is easy to configure wrongly."""
    raw = b"ok\xffbad\n"
    assert raw.decode("utf-8", errors="replace").startswith("ok")


def test_every_stdout_loop_reaps_the_child_on_any_exception():
    """§7.7 #1: the seam is the *caller's* structure, not a helper's API.

    Both read loops — local and remote — must sit inside a `try` whose
    `finally` waits on the process. A loop added later without one is the same
    orphan, and nothing about the loop itself would look wrong.
    """
    from services import pipeline_runner

    src = inspect.getsource(pipeline_runner)
    tree = ast.parse(src)

    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.For)
             and "proc.stdout" in ast.unparse(n.iter)]
    assert len(loops) >= 2, (
        f"expected the local and remote read loops; found {len(loops)}")

    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        direct = [n for n in node.body
                  if isinstance(n, ast.For) and "proc.stdout" in ast.unparse(n.iter)]
        if not direct:
            continue
        tail = ast.unparse(ast.Module(
            body=list(node.finalbody) + [h for h in node.handlers],
            type_ignores=[]))
        if "proc.wait()" in tail or "_signal_subprocess" in tail:
            guarded.append(node.lineno)

    assert len(guarded) >= len(loops), (
        f"{len(loops)} loop(s) read proc.stdout but only {len(guarded)} are "
        f"inside a try that reaps or signals the child on the way out; an "
        f"exception from the unguarded one leaves a session leader running "
        f"against the user's plate")


def test_the_handler_signals_before_it_re_raises():
    """Reaping alone is not enough — a child that is still writing will not
    exit on `wait()`, so it has to be signalled first."""
    from services import pipeline_runner

    src = textwrap.dedent(inspect.getsource(pipeline_runner))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # The try that *directly* wraps the loop, not an outer one whose body
        # merely contains it — an outer handler runs after the child is
        # already orphaned.
        direct = [n for n in node.body
                  if isinstance(n, ast.For) and "proc.stdout" in ast.unparse(n.iter)]
        if not direct:
            continue
        handlers = ast.unparse(ast.Module(body=list(node.handlers), type_ignores=[]))
        assert "_signal_subprocess" in handlers, (
            "the read-failure handler does not signal the child, so `wait()` "
            "in the finally block would block on a process still producing output")
