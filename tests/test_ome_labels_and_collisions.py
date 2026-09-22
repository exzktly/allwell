"""Channel and timepoint labels read out of OME metadata (``pipeline.ome_explode``).

Two audit findings, both about a *name* rather than a pixel:

* **C6** — ``util.filename_token.safe_token`` keeps alphanumerics only, so ``"488 nm"`` and
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

import logging

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
    # Case alone is not a distinction downstream: every reader of a channel
    # token folds case, so `GFP` and `gfp` write two member names that collapse
    # into one key the moment anything looks the channel up again.
    ("GFP", "gfp"),
    ("DAPI", "dapi"),
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


@pytest.mark.parametrize("deltas", [
    ("3600.0000001", "3600.0000002"),   # differ below %g's six significant figures
    ("0", "0"),                         # a writer that stamps one placeholder
])
def test_timepoints_that_would_render_alike_fall_back_to_positional(deltas):
    """One token for two timepoints would write every image of the second over
    the first. Positional tokens name them all and lose only a number the file
    did not really state."""
    xml = _ome_xml(size_c=1, size_t=2,
                   planes=[(0, deltas[0], "s"), (1, deltas[1], "s")])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"]


def test_colliding_timepoint_tokens_are_refused_as_a_backstop():
    """The invariant belongs to the member name, not to one producer of it.

    ``_parse_ome_labels`` heads this off (above), so this drives
    ``validation_report`` from a hand-built layout — the check is here for a
    layout assembled any other way, and it is the same three lines that catch
    the channel collision that does happen in the wild.
    """
    from pipeline.ome_explode import OmeLayout

    layout = OmeLayout(n_positions=2, size_t=2, size_c=1, size_z=1,
                       channel_names=["GFP"], timepoint_tokens=["1h", "1h"],
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
    # SI prefixes are case-sensitive in OME's UnitsTime enum, so the symbol
    # table is consulted before the lower-cased word table: ``Ms`` is a
    # megasecond and ``ms`` a millisecond, twelve orders of magnitude apart.
    ("Ms", 0.0036, "1h"),
    ("ks", 3.6, "1h"),
    ("cs", 360_000, "1h"),
])
def test_every_delta_t_unit_converts_to_hours(unit, delta, want):
    xml = _ome_xml(planes=[(0, 0, unit), (1, delta, unit)])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", want]


@pytest.mark.parametrize("unit", ["furlong", "ticks", "Zs", "1/s",
                                 # A case-significant symbol in a spelling
                                 # OME does not use: silently picking the
                                 # smaller of the two units it could mean
                                 # is the 10^9 fold this table prevents.
                                 "MS", "mS", "KS", "DAS"])
def test_an_unreadable_unit_falls_back_to_positional_tokens(unit):
    """Unknown is not a synonym for seconds; a positional token states nothing."""
    xml = _ome_xml(planes=[(0, 0, unit), (1, 3600, unit)])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"], (
        f"an unreadable DeltaTUnit {unit!r} produced {tokens!r}"
    )


def test_an_unreadable_unit_says_so_in_the_run_log(caplog):
    """The token is positional either way; the log is what distinguishes them.

    A plate whose timepoints read ``t0``/``t1`` because the writer stamped no
    DeltaT and one whose timepoints read ``t0``/``t1`` because it stamped a unit
    this converter cannot convert produce byte-identical archives. Only a log
    line separates a file that stated no time from one whose time was dropped.
    """
    xml = _ome_xml(planes=[(0, 0, "furlong"), (1, 3600, "furlong")])
    with caplog.at_level(logging.WARNING, logger="pipeline.ome_explode"):
        _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"]
    assert "cannot read" in caplog.text
    assert "furlong" in caplog.text
    assert "named by position" in caplog.text


def test_labels_that_collide_say_which_label_repeated(caplog):
    """Two timepoints rendering to one label is silent data loss without this."""
    xml = _ome_xml(planes=[(0, 3600, "s"), (1, 3600, "s")])
    with caplog.at_level(logging.WARNING, logger="pipeline.ome_explode"):
        _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"]
    assert "1h" in caplog.text, caplog.text
    assert "named by position" in caplog.text


def test_a_file_with_readable_units_logs_no_warning(caplog):
    xml = _ome_xml(planes=[(0, 0, "s"), (1, 3600, "s")])
    with caplog.at_level(logging.WARNING, logger="pipeline.ome_explode"):
        _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", "1h"]
    assert caplog.text.strip() == ""


def test_an_empty_unit_attribute_is_read_as_the_schema_default():
    """``DeltaTUnit=""`` is a writer that stated no unit, not an unknown one.

    OME's default for the attribute is seconds, and an empty value is as close
    to absent as the file gets. Pinned because the alternative reading — treat
    it as unrecognised and go positional — is equally arguable and the choice
    should not drift silently.
    """
    xml = _ome_xml(planes=[(0, 0, ""), (1, 3600, "")])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", "1h"]


def test_one_unreadable_plane_drops_the_whole_file_to_positional():
    xml = _ome_xml(planes=[(0, 0, "s"), (1, 3600, "furlong")])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["t0", "t1"]


def test_the_raw_channel_names_survive_tokenisation():
    xml = _ome_xml(planes=[(0, 0, "s"), (1, 3600, "s")])
    _channels, _tokens, raw, _note = _parse_ome_labels(xml, 1, 2)
    assert raw == ["GFP"]


# ── the label has to be one the app can read back ───────────────────────────

@pytest.mark.parametrize("unit,step,frames", [
    ("ms", 100, 6),        # 100 ms apart: 2.78e-05 h
    ("s", 1, 6),           # one second apart
    ("ms", 1, 4),          # one millisecond apart
    ("s", 3600, 4),        # an hour apart, the ordinary case
    ("min", 30, 5),
])
def test_every_rendered_timepoint_parses_and_sorts(unit, step, frames):
    """``%g`` goes exponential below 1e-4, and the parser matches no exponent.

    Adding the sub-second units made that routine rather than rare: a six-frame
    time-lapse 100 ms apart rendered ``2.77778e-05h``, ``parse_hours`` answered
    ``None`` for three of the six, and ``sort_key`` fell back to sorting those
    tokens as text — putting frames 5 and 6 ahead of frames 2 to 4. A number the
    app cannot read is worse than the wrong number it replaced, which at least
    ordered correctly.
    """
    from util.timepoint import parse_hours, sort_key

    xml = _ome_xml(planes=[(i, i * step, unit) for i in range(frames)])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, frames)
    assert all(not t.startswith("t") for t in tokens), (
        f"{unit} at {step} fell back to positional tokens: {tokens}")
    unread = [t for t in tokens if parse_hours(t) is None]
    assert not unread, f"the app's own parser cannot read {unread} out of {tokens}"
    assert sorted(tokens, key=sort_key) == tokens, (
        f"Review would order these {sorted(tokens, key=sort_key)}, not {tokens}")


def test_a_one_based_thet_does_not_mix_the_two_vocabularies():
    """An out-of-range TheT must not satisfy the completeness test.

    ``TheT`` is a zero-based index. A writer stamping it one-based left keys
    ``{1,2,3}`` for ``SizeT=3``, which is three keys and passed the count check
    while attaching every elapsed time to the wrong index: the tokens came out
    ``['t0', '0h', '1h']`` — mixed vocabularies, and ``t0`` and ``0h`` both read
    as 0 h, so two distinct timepoints shared one reading.
    """
    xml = _ome_xml(planes=[(1, 0, "s"), (2, 3600, "s"), (3, 7200, "s")])
    _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 3)
    assert tokens == ["t0", "t1", "t2"], tokens


def test_an_out_of_range_thet_says_which_way_it_was_out(caplog):
    xml = _ome_xml(planes=[(1, 0, "s"), (2, 3600, "s")])
    with caplog.at_level(logging.WARNING, logger="pipeline.ome_explode"):
        _parse_ome_labels(xml, 1, 2)
    assert "TheT outside" in caplog.text, caplog.text


def test_an_unreadable_unit_on_a_file_that_resolved_says_nothing(caplog):
    """A warning must describe what happened, not what could have.

    The unreadable-unit line fired wherever a bad unit was seen, which is not
    the same as the file falling back — one channel's planes carrying a bad
    unit while another's carry seconds leaves every timepoint resolved. Saying
    "named by position" about a file whose timepoints are named by elapsed
    time sends a user to re-export a plate for nothing.
    """
    planes = [(0, 0, "s"), (1, 3600, "s"),         # channel 0: readable
              (0, 0, "furlong"), (1, 3600, "furlong")]   # channel 1: not
    xml = _ome_xml(planes=planes)
    with caplog.at_level(logging.WARNING, logger="pipeline.ome_explode"):
        _channels, tokens, _raw, _note = _parse_ome_labels(xml, 1, 2)
    assert tokens == ["0h", "1h"], tokens
    assert caplog.text.strip() == "", (
        f"warned about a fallback that did not happen: {caplog.text!r}")
