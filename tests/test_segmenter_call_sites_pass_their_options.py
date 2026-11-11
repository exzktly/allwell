"""Every place the pipeline builds the nanowell count's segmenter passes the run's options to it.

The count's Cellpose model and thresholds reach Cellpose only through ``NanowellSettings.segmenter_options()``
(``tests/test_nanowell_count_takes_cellpose_thresholds.py`` follows a typed threshold to Cellpose's call). The run
and Test detection each build the segmenter at a call site of their own, and the tests there build one themselves,
so a call site that named only the model would pass them all and count at Cellpose's defaults: reverting the run's
line to that form left every one of them green. This guard reads the whole tree, so it lives in the fast lane.
"""

from __future__ import annotations


def _segmenter_calls(tree):
    import ast

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "cellpose_segmenter":
                yield node


def _offenders(sources) -> list[str]:
    """Each call that builds the count's segmenter without ``**settings.segmenter_options()``, which is where the
    model and the thresholds come from: a call that names ``model_type`` itself counts at Cellpose's defaults."""
    import ast

    bad = []
    for name, tree in sources:
        for call in _segmenter_calls(tree):
            starred = [k.value for k in call.keywords if k.arg is None]
            ok = any(isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                     and v.func.attr == "segmenter_options" for v in starred)
            if not ok or any(k.arg == "model_type" for k in call.keywords):
                bad.append(f"{name}:{call.lineno}")
    return bad


def test_every_place_the_pipeline_builds_the_count_s_segmenter_passes_its_options():
    """The run and Test detection each build the segmenter at their own call site; the tests above build one
    themselves, so a call site that still named only the model would pass them all and count at the defaults."""
    from tests._repo_scan import parse_source, python_sources, rel

    sources = [(rel(p), parse_source(p)) for p in python_sources()]
    calls = [(n, c) for n, t in sources for c in _segmenter_calls(t) if not n.startswith("tests/")]
    assert len(calls) >= 2, f"expected the run's and Test detection's call sites, found {[n for n, _ in calls]}"
    offenders = _offenders([(n, t) for n, t in sources if not n.startswith("tests/")])
    assert not offenders, ("these build the nanowell count's segmenter without **settings.segmenter_options(), so "
                           f"they ignore the run's thresholds: {offenders}")


def test_the_call_site_guard_bites():
    from tests._repo_scan import parse_text

    bad = parse_text("seg = nm.cellpose_segmenter(d, model_type=settings.cellpose_model)\n")
    good = parse_text("seg = nm.cellpose_segmenter(d, **settings.segmenter_options())\n")
    assert _offenders([("bad.py", bad)]) == ["bad.py:1"]
    assert _offenders([("good.py", good)]) == []
