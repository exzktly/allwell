"""Audit G8 — ``make_replicate_pairs`` drops the dead ``prefix`` parameter
(never used in the body) and the module gains its ``typing`` import.
"""
from __future__ import annotations

import inspect

from well_viewer.features.grouping import controller
from well_viewer.features.grouping.controller import make_replicate_pairs


def test_signature_has_no_prefix():
    assert list(inspect.signature(make_replicate_pairs).parameters) == ["toks"]


def test_pairs_adjacent_tokens_and_solo_tail():
    sets = make_replicate_pairs(["A1", "A2", "A3"])
    assert [s.wells for s in sets] == [["A1", "A2"], ["A3"]]


def test_typing_names_importable():
    # The annotations referenced Dict/List/Optional with no import (latent
    # NameError under get_type_hints). They must now resolve.
    assert controller.Dict is not None
    assert controller.List is not None
    assert controller.Optional is not None
