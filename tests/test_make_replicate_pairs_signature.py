"""``make_replicate_pairs`` chunks adjacent tokens into replicate groups of a
configurable size N (default 2). Extended from the historical always-pairs
behaviour so a group can hold N replicate wells, not just 2.
"""
from __future__ import annotations

import inspect

from well_viewer.features.grouping import controller
from well_viewer.features.grouping.controller import make_replicate_pairs


def test_signature_takes_toks_and_size():
    params = inspect.signature(make_replicate_pairs).parameters
    assert list(params) == ["toks", "size"]
    assert params["size"].default == 2


def test_default_size_pairs_adjacent_tokens_and_solo_tail():
    sets = make_replicate_pairs(["A1", "A2", "A3"])
    assert [s.wells for s in sets] == [["A1", "A2"], ["A3"]]


def test_size_three_chunks_triplicates_with_remainder():
    sets = make_replicate_pairs(["A1", "A2", "A3", "A4"], 3)
    assert [s.wells for s in sets] == [["A1", "A2", "A3"], ["A4"]]


def test_size_one_makes_solo_sets():
    sets = make_replicate_pairs(["A1", "A2"], 1)
    assert [s.wells for s in sets] == [["A1"], ["A2"]]


def test_annotations_resolve():
    """Every annotation in the module resolves — no latent ``NameError``.

    This module once annotated with ``Dict`` / ``List`` / ``Optional`` and
    imported none of them, which ``get_type_hints`` turns into a ``NameError``
    at the first introspection. The check used to assert those three names were
    importable, which was a proxy for the real invariant and stopped meaning
    anything once the annotations moved to the builtin generics (``dict[...]``,
    ``X | None``) that always resolve. It now asserts the invariant directly, so
    it keeps working whichever spelling the annotations use.
    """
    import typing

    for name, obj in vars(controller).items():
        if not callable(obj) or getattr(obj, "__module__", None) != controller.__name__:
            continue
        if not getattr(obj, "__annotations__", None):
            continue
        typing.get_type_hints(obj)   # raises NameError on an unresolvable name
