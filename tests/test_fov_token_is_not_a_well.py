"""A field token ``f1`` must not be read as the well ``F01``.

``pipeline/ome_explode`` writes ``{experiment}_{channel}_{well}_f{fov}_{tp}``:
the well comes from ``canonical_well_label`` and is upper-case, and the field
marker is a lower-case ``f`` in front of its number. The free-text well scan
(``_WELL_TOKEN_IN_TEXT_RE``, and the substring fallback beside it) read both
cases, so ``f1`` answered "yes, this is well F01" — for every well's first
field, in every plate using the standard convention.

The consequence is the one item 56 was about, reached through a different
vocabulary: the loose-file per-well filter admitted another well's image. Well
F01 drew A01's picture, F02 drew B02's, and a filename naming no well at all
(``sample_f3_t0.tif``) was claimed by F03 — each of them a plausible-looking
image under a label it does not belong to.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from well_viewer.data.image_resolver import (
    _WELL_TOKEN_IN_TEXT_RE,
    well_token_matches_text,
)


def _tokens(text: str) -> list[str]:
    return _WELL_TOKEN_IN_TEXT_RE.findall(text)


def test_the_field_marker_is_not_a_well_token():
    assert _tokens("A01_GFP_f1_t0.tif") == ["A01"]
    assert _tokens("B02_GFP_f11_t0.tif") == ["B02"]
    # A name that carries a field and no well names no well.
    assert _tokens("sample_f3_t0.tif") == []


def test_an_upper_case_row_f_well_is_still_read():
    assert _tokens("F01_GFP_f1_t0.tif") == ["F01"]
    assert _tokens("F12_GFP_f2_t0.tif") == ["F12"]


def test_every_other_row_stays_case_insensitive():
    assert _tokens("a01_GFP_f1_t0.tif") == ["a01"]
    assert _tokens("h12_GFP_f1_t0.tif") == ["h12"]


def test_a_legacy_three_digit_field_never_matched_and_still_does_not():
    assert _tokens("exp_GFP_A01_F001_0h.tif") == ["A01"]


def test_the_predicate_refuses_another_wells_image():
    assert well_token_matches_text("A01_GFP_f1_t0.tif", "F01") is False
    assert well_token_matches_text("A01_GFP_f1_t0.tif", "A01") is True
    assert well_token_matches_text("B02_GFP_f2_t0.tif", "F02") is False


def test_the_substring_fallback_applies_the_same_rule():
    """The fallback runs when no delimited token is found at all.

    Leaving it case-insensitive would have left one function holding two
    definitions of the same word: the regex refusing ``f1`` and the substring
    scan two lines below accepting it.
    """
    # No delimited well token in either of these.
    assert well_token_matches_text("sample_f1_t0.tif", "F01") is False
    assert well_token_matches_text("scan_f01_t0.tif", "F01") is False
    # The case the fallback exists for still works, for row F and for others.
    assert well_token_matches_text("plateA01run.tif", "A01") is True
    assert well_token_matches_text("plateF01run.tif", "F01") is True
    assert well_token_matches_text("platea01run.tif", "A01") is True


def _write_tif(path: Path) -> None:
    from PIL import Image

    Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(path, format="TIFF")


@pytest.fixture()
def loose_plate(tmp_path):
    pytest.importorskip("PIL")
    for name in ("A01_GFP_f1_t0.tif", "B02_GFP_f2_t0.tif"):
        _write_tif(tmp_path / name)
    return tmp_path


def _names(refs):
    return sorted(Path(str(r.disk_path)).name for r in refs.values())


def test_a_row_f_well_no_longer_shows_another_wells_field(loose_plate):
    from well_viewer.data.image_discovery import find_well_images_and_masks

    f01, *_rest = find_well_images_and_masks(loose_plate, "F01", "GFP")
    f02, *_rest = find_well_images_and_masks(loose_plate, "F02", "GFP")
    # Empty is the honest answer: this plate holds no row-F images. Showing
    # A01's frame under the F01 label is the failure, and it is invisible.
    assert _names(f01) == []
    assert _names(f02) == []


def test_the_wells_that_do_own_those_images_still_find_them(loose_plate):
    from well_viewer.data.image_discovery import find_well_images_and_masks

    a01, *_rest = find_well_images_and_masks(loose_plate, "A01", "GFP")
    b02, *_rest = find_well_images_and_masks(loose_plate, "B02", "GFP")
    assert _names(a01) == ["A01_GFP_f1_t0.tif"]
    assert _names(b02) == ["B02_GFP_f2_t0.tif"]


def test_a_genuine_row_f_plate_still_resolves(tmp_path):
    pytest.importorskip("PIL")
    from well_viewer.data.image_discovery import find_well_images_and_masks

    for name in ("F01_GFP_f1_t0.tif", "A01_GFP_f1_t0.tif"):
        _write_tif(tmp_path / name)

    # Only one frame comes back: with no schema to parse the field from, both
    # files key on the same ``(fov, tp)`` and the merge is first-hit-wins. That
    # predates this change and is not what is under test — what is, is *whose*
    # frame it is.
    f01, *_rest = find_well_images_and_masks(tmp_path, "F01", "GFP")
    assert _names(f01) == ["F01_GFP_f1_t0.tif"]
