"""FOV-token numeric ordering (``util.fov``).

The converter and hand-named datasets can prefix the FOV token with ``f``
(``f0``, ``f10``). These tokens must order numerically — ``f2`` before ``f10`` —
not lexicographically, everywhere a FOV dropdown or listing is built.
"""

from __future__ import annotations

from util.fov import parse_fov, sort_key


def test_parse_bare_number():
    assert parse_fov("1") == 1.0
    assert parse_fov("01") == 1.0
    assert parse_fov("1.5") == 1.5


def test_parse_prefixed_takes_trailing_digits():
    assert parse_fov("f0") == 0.0
    assert parse_fov("f10") == 10.0
    assert parse_fov("F001") == 1.0
    assert parse_fov("fov3") == 3.0


def test_parse_opaque_is_none():
    assert parse_fov("") is None
    assert parse_fov("edge") is None
    assert parse_fov(None) is None


def test_prefixed_fovs_sort_numerically_not_lexicographically():
    tokens = ["f10", "f2", "f1", "f0", "f11", "f3"]
    assert sorted(tokens, key=sort_key) == ["f0", "f1", "f2", "f3", "f10", "f11"]


def test_bare_number_fovs_sort_numerically():
    tokens = ["10", "2", "1"]
    assert sorted(tokens, key=sort_key) == ["1", "2", "10"]


def test_opaque_tokens_sort_last_as_a_block():
    tokens = ["f10", "edge", "f2", "control"]
    ordered = sorted(tokens, key=sort_key)
    assert ordered == ["f2", "f10", "control", "edge"]
