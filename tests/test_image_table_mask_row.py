"""A channel's render mode is part of its identity on the grid.

The reported failure: setting a channel's ⚙ render to **cell mask** made that row
vanish from the Image Table entirely — not a row of placeholders, no row at all —
while **≥ threshold** on the same channel worked.

That asymmetry was the whole diagnosis. ``ChannelSpec.axis_token`` appended
``" (binary)"`` for ``threshold`` and returned the bare channel token for every
other render, and ``spec_to_state_fields`` keys ``channel_opts`` by that token.
A channel dropped twice — once as its image, once as its cell mask — therefore
wrote one key, and ``grid_layout._axis_dim_values`` derives the channel axis from
those keys, so the axis lost an entry and the second chip disappeared. The same
collapse hit raw-vs-corrected, silently, and ``spec_from_state`` rebuilt the
shelf from the same keys so the lost chip never came back.

These cover the token rule, the pixels a binary cell paints (white or one colour
per label), and the behaviours that had to survive the fix.
"""

from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.image_browser.image_table.image_compose import (
    _base_channel,
    _compose_image_table_rgb,
    is_variant_token,
)
from well_viewer.features.image_browser.image_table.state import (
    Axis,
    ChannelSpec,
    ImageTableState,
    TableSpec,
    spec_from_state,
    spec_to_state_fields,
)


def _shelf(*channels: ChannelSpec) -> TableSpec:
    return TableSpec(rows=Axis(root="well", channels=list(channels)),
                     cols=Axis(root="timepoint"))


def _rows(*channels: ChannelSpec) -> dict:
    """The channel_opts the grid builds its channel axis from."""
    return spec_to_state_fields(_shelf(*channels))["channel_opts"]


# ── the token rule ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("render,token", [
    ("corrected", "DAPI"),                # unchanged: old saved views keep their keys
    ("threshold", "DAPI (binary)"),       # unchanged: already had its own token
    ("mask", "DAPI (mask)"),
    ("raw", "DAPI (raw)"),
])
def test_the_render_is_part_of_the_axis_token(render, token):
    assert ChannelSpec("dapi", render=render).axis_token() == token


def test_a_channel_shown_as_image_and_as_mask_gets_two_rows():
    """The reported case. One key meant one row, and the mask chip was the one
    that vanished — the grid had no way to hold two renders of one channel."""
    opts = _rows(ChannelSpec("DAPI"), ChannelSpec("DAPI", render="mask"))
    assert list(opts) == ["DAPI", "DAPI (mask)"]
    assert [v["render"] for v in opts.values()] == ["corrected", "mask"]


def test_raw_beside_corrected_also_gets_two_rows():
    """The same collapse, never reported because it looks like the drag failed."""
    opts = _rows(ChannelSpec("DAPI"), ChannelSpec("DAPI", render="raw"))
    assert list(opts) == ["DAPI", "DAPI (raw)"]


def test_distinct_channels_are_untouched():
    opts = _rows(ChannelSpec("DAPI"), ChannelSpec("GFP", render="mask"))
    assert list(opts) == ["DAPI", "GFP (mask)"]


def test_both_chips_survive_a_round_trip():
    """``spec_from_state`` rebuilds the shelf from the same keys, so a token that
    collapsed lost the chip permanently — it did not come back on restore."""
    spec = _shelf(ChannelSpec("DAPI"),
                  ChannelSpec("DAPI", render="mask", label_colors=True))
    st = ImageTableState()
    for k, v in spec_to_state_fields(spec).items():
        setattr(st, k, v)
    back = spec_from_state(st)
    assert [(c.channel, c.render, c.label_colors) for c in back.rows.channels] == [
        ("DAPI", "corrected", False),
        ("DAPI", "mask", True),
    ]


@pytest.mark.parametrize("token,base,variant", [
    ("DAPI", "DAPI", False),
    ("DAPI (mask)", "DAPI", True),
    ("DAPI (binary)", "DAPI", True),
    ("DAPI (raw)", "DAPI", True),
    ("dapi (Mask)", "dapi", True),          # the suffix match is case-insensitive
])
def test_a_variant_token_resolves_back_to_its_channel(token, base, variant):
    """The load path asks for the base channel's pixels; only the render differs."""
    assert _base_channel(token) == base
    assert is_variant_token(token) is variant


# ── the pixels ───────────────────────────────────────────────────────────────

def _labels() -> np.ndarray:
    """Two touching nuclei plus a far one, on background."""
    m = np.zeros((4, 4), dtype=np.int32)
    m[0, 0] = 1
    m[0, 1] = 2                       # shares a border with label 1
    m[3, 3] = 300                     # an id well past a uint8 palette's reach
    return m


def _binary_rgb(mask, *, label_colors):
    return _compose_image_table_rgb(
        mask, None, lo=None, hi=None, cmap_name="", is_overlay=False,
        binary_mask=mask, show_boundaries=False, label_colors=label_colors)


def test_white_is_still_white():
    """The default is unchanged, so every existing grid and saved view paints
    exactly what it painted before."""
    rgb = _binary_rgb(_labels(), label_colors=False)
    assert rgb[0, 0].tolist() == [255, 255, 255]
    assert rgb[0, 1].tolist() == [255, 255, 255]
    assert rgb[3, 3].tolist() == [255, 255, 255]
    assert rgb[2, 2].tolist() == [0, 0, 0]           # background


def test_touching_nuclei_get_different_colours():
    """The point of the option: all-white cells that touch read as one shape, and
    two nuclei sharing a border are what a segmentation view exists to tell
    apart. Consecutive ids are exactly the pair a segmenter assigns to them."""
    rgb = _binary_rgb(_labels(), label_colors=True)
    one, two = rgb[0, 0].tolist(), rgb[0, 1].tolist()
    assert one != two
    assert one != [255, 255, 255] and two != [255, 255, 255]
    assert rgb[2, 2].tolist() == [0, 0, 0]           # background stays black


def test_the_colours_do_not_move_between_redraws():
    """A cell that is teal before a redraw and orange after it looks like a
    different cell. The palette is a pure function of the label id."""
    mask = _labels()
    assert np.array_equal(_binary_rgb(mask, label_colors=True),
                          _binary_rgb(mask, label_colors=True))


def test_only_the_cells_in_the_mask_are_painted():
    """A ≥threshold cell passes the label ids of the cells that cleared the gate;
    everything else must stay black rather than picking up a colour."""
    mask = _labels()
    gated = np.where(mask == 2, mask, 0)             # only label 2 passes
    rgb = _binary_rgb(gated, label_colors=True)
    assert rgb[0, 1].tolist() != [0, 0, 0]
    assert rgb[0, 0].tolist() == [0, 0, 0]
    assert rgb[3, 3].tolist() == [0, 0, 0]


def test_a_binary_cell_never_falls_back_to_the_intensity_image():
    """Guarding the existing contract: the binary branch wins over the
    fluorescence, so a mask view cannot come out half switched."""
    mask = _labels()
    intensity = np.full((4, 4), 5000.0, dtype=np.float32)
    rgb = _compose_image_table_rgb(
        intensity, None, lo=0.0, hi=10000.0, cmap_name="viridis", is_overlay=False,
        binary_mask=mask, show_boundaries=False, label_colors=False)
    assert rgb[2, 2].tolist() == [0, 0, 0]           # not a viridis mid-tone
    assert rgb[0, 0].tolist() == [255, 255, 255]
