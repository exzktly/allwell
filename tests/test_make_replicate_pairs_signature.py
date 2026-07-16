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


def test_typing_names_importable():
    # The annotations referenced Dict/List/Optional with no import (latent
    # NameError under get_type_hints). They must now resolve.
    assert controller.Dict is not None
    assert controller.List is not None
    assert controller.Optional is not None
