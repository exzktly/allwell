"""Channel and timepoint labels read out of OME metadata (``pipeline.ome_explode``).

Two audit findings, both about a *name* rather than a pixel:

* **C6** — ``_safe_token`` keeps alphanumerics only, so ``"488 nm"`` and
  ``"488nm"`` (or ``"GFP-1"`` and ``"GFP_1"``, or two channels simply both named
  ``GFP``) reduce to one filename token. Every image is written as
  ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif``, so both channels are
  written to the same member name; ``zipfile`` keeps both copies of the bytes and
  a reader returns only the last. The run reports two images written for one
  readable image, and ``atomic_zip``'s verification cannot see it because the
  duplicate name appears on both sides of ``sorted(namelist())``. So the refusal
  has to happen in ``read_ome_layout``, before an archive exists.
* **C9** — ``DeltaTUnit`` handling covered minutes and treated everything else as
  seconds, so a file whose deltas are in milliseconds produced a ``1000h`` token
  for a frame taken one hour in. Those tokens are the filename's timepoint field
  and the app's chronological key, so every hours axis in Review was scaled by
  1000 (or by 1/3600 for ``DeltaTUnit="h"``). Ordering survived; the numbers did
  not.
"""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from pipeline.ome_explode import (  # noqa: E402
    _parse_ome_labels,
    explode_ome_tiff,
    read_ome_layout,
)


def _write_ome(path, *, positions=2, size_t=1, size_c=2, channel_names=("DAPI", "GFP")):
    y = x = 6
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.zeros((size_t, size_c, 1, y, x), np.uint16)
            for t in range(size_t):
                for c in range(size_c):
                    arr[t, c, 0] = pos * 100 + t * 10 + c
            tw.write(arr, metadata={"axes": "TCZYX",
                                    "Channel": {"Name": list(channel_names)}})
    return path


# ── C6: two names, one token ─────────────────────────────────────────────────

@pytest.mark.parametrize("names", [
    ("488 nm", "488nm"),
    ("GFP-1", "GFP_1"),
    ("GFP", "GFP"),
    ("DAPI", "d.a.p.i" .upper()),
])
def test_channel_names_that_reduce_to_one_token_are_refused(tmp_path, names):
    path = _write_ome(tmp_path / "dup.ome.tif", channel_names=names)
    layout = read_ome_layout(path)
    report = layout.validation_report("dup.ome.tif")
    assert report, f"{names!r} reduced to {layout.channel_names!r} and was accepted"
    assert "filename token" in report
    # The message names the channels the user has to go and rename, in the
    # spelling their acquisition software shows them.
    for name in names:
        assert repr(name) in report


def test_the_refusal_happens_before_any_archive_is_written(tmp_path):
    path = _write_ome(tmp_path / "dup.ome.tif", channel_names=("488 nm", "488nm"))
    dest = tmp_path / "out"
    with pytest.raises(ValueError) as excinfo:
        explode_ome_tiff(path, dest, experiment="exp", grid_rows=1, grid_cols=2,
                         fovs_per_well=1)
    assert "filename token" in str(excinfo.value)
    assert not list(dest.glob("*.zip")), "an archive was written despite the refusal"


def test_distinct_channel_names_are_still_accepted(tmp_path):
    path = _write_ome(tmp_path / "ok.ome.tif", channel_names=("DAPI", "GFP"))
    layout = read_ome_layout(path)
    assert layout.validation_report("ok.ome.tif") == ""
    assert layout.channel_names == ["DAPI", "GFP"]


def test_unnamed_channels_fall_back_to_distinct_tokens(tmp_path):
    """Two blank names must not both become one fallback token."""
    path = _write_ome(tmp_path / "blank.ome.tif", channel_names=("", ""))
    layout = read_ome_layout(path)
    assert layout.channel_names == ["c0", "c1"]
    assert layout.validation_report("blank.ome.tif") == ""


def test_timepoint_tokens_that_collide_are_refused():
    """Two ``DeltaT`` values that render alike at ``%g`` name one member twice."""
    xml = _ome_xml(size_c=1, size_t=2,
                   planes=[(0, "3600.0000001", "s"), (1, "3600.0000002", "s")])
    channels, tokens, _raw = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["1h", "1h"], f"expected a collision to reproduce, got {tokens!r}"
    from pipeline.ome_explode import OmeLayout

    layout = OmeLayout(n_positions=2, size_t=2, size_c=1, size_z=1,
                       channel_names=channels, timepoint_tokens=tokens,
                       axes_per_series=["TCYX", "TCYX"])
    report = layout.validation_report("t.ome.tif")
    assert "filename token" in report
    assert "timepoint" in report


# ── C9: every unit OME can state, and nothing else ───────────────────────────

def _ome_xml(*, size_c=1, size_t=2, planes):
    """Minimal OME-XML carrying one ``Plane`` per entry of *planes*."""
    plane_xml = "".join(
        f'<Plane TheZ="0" TheT="{t}" TheC="0" DeltaT="{delta}"'
        + (f' DeltaTUnit="{unit}"' if unit is not None else "")
        + "/>"
        for t, delta, unit in planes
    )
    return (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYCZT" '
        f'Type="uint16" SizeX="4" SizeY="4" SizeZ="1" SizeC="{size_c}" SizeT="{size_t}">'
        '<Channel ID="Channel:0:0" Name="GFP"/>'
        f"{plane_xml}</Pixels></Image></OME>"
    )


@pytest.mark.parametrize("unit,delta,want", [
    ("ms", 3_600_000, "1h"),
    ("s", 3600, "1h"),
    (None, 3600, "1h"),            # absent unit: OME's default is seconds
    ("min", 60, "1h"),
    ("h", 1, "1h"),
    ("d", 0.5, "12h"),
    ("µs", 3_600_000_000, "1h"),   # MICRO SIGN, U+00B5
    ("μs", 3_600_000_000, "1h"),   # GREEK SMALL LETTER MU, U+03BC
    ("minute", 60, "1h"),
    ("Ms", 3_600_000, "1h"),       # units are matched case-insensitively
])
def test_every_delta_t_unit_converts_to_hours(unit, delta, want):
    xml = _ome_xml(planes=[(0, 0, unit), (1, delta, unit)])
    _channels, tokens, _raw = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", want]


@pytest.mark.parametrize("unit", ["furlong", "ticks", "Zs", "1/s"])
def test_an_unreadable_unit_falls_back_to_positional_tokens(unit):
    """Unknown is not a synonym for seconds; a positional token states nothing."""
    xml = _ome_xml(planes=[(0, 0, unit), (1, 3600, unit)])
    _channels, tokens, _raw = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"], (
        f"an unreadable DeltaTUnit {unit!r} produced {tokens!r}"
    )


def test_an_empty_unit_attribute_is_read_as_the_schema_default():
    """``DeltaTUnit=""`` is a writer that stated no unit, not an unknown one.

    OME's default for the attribute is seconds, and an empty value is as close
    to absent as the file gets. Pinned because the alternative reading — treat
    it as unrecognised and go positional — is equally arguable and the choice
    should not drift silently.
    """
    xml = _ome_xml(planes=[(0, 0, ""), (1, 3600, "")])
    _channels, tokens, _raw = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", "1h"]


def test_one_unreadable_plane_drops_the_whole_file_to_positional():
    xml = _ome_xml(planes=[(0, 0, "s"), (1, 3600, "furlong")])
    _channels, tokens, _raw = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"]


def test_the_raw_channel_names_survive_tokenisation():
    xml = _ome_xml(planes=[(0, 0, "s"), (1, 3600, "s")])
    _channels, _tokens, raw = _parse_ome_labels(xml, 1, 2)
    assert raw == ["GFP"]
