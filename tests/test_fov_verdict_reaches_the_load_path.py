"""The FOV-label warning has to be able to fire.

`_report_unusable_fov_labels` reads `dataset.fov_integrity`, which `get_rows`
fills as it builds a well's frame. But `get_rows` is not the only way a frame
is built: `load_controller.load_directory` fills `dataset.cache` itself, well by
well, and never called it — so the map was empty for the whole session and the
warning could not fire at load time, which is the one moment it exists for.

The function existed, was covered by tests of its own, and was unreachable.
That is the §7.7 shape: a branch written, tested and documented, that the app
never takes. These tests drive the seam rather than the function.
"""

from __future__ import annotations

import ast
import pathlib

import pandas as pd
import pytest
from tests._repo_scan import parse_source

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _untrusted_frame() -> pd.DataFrame:
    """Three distinct images all labelled field "1" — a well whose filenames
    did not match the schema."""
    return pd.DataFrame({
        "fov": ["1", "1", "1"],
        "filename": ["a.tif", "b.tif", "c.tif"],
        "nucleus_id": [1, 2, 3],
    })


def test_the_recorder_stores_an_untrusted_verdict(ctx_stub):
    from review.data.data_loading import record_fov_verdict

    record_fov_verdict(ctx_stub, "A01", _untrusted_frame())
    verdict = ctx_stub.state.dataset.fov_integrity["A01"]
    assert verdict.trusted is False
    assert verdict.reason


def test_a_well_with_real_field_labels_is_trusted(ctx_stub):
    """The control: the recorder must not condemn an ordinary well."""
    from review.data.data_loading import record_fov_verdict

    df = pd.DataFrame({
        "fov": ["1", "2", "3"],
        "filename": ["a.tif", "b.tif", "c.tif"],
        "nucleus_id": [1, 2, 3],
    })
    record_fov_verdict(ctx_stub, "A01", df)
    assert ctx_stub.state.dataset.fov_integrity["A01"].trusted is True


@pytest.fixture
def ctx_stub():
    import types

    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(fov_integrity={})))


def test_every_path_that_builds_a_frame_records_a_verdict():
    """The seam, checked structurally.

    Two places put a freshly-loaded frame into ``dataset.cache``:
    ``data_loading.get_rows`` and ``load_controller.load_directory``. Only the
    first recorded a verdict, and because the second is the one that runs at
    *load* time, the load-time warning had nothing to read. A third such site
    would inherit the same silence, so the rule is checked rather than
    remembered.
    """
    sites: list[str] = []
    for rel in ("review/data/data_loading.py",
                "review/core/load_controller.py"):
        path = REPO_ROOT / rel
        tree = parse_source(path)
        for node in ast.walk(tree):
            # `<something>.cache[<key>] = <frame>`
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Subscript):
                    continue
                if getattr(target.value, "attr", "") != "cache":
                    continue
                fn = _enclosing_function(tree, node)
                if fn is None:
                    continue
                if "record_fov_verdict" not in ast.unparse(fn):
                    sites.append(f"{rel}:{node.lineno} (in {fn.name})")
    assert not sites, (
        "these build a well's frame into dataset.cache without recording its "
        "FOV verdict, so dataset.fov_integrity stays empty and the load-time "
        "warning cannot fire:\n  " + "\n  ".join(sites))


def _enclosing_function(tree: ast.AST, node: ast.AST):
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(inner is node for inner in ast.walk(fn)):
                return fn
    return None
