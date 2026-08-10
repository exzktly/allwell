"""Audit S5 — image-table (fov, tp) resolution must not silently surface a
neighbouring frame.

``_match_image_ref`` resolves in tiers (exact → normalized-token →
numeric-close) and, when the FOV dropdown is empty but the dataset holds more
than one FOV for the timepoint, prefers "no image" over an arbitrary first FOV.
Non-exact resolutions are annotated by ``_load_array``'s debug breadcrumb.
"""
from __future__ import annotations

import logging

from well_viewer.data import frame_load as fl


def test_exact_match_wins():
    images = {("1", "0"): "A", ("2", "0"): "B"}
    ref, kind = fl.match_image_ref(images, "1", "0")
    assert ref == "A" and kind == "exact"


def test_normalized_token_match():
    # dropdown "1.0" ↔ cache "1"
    images = {("1", "0"): "A"}
    ref, kind = fl.match_image_ref(images, "1.0", "0.0")
    assert ref == "A" and kind == "normalized"


def test_numeric_close_match_for_t_prefixed_keys():
    # cache keys carry a T-prefix; dropdown is plain numeric.
    images = {("1", "T01"): "A"}
    ref, kind = fl.match_image_ref(images, "1", "1")
    assert ref == "A" and kind == "numeric"


def test_empty_fov_single_fov_resolves():
    # A genuinely single-FOV dataset with an empty dropdown still renders.
    images = {("1", "0"): "A"}
    ref, kind = fl.match_image_ref(images, "", "0")
    assert ref == "A" and kind == "normalized"


def test_empty_fov_multi_fov_prefers_no_image():
    # S5: empty dropdown + two FOVs at this tp → prefer "no image" over an
    # arbitrary first FOV.
    images = {("1", "0"): "A", ("2", "0"): "B"}
    ref, kind = fl.match_image_ref(images, "", "0")
    assert ref is None and kind == "ambiguous_fov"


def test_no_match_returns_empty_kind():
    images = {("1", "0"): "A"}
    ref, kind = fl.match_image_ref(images, "9", "9")
    assert ref is None and kind == ""


def test_load_array_annotates_non_exact(monkeypatch, caplog):
    import well_viewer.data.image_discovery as idisc
    monkeypatch.setattr(idisc, "open_imgref_as_array", lambda *a, **k: "IMG")
    # Only a T-prefixed key exists; request the plain-numeric form → numeric tier.
    fluor = {("1", "T01"): object()}
    monkeypatch.setattr(fl, "load_well_channel", lambda *a, **k: fluor)

    name = "well_viewer.data.frame_load"
    with caplog.at_level(logging.DEBUG, logger=name):
        out = fl.load_frame_array(None, {}, "A01", "GFP", "1", "1")
    assert out == "IMG"
    assert any("fallback" in r.getMessage() for r in caplog.records), \
        "a non-exact resolution must leave a breadcrumb"
