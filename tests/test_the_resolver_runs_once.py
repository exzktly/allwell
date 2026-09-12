"""The input resolver is never run twice for one run.

`resolve_dirs` groups a plate's loose images into per-well archives and then
deletes the sources it consumed. Running it a second time over a directory the
first pass has already changed is not a retry — it is a pass over different
input, mid-way through the first one's edits.

`PipelineRunner` used to call it inside `try: … except TypeError:` and, on a
`TypeError`, call it again without `proc_hook`. The comment said this was for
"callers that haven't been updated yet", but the only production caller —
`analyze.domain.input._resolve_run_dirs_for_runner` — takes `proc_hook`. So the
handler could not be reached by a stale signature at all; it could only be
reached by a `TypeError` raised from *inside* the resolver, which it answered
by re-running the whole thing. A branch enterable only by masking a bug, whose
response is to repeat a side-effecting operation.

The second call also dropped `proc_hook`, so the grouping subprocess was
untracked and Stop could not reach it.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from services.pipeline_runner import PipelineRunner


def test_a_typeerror_from_inside_the_resolver_is_not_a_retry():
    """Driven: the resolver raises once and must not be asked again."""
    calls: list[dict] = []

    def _resolver(_opts, _log_q, **kw):
        calls.append(kw)
        raise TypeError("something inside the resolver, not its signature")

    runner = PipelineRunner()
    runner.start(__import__("pathlib").Path("pm.py"), {}, resolve_dirs=_resolver)
    if runner._thread is not None:
        runner._thread.join(timeout=5)

    assert len(calls) == 1, (
        f"the resolver ran {len(calls)} times for one run; the second pass "
        f"sees a directory the first had already begun rewriting")


def test_the_one_call_forwards_proc_hook():
    """Without it the grouping subprocess is untracked and Stop cannot
    reach it — which is what the fallback call silently gave up."""
    calls: list[dict] = []

    def _resolver(_opts, _log_q, **kw):
        calls.append(kw)
        return None

    runner = PipelineRunner()
    runner.start(__import__("pathlib").Path("pm.py"), {}, resolve_dirs=_resolver)
    if runner._thread is not None:
        runner._thread.join(timeout=5)

    assert calls and "proc_hook" in calls[0], calls


def test_no_typeerror_fallback_remains_around_the_resolver():
    """The structural half, so the shim cannot come back as a kindness."""
    src = textwrap.dedent(inspect.getsource(PipelineRunner._run_pipeline_thread))
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
        if "resolve_dirs(" not in body:
            continue
        for handler in node.handlers:
            names = ast.unparse(handler.type) if handler.type else ""
            assert "TypeError" not in names, (
                "a TypeError handler wraps the resolver again; it can only be "
                "entered by a fault inside the resolver, and its answer is to "
                "run the resolver a second time")
