"""De-interleave a single packed OME-TIFF into per-well ``.zip`` archives.

Some acquisition software emits one OME-TIFF holding every timepoint, channel,
Z-plane and stage position (well × field-of-view) of a plate scan, with the
dimension order described in the embedded OME-XML metadata. All-Well's pipeline,
by contrast, is filename-token driven: it wants one 2-D TIFF per
(channel, well, FOV, timepoint), named to the filename schema, grouped per well.

This module bridges the two by *exploding* the packed file directly into the
per-well ``<WELL>.zip`` archives the pipeline reads — the same layout
``resolve_input_output`` accepts as ready input (no ``WellPlateZipper`` pass).
Wells are converted in parallel across a thread pool, each written as one atomic
archive (``util.zip_write.atomic_zip``), which also collapses the thousands of
per-file fsyncs a loose-TIF explode would cost into two per well.

Two axes get special handling:

* **Z** is collapsed by **maximum-intensity projection** per (position, T, C) —
  the standard reduction for fluorescence quantification.
* the **stage-position** axis (one OME ``Image`` / ``tifffile`` series per
  position) is mapped to a 96-well label via :mod:`util.plate_grid`, using the
  serpentine acquisition order and the imaged well-grid dimensions the user
  supplies. Each position is one well × FOV; FOVs-per-well is derived as
  ``n_positions / (grid_rows * grid_cols)``.

Channel names and timepoint labels come from the OME-XML when present, falling
back to positional tokens (``c0``, ``t0``) otherwise.

Depends only on numpy + tifffile (like :mod:`pipeline.io`) plus the stdlib and
the ``util`` helpers — no ``well_viewer`` / ``widgets`` / Qt imports, so it stays
inside the pipeline self-sufficiency contract (ARCHITECTURE §6.2).
"""

from __future__ import annotations

import math
import io
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile

from util.plate_grid import block_well_labels, normalize_well_order
from util.zip_write import atomic_zip

# How a single-image file's flat T-frame sequence splits into (position, timepoint):
#   "single"      — every frame is a distinct position; one timepoint.
#   "pos_in_time" — time on the outside: frame = t*n_positions + p.
#   "time_in_pos" — position on the outside: frame = p*n_timepoints + t.
FRAME_ORDERS = ("single", "pos_in_time", "time_in_pos")


class OmeExplodeCancelled(Exception):
    """Raised when a ``should_abort`` callback stops an explode mid-run."""


@dataclass
class OmeLayout:
    """The dimensions and metadata labels read from a packed OME-TIFF.

    Two structures are represented:

    * **Multi-image** — the plate scan is stored as several OME ``<Image>``
      elements (one ``tifffile`` series each), one per stage position. Then
      ``single_image`` is False, ``n_positions`` is the series count, and
      ``size_t`` is the timepoints *per* position.
    * **Single-image packed** — one ``<Image>`` whose ``T`` axis packs every
      stage position (and any real timepoints) into one flat frame sequence,
      because OME allows only X/Y/Z/C/T in an Image (no position dimension).
      Then ``single_image`` is True and ``n_frames`` is that ``SizeT`` frame
      count; how it splits into positions × timepoints is decided at explode time
      from the user's grid and frame-order.

    The fields after ``n_frames`` describe the file's *soundness* rather than its
    shape — whether it is an OME-TIFF at all, whether the dimensions it declares
    are the ones it turned out to have, and whether every plane it declares is
    backed by image data. :meth:`validation_report` turns them into the one
    sentence a caller shows; :func:`series_page_gaps` defines what a gap is.
    """

    n_positions: int
    size_t: int
    size_c: int
    size_z: int
    channel_names: list[str]      # length == size_c
    timepoint_tokens: list[str]   # length == size_t (multi-image only)
    axes_per_series: list[str]    # tifffile axes string per series
    # T/C/Z as each series actually holds them. `size_t`/`size_c`/`size_z`
    # above come from series[0] alone, which is the whole plate's shape only if
    # every position agrees — and a plate whose acquisition was stopped early at
    # one well does not. Recorded so `validation_report` can say so before a
    # single archive is written.
    sizes_per_series: list[dict[str, int]] = field(default_factory=list)
    single_image: bool = False
    n_frames: int = 0             # single-image: the T-axis frame count
    total_planes: int = 0         # image planes the metadata declares, all series
    missing_planes: int = 0       # of those, the ones the file holds no data for
    # Series indices (= stage positions, for a multi-image file) holding at least
    # one missing plane; the first missing plane's index within its own series
    # (-1 when none), which locates the shortfall in a single-image file where
    # there are no positions to name; and companion files the OME-XML references
    # that are not on disk beside the source — the usual reason planes go missing.
    incomplete_positions: list[int] = field(default_factory=list)
    first_missing_plane: int = -1
    missing_files: list[str] = field(default_factory=list)
    # Is this an OME-TIFF at all, and did its OME structure survive the read?
    # Three separate questions, because a plain TIFF stack is a legitimate input
    # here while a *named* ``.ome.tif`` with no readable OME-XML is a damaged
    # one. ``series_kind`` is ``tifffile``'s own verdict: anything other than
    # ``"ome"`` means it declined to build the series from the OME structure and
    # fell back to enumerating pages, so channel and timepoint identity are
    # guesses whatever the metadata says.
    named_ome: bool = False
    has_ome_xml: bool = True
    series_kind: str = "ome"
    # ``SizeT``/``SizeC``/``SizeZ`` as the OME-XML states them, against which the
    # series tifffile actually produced is checked: a file can declare forty
    # frames and yield one.
    declared_sizes: dict[str, int] = field(default_factory=dict)
    # Physical IFDs in the file that no series slot references. Non-zero means
    # image data is present that the metadata's index map never points at — the
    # third cause of a missing plane, and the only one where nothing is lost.
    unreferenced_pages: int = 0
    # What ``tifffile`` itself said while reading. It diagnoses every fault here
    # (bad page offsets, out-of-range TiffData indices, missing companion files)
    # and writes it to a logger, so a caller that does not listen throws the
    # file's own account of what is wrong with it away.
    warnings: list[str] = field(default_factory=list)
    # ``(x, y)`` stage coordinates per plane, in the OME-XML's plane order, for
    # callers that infer the plate layout from where the images were taken.
    stage_positions: list[tuple[float, float]] = field(default_factory=list)
    # True when those coordinates were converted to **micrometres** from a unit
    # the file stated. Without it a layout can only be read from the *ratios*
    # between positions, which cannot tell wells two apart from wells one apart
    # unless something in the scan is adjacent; with it, a coordinate names its
    # well outright. False when the file states no unit, or states one that is
    # not a length (OME's default for PositionX is "reference frame", which is
    # the microscope's own and has no fixed size).
    stage_positions_in_um: bool = False
    # What metadata the file turned out to carry, as ``{source: count}``, and a
    # sample of the keys in it. Neither is used to decide anything — they exist
    # so a file that yields no layout can say what it *does* hold instead of
    # only what it does not. Without that, "no stage coordinates" is
    # indistinguishable from "looked in the wrong place", and the only way to
    # tell them apart is to have the file in hand.
    metadata_blocks: dict[str, int] = field(default_factory=dict)
    metadata_key_sample: list[str] = field(default_factory=list)
    # One name per ``<Image>``, from its ``<StageLabel>`` or its own ``Name``.
    # Coordinates say where on the *stage* a position was; only these ever say
    # where on the *plate*, so they are the one place a starting well can come
    # from rather than be typed.
    position_names: list[str] = field(default_factory=list)
    # The channel names as the OME-XML spells them, before ``_safe_token``
    # strips them to filename tokens. Kept only so a token collision can name
    # the two channels a user has to go and rename; nothing decides anything
    # from these.
    raw_channel_names: list[str] = field(default_factory=list)

    def _colliding_tokens(self) -> tuple[str, str, list[str]] | None:
        """The first token collision the exploded filenames would carry.

        Returns ``(what, token, the names that produced it)``, or ``None``.

        Every image is published as
        ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif``, so two channels
        whose names reduce to the same token — ``"488 nm"`` and ``"488nm"``,
        ``"GFP-1"`` and ``"GFP_1"``, or simply two channels both called ``GFP`` —
        write the *same member name* into one archive. ``zipfile`` accepts that
        with a ``UserWarning`` and keeps both copies of the bytes; a reader
        returns only the last, so one channel is unreachable and the run reports
        two images written for one readable image. ``atomic_zip``'s
        verification cannot catch it either: the duplicate name is on both sides
        of the comparison, so ``landed == written``. The same holds for two
        timepoints whose ``DeltaT`` values render alike at ``%g``'s six
        significant figures — though :func:`_parse_ome_labels` heads that half
        off by falling back to positional tokens, so in practice this fires on
        channel names. The timepoint arm stays because the invariant belongs to
        the member name rather than to one producer of it.
        """
        for what, tokens, sources in (
            ("channel", self.channel_names,
             self.raw_channel_names or self.channel_names),
            ("timepoint", self.timepoint_tokens, self.timepoint_tokens),
        ):
            # Keyed case-**insensitively**, because every consumer of these
            # tokens folds case: `_SchemaReader.channel`, `auto_threshold`'s
            # member index and `process_microscopy`'s two grouping sites all
            # `.lower()` the channel they read out of a filename. So `GFP` and
            # `gfp` write two distinct member names, pass this check and pass
            # `atomic_zip` — and then collapse into one key downstream, which
            # is the same channel-becomes-unreachable outcome by a slower
            # route. Comparing raw tokens made the refusal blind to exactly
            # the pair a user is most likely to produce.
            seen: dict[str, list[str]] = {}
            for i, tok in enumerate(tokens):
                src = sources[i] if i < len(sources) else tok
                seen.setdefault(str(tok).lower(), []).append(str(src))
            for tok, names in seen.items():
                if len(names) > 1:
                    return what, tok, names
        return None

    def _ragged_positions(self) -> tuple[str, int, int, int] | None:
        """The first axis on which the positions disagree, or ``None``.

        Returns ``(axis label, series-0 size, offending index, its size)``.
        Single-image files have one series and cannot be ragged.

        **T and C only.** Those are the axes the explode loop indexes, once per
        position, from series 0's sizes — so a position that holds fewer of
        them is asked for a plane it does not have. Z is not one of them:
        :func:`_extract_plane` collapses the whole stack, so positions with
        different stack depths (routine with per-position autofocus) project
        correctly and produce exactly the planes they should. Blocking on Z
        refused a sound file, and said it did so for a reason that was not
        true.
        """
        if self.single_image or len(self.sizes_per_series) < 2:
            return None
        labels = {"T": "timepoints", "C": "channels"}
        first = self.sizes_per_series[0]
        for ax in ("T", "C"):
            want = int(first.get(ax, 1))
            for i, sizes in enumerate(self.sizes_per_series[1:], start=1):
                got = int(sizes.get(ax, 1))
                if got != want:
                    return labels[ax], want, i, got
        return None

    def validation_report(self, name: str = "") -> str:
        """The one blocking problem with this file, or ``""`` if it is sound.

        Ordered by how far upstream the fault is: a file that is not an OME-TIFF
        cannot have its dimensions checked, and dimensions that were not
        realised make a plane count meaningless. Each branch names the remedy,
        because the causes need different ones and the wrong remedy costs a
        re-export of a plate that did not need one.
        """
        subject = name or "This file"
        detail = f" {self.warnings[0]}" if self.warnings else ""
        # A plain TIFF stack carries no OME-XML and is a legitimate input, so
        # only a file *named* .ome.tif is expected to have any: one that does
        # not has almost certainly been truncated in transit, which leaves a
        # structurally valid TIFF holding a fraction of the plate.
        if self.named_ome and not self.has_ome_xml:
            return (
                f"{subject} has no readable OME metadata, so the wells, channels "
                f"and timepoints it holds are unknown.{detail} A copy that stopped "
                f"part-way does this — the file still opens as a plain TIFF and "
                f"reads as a fraction of its real size. Check its size against the "
                f"original and copy it again."
            )
        if self.has_ome_xml and self.series_kind != "ome":
            return (
                f"{subject} carries OME metadata that does not describe its pages, "
                f"so the reader fell back to a plain sequence and the channel and "
                f"timepoint of each plane are unknown.{detail} Re-export it from "
                f"the acquisition software."
            )
        # Every position must agree about T/C/Z, because the explode loop asks
        # each of them for `t` in `range(self.size_t)` and `size_t` is read from
        # series[0] alone. A position holding fewer used to have its last frame
        # written out again under each later timepoint's name — a fold-change
        # over time of exactly 1.000, indistinguishable from a measured null.
        # The file is not damaged and declares nothing false, so none of the
        # checks above sees it: it is honestly describing two differently
        # shaped Images.
        ragged = self._ragged_positions()
        if ragged:
            ax, first, odd_pos, odd = ragged
            return (
                f"{subject} holds {self.n_positions} stage positions that do "
                f"not agree about how many {ax} they have: position 1 has "
                f"{first} and position {odd_pos + 1} has {odd}. Every position "
                f"has to carry the same {ax} for the timepoints and channels to "
                f"line up across the plate. Re-export it from the acquisition "
                f"software, or export the affected position separately."
            )
        # Names, not shape: two channels (or two timepoints) that reduce to one
        # filename token would publish one member name twice, and the second
        # copy is unreachable for ever after. Refused here, before an archive is
        # written, because nothing downstream can see it — see
        # :meth:`_colliding_tokens`.
        collision = self._colliding_tokens()
        if collision:
            what, tok, names = collision
            shown = ", ".join(repr(n) for n in names)
            return (
                f"{subject} has {len(names)} {what}s whose names all reduce to "
                f"the filename token {tok!r}: {shown}. Every image is named "
                f"<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif, so those "
                f"would be written to the same name inside one archive and only "
                f"the last would be readable — the rest would be lost without "
                f"the run reporting a single missing image. Rename the {what}s "
                f"in the acquisition software so they differ by more than "
                f"punctuation or spacing, and export again."
            )
        realised = self.n_frames if self.single_image else self.size_t
        declared_t = int(self.declared_sizes.get("T", 0))
        if self.single_image and declared_t and realised and declared_t != realised:
            return (
                f"{subject} declares SizeT={declared_t} but the image series it "
                f"produced holds {realised} — its metadata and its pages disagree, "
                f"so which plane is which cannot be trusted. Re-export it from the "
                f"acquisition software."
            )
        return self.missing_plane_report(name)

    def missing_plane_report(self, name: str = "") -> str:
        """A user-facing explanation of the file's missing planes, or ``""``.

        Returns the empty string when every declared plane is backed by image
        data. Otherwise the message names the shortfall and the likeliest cause,
        because the three causes need different fixes. Missing companion files
        are recovered by putting them back beside the source. An acquisition that
        stopped early has to be re-exported with the dimensions it really holds.
        And a **broken index map** — ``TiffData`` entries pointing at IFDs the
        file does not have, or carrying a coordinate outside the declared sizes,
        which ``tifffile`` reports and then leaves as a hole — loses nothing at
        all: the pixels are in the file and only the metadata is wrong, so
        telling that user to re-export a shorter file would throw away data that
        is there. The tell is *unreferenced pages*: image data the map never
        points at.
        """
        if self.missing_planes <= 0:
            return ""
        subject = f"{name} is incomplete" if name else "This OME-TIFF is incomplete"
        where = ""
        if self.single_image:
            if self.first_missing_plane >= 0:
                # Plane, not frame: the planes are ordered by the file's own
                # DimensionOrder, so a plane index is a frame index only when
                # there is a single channel and a single Z.
                where = (" The first plane with no image data is number "
                         f"{self.first_missing_plane} of {self.total_planes}.")
        elif self.incomplete_positions:
            shown = ", ".join(str(p) for p in self.incomplete_positions[:6])
            more = (f" and {len(self.incomplete_positions) - 6} more"
                    if len(self.incomplete_positions) > 6 else "")
            where = f" Affected stage position(s): {shown}{more}."
        if self.missing_files:
            cause = (
                " Companion file(s) the metadata references are not in the same "
                f"folder as the source: {', '.join(self.missing_files)}. Put them "
                "back beside it and convert again."
            )
        elif self.unreferenced_pages > 0:
            cause = (
                f" No image data is missing: the file still holds "
                f"{self.unreferenced_pages} image plane(s) that its own index map "
                "never points at, so the fault is in the metadata rather than the "
                "pixels. Re-export or repair the OME-XML rather than re-running "
                "the acquisition."
            )
        else:
            cause = (
                " That usually means the acquisition was stopped before it "
                "finished, leaving the planned dimensions in the header. "
                "Re-export the file with the dimensions it actually holds, or "
                "trim the run to the planes that were written."
            )
        return (
            f"{subject}: its OME metadata declares {self.total_planes} image "
            f"plane(s) but {self.missing_planes} of them carry no image data, so "
            f"converting would write blank images indistinguishable from real "
            f"ones.{where}{cause}"
        )


@dataclass
class ExplodeResult:
    """A summary of an explode run, for logging and a pre-run preview."""

    wells: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    timepoints: list[str] = field(default_factory=list)
    fovs_per_well: int = 0
    images_written: int = 0
    # Wells a resumed run found already complete and did not rewrite. Reported
    # rather than merely skipped: a run that converted a tenth of a plate and
    # one that converted all of it otherwise print the same closing line.
    wells_resumed: list[str] = field(default_factory=list)


def _safe_token(value: object, fallback: str) -> str:
    """Filename-safe token: keep alphanumerics only, else *fallback*.

    The exploded filenames are split on the schema separator (``_``) downstream,
    so a channel name like ``"DAPI 405"`` or ``"GFP_2"`` must not carry spaces or
    separators into a token slot. Strips everything but ``[0-9A-Za-z]``.
    """
    tok = re.sub(r"[^0-9A-Za-z]+", "", str(value if value is not None else ""))
    return tok or fallback


# OME's ``UnitsTime`` enumeration, as hours per unit. Only the units an
# acquisition realistically writes are listed, plus the spellings this module
# accepted before (``minute``, and the bare ``m`` that OME itself does not
# define). ``µ`` is here twice: MICRO SIGN (U+00B5) is what OME's schema uses and
# GREEK SMALL LETTER MU (U+03BC) is what several writers emit instead, and they
# are different characters.
#
# Anything *not* in this table is unknown, and unknown is not a synonym for
# seconds. Treating it as seconds is how a file whose DeltaT is in milliseconds
# produced a "1000h" token for a frame taken one hour in — the token becomes the
# filename's timepoint field and the app's chronological key, so every hours axis
# in Review was scaled by 1000. An unreadable unit now leaves the timepoint
# tokens positional (``t0``, ``t1``, …), which orders correctly and states no
# number it cannot back up.

#: Named for the module so a converted OME-TIFF's warnings land in the run log
#: beside everything else the pipeline says about that file.
_log = logging.getLogger(__name__)

#: SI symbols whose meaning depends on their case, and which therefore must be
#: matched **before** the case-insensitive table below. OME's `UnitsTime`
#: enumeration contains both `Ms` (megasecond) and `ms` (millisecond);
#: lower-casing the lookup folded them together and read a megasecond as a
#: millisecond — off by a factor of 10^9, which is the same class of error, an
#: order of magnitude worse, that reading every unit as seconds produced.
_DELTA_T_HOURS_PER_SYMBOL = {
    "Ms": 1_000_000.0 / 3600.0,     # megasecond
    "ks": 1_000.0 / 3600.0,         # kilosecond
    "hs": 100.0 / 3600.0,           # hectosecond
    "das": 10.0 / 3600.0,           # decasecond
    "ds": 0.1 / 3600.0,             # decisecond
    "cs": 0.01 / 3600.0,            # centisecond
    "ms": 1.0 / 3_600_000.0,        # millisecond
}

_DELTA_T_HOURS_PER_UNIT = {
    "ms": 1.0 / 3_600_000.0,
    "millisecond": 1.0 / 3_600_000.0,
    "milliseconds": 1.0 / 3_600_000.0,
    "µs": 1.0 / 3_600_000_000.0,
    "μs": 1.0 / 3_600_000_000.0,
    "us": 1.0 / 3_600_000_000.0,
    "microsecond": 1.0 / 3_600_000_000.0,
    "microseconds": 1.0 / 3_600_000_000.0,
    "ns": 1.0 / 3.6e12,
    "nanosecond": 1.0 / 3.6e12,
    "nanoseconds": 1.0 / 3.6e12,
    "s": 1.0 / 3600.0,
    "sec": 1.0 / 3600.0,
    "secs": 1.0 / 3600.0,
    "second": 1.0 / 3600.0,
    "seconds": 1.0 / 3600.0,
    "min": 1.0 / 60.0,
    "mins": 1.0 / 60.0,
    "minute": 1.0 / 60.0,
    "minutes": 1.0 / 60.0,
    "m": 1.0 / 60.0,
    "h": 1.0,
    "hr": 1.0,
    "hrs": 1.0,
    "hour": 1.0,
    "hours": 1.0,
    "d": 24.0,
    "day": 24.0,
    "days": 24.0,
}


def _localname(tag: str) -> str:
    """Strip an XML namespace (``{ns}Local`` → ``Local``)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _hours_token(hours: float) -> str:
    """``"<hours>h"`` in a spelling ``util.timepoint`` can read, or ``""``.

    Six significant figures in **fixed** notation. ``f"{v:g}"`` switches to
    exponent form below 1e-4, which was rare while every DeltaT was read as
    seconds and became routine once milliseconds, microseconds and nanoseconds
    were added: a six-frame time-lapse 100 ms apart rendered
    ``2.77778e-05h``, and ``util.timepoint._UNIT_RE`` matches no exponent, so
    ``parse_hours`` answered ``None`` and ``sort_key`` fell back to sorting
    those tokens as text — putting frames 5 and 6 before frames 2 to 4. A
    number the parser cannot read is worse than the wrong number it replaced,
    because the wrong one at least ordered correctly.

    Answers ``""`` for a value the parser cannot represent at all: not finite,
    or negative (``_UNIT_RE`` accepts no sign, and a writer stamping a
    pre-treatment frame at a negative elapsed time is the case that produces
    one). The caller reads that as a reason to name every timepoint by
    position, and says so.
    """
    if not math.isfinite(hours) or hours < 0.0:
        return ""
    if hours == 0.0:
        return "0h"
    # Decimal places chosen so six significant figures survive, capped: below
    # a microsecond in hours the label is more zeros than number, and two such
    # timepoints colliding is caught by the duplicate check either way.
    places = min(12, max(0, 5 - int(math.floor(math.log10(hours)))))
    text = f"{hours:.{places}f}".rstrip("0").rstrip(".")
    return f"{text}h" if text and text != "0" else "0h"


def _parse_ome_labels(
    ome_xml: str | None, size_c: int, size_t: int
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(channel_names, timepoint_tokens, raw_channel_names)`` from OME-XML.

    Channel names come from the first ``Image``'s ``Channel`` elements (channels
    are the same across positions in a plate scan). Timepoint tokens come from
    the per-plane ``DeltaT`` when present — rendered as ``"<hours>h"`` so the
    app's timepoint parser reads them chronologically — otherwise positional
    ``t0``, ``t1``, …. Any parse failure falls back cleanly to positional tokens
    for both, so a malformed or absent metadata block never aborts the explode.
    """
    channel_names = [f"c{i}" for i in range(size_c)]
    timepoint_tokens = [f"t{i}" for i in range(size_t)]
    raw_names: list[str] = []
    if not ome_xml:
        return channel_names, timepoint_tokens, raw_names

    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return channel_names, timepoint_tokens, raw_names

    image = next((el for el in root.iter() if _localname(el.tag) == "Image"), None)
    if image is None:
        return channel_names, timepoint_tokens, raw_names

    pixels = next(
        (el for el in image.iter() if _localname(el.tag) == "Pixels"), None
    )
    if pixels is None:
        return channel_names, timepoint_tokens, raw_names

    # Channel names, in channel order.
    names: list[str] = []
    for el in pixels:
        if _localname(el.tag) == "Channel":
            names.append(el.get("Name") or "")
    if names:
        raw_names = [str(names[i]) if i < len(names) else "" for i in range(size_c)]
        channel_names = [
            _safe_token(names[i] if i < len(names) else "", f"c{i}")
            for i in range(size_c)
        ]

    # Timepoints from DeltaT on the T-index planes, converted to hours. A plane
    # whose unit cannot be converted contributes nothing, so ``delta_by_t``
    # comes up short of ``size_t`` and every token stays positional.
    delta_by_t: dict[int, float] = {}
    unreadable_units: set[str] = set()
    unreadable_planes = 0
    out_of_range = 0
    for el in pixels:
        if _localname(el.tag) != "Plane":
            continue
        the_t = el.get("TheT")
        delta = el.get("DeltaT")
        if the_t is None or delta is None:
            continue
        try:
            ti = int(the_t)
            value = float(delta)
        except ValueError:
            continue
        # ``TheT`` is a zero-based index into the T axis, and a writer that
        # stamps it one-based is the common deviation. Unchecked, those keys
        # still satisfy the ``len(delta_by_t) >= size_t`` test below while
        # attaching every elapsed time to the wrong array index — a file with
        # TheT 1,2,3 and SizeT 3 rendered ``['t0', '0h', '1h']``, mixing the two
        # vocabularies and giving two timepoints the same 0 h reading, with
        # nothing saying so.
        if not (0 <= ti < size_t):
            out_of_range += 1
            continue
        # OME's default for DeltaT is seconds, so an *absent* unit is seconds.
        # An unrecognised one is not: it is a unit this module cannot convert.
        unit = (el.get("DeltaTUnit") or "s").strip()
        # Case-sensitive symbols first: `Ms` and `ms` are different units and
        # only their case tells them apart.
        per_hour = _DELTA_T_HOURS_PER_SYMBOL.get(unit)
        if per_hour is None and unit.lower() not in _DELTA_T_HOURS_PER_SYMBOL:
            # Only fall through to the case-insensitive table when the unit is
            # not a *case-significant* symbol in some other spelling. ``"MS"``
            # is neither of OME's two: lower-cased it becomes the millisecond,
            # which is the exact 10^9 fold this table exists to prevent, and
            # silently picking the smaller of two units a spelling could mean
            # is worse than declining to read it.
            per_hour = _DELTA_T_HOURS_PER_UNIT.get(unit.lower())
        if per_hour is None:
            unreadable_units.add(unit)
            unreadable_planes += 1
            continue
        delta_by_t.setdefault(ti, value * per_hour)

    # Every route to a positional token is a *degradation*: the file stated a
    # time and the archive will not carry it, so Review orders the timepoints by
    # index and can put no number on an axis. Each one says so, because a
    # ``t0``/``t1`` plate is otherwise indistinguishable from one whose writer
    # recorded no DeltaT at all.
    #
    # The warnings live *inside* the branch that actually falls back. Reporting
    # an unreadable unit wherever one was seen said "the timepoints are named by
    # position" about a file whose timepoints had resolved perfectly — one
    # channel's planes carrying a bad unit while another's carried seconds is
    # enough — and a log line asserting data loss that did not happen sends a
    # user to re-export a plate for nothing.
    complete = bool(size_t) and len(delta_by_t) >= size_t
    from_delta = (
        [_hours_token(delta_by_t[i]) for i in range(size_t)] if complete else []
    )
    # ``%g`` renders six significant figures, so two deltas that differ by less
    # than that — or a writer that stamps the same placeholder on every plane —
    # produce one token for two timepoints, and every image of the second would
    # be written over the first's member name. Positional tokens name them all
    # distinctly and lose only a number the file did not really state.
    collided = sorted({t for t in from_delta if from_delta.count(t) > 1})
    unrenderable = [i for i, t in enumerate(from_delta) if not t]

    if complete and not collided and not unrenderable:
        timepoint_tokens = from_delta
    elif size_t:
        why: list[str] = []
        if unreadable_units:
            why.append(
                f"{unreadable_planes} plane(s) carry a DeltaTUnit this "
                f"converter cannot read ({', '.join(sorted(unreadable_units))})")
        if out_of_range:
            why.append(
                f"{out_of_range} plane(s) carry a TheT outside "
                f"0..{size_t - 1}, so the file's T indexing is not this "
                f"converter's")
        if complete and collided:
            why.append(
                f"the elapsed times render to {len(set(from_delta))} distinct "
                f"label(s) for {len(from_delta)} timepoint(s) — "
                f"{', '.join(collided)} repeat(s), and two timepoints sharing "
                f"a label would write every image of the second over the first")
        if complete and unrenderable:
            why.append(
                f"{len(unrenderable)} elapsed time(s) are not finite or not "
                f"positive, and the app's timepoint parser reads neither")
        if not complete and not why:
            why.append(
                f"only {len(delta_by_t)} of {size_t} timepoint(s) carry a "
                f"readable DeltaT")
        _log.warning(
            "OME timepoints: naming by position (t0, t1, …) rather than by "
            "elapsed time — %s.", "; ".join(why),
        )

    return channel_names, timepoint_tokens, raw_names

    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return channel_names, timepoint_tokens, raw_names

    image = next((el for el in root.iter() if _localname(el.tag) == "Image"), None)
    if image is None:
        return channel_names, timepoint_tokens, raw_names

    pixels = next(
        (el for el in image.iter() if _localname(el.tag) == "Pixels"), None
    )
    if pixels is None:
        return channel_names, timepoint_tokens, raw_names

    # Channel names, in channel order.
    names: list[str] = []
    for el in pixels:
        if _localname(el.tag) == "Channel":
            names.append(el.get("Name") or "")
    if names:
        raw_names = [str(names[i]) if i < len(names) else "" for i in range(size_c)]
        channel_names = [
            _safe_token(names[i] if i < len(names) else "", f"c{i}")
            for i in range(size_c)
        ]

    # Timepoints from DeltaT on the T-index planes, converted to hours through
    # ``_DELTA_T_HOURS_PER_UNIT``. A plane whose unit is not in that table
    # contributes nothing, so ``delta_by_t`` comes up short of ``size_t`` and
    # every token stays positional.
    delta_by_t: dict[int, float] = {}
    unreadable_units: set[str] = set()
    unreadable_planes = 0
    for el in pixels:
        if _localname(el.tag) != "Plane":
            continue
        the_t = el.get("TheT")
        delta = el.get("DeltaT")
        if the_t is None or delta is None:
            continue
        try:
            ti = int(the_t)
            value = float(delta)
        except ValueError:
            continue
        # OME's default for DeltaT is seconds, so an *absent* unit is seconds.
        # An unrecognised one is not: it is a unit this module cannot convert,
        # and the plane is left without a delta, which drops the whole file back
        # to positional tokens below.
        unit = (el.get("DeltaTUnit") or "s").strip()
        # Case-sensitive symbols first: `Ms` and `ms` are different units and
        # only their case tells them apart.
        per_hour = _DELTA_T_HOURS_PER_SYMBOL.get(unit)
        if per_hour is None and unit.lower() not in _DELTA_T_HOURS_PER_SYMBOL:
            # Only fall through to the case-insensitive table when the unit is
            # not a *case-significant* symbol in some other spelling. ``"MS"``
            # is neither of OME's two: lower-cased it becomes the millisecond,
            # which is the exact 10^9 fold this table exists to prevent, and
            # silently picking the smaller of two units a spelling could mean
            # is worse than declining to read it.
            per_hour = _DELTA_T_HOURS_PER_UNIT.get(unit.lower())
        if per_hour is None:
            unreadable_units.add(unit)
            unreadable_planes += 1
            continue
        delta_by_t.setdefault(ti, value * per_hour)

    # Every route to a positional token is a *degradation*: the file stated a
    # time and the archive will not carry it, so Review orders the timepoints by
    # index and can put no number on an axis. Each one says so, because a
    # ``t0``/``t1`` plate is otherwise indistinguishable from one whose writer
    # recorded no DeltaT at all.
    if unreadable_units:
        _log.warning(
            "OME timepoints: %d plane(s) carry a DeltaTUnit this converter "
            "cannot read (%s), so the timepoints are named by position (t0, "
            "t1, …) rather than by elapsed time.",
            unreadable_planes, ", ".join(sorted(unreadable_units)),
        )
    if size_t and delta_by_t and len(delta_by_t) < size_t:
        _log.warning(
            "OME timepoints: only %d of %d timepoint(s) carry a readable "
            "DeltaT, so all of them are named by position (t0, t1, …).",
            len(delta_by_t), size_t,
        )

    if delta_by_t and len(delta_by_t) >= size_t:
        from_delta = [
            f"{delta_by_t[i]:g}h" if i in delta_by_t else f"t{i}"
            for i in range(size_t)
        ]
        # ``%g`` renders six significant figures, so two deltas that differ by
        # less than that — or a writer that stamps the same placeholder on every
        # plane — produce one token for two timepoints, and every image of the
        # second would be written over the first's member name. Positional
        # tokens name them all distinctly and lose only a number the file did
        # not really state, which is the same trade the unreadable-unit branch
        # above already takes.
        if len(set(from_delta)) == len(from_delta):
            timepoint_tokens = from_delta
        else:
            collided = sorted({t for t in from_delta if from_delta.count(t) > 1})
            _log.warning(
                "OME timepoints: the elapsed times render to %d distinct "
                "label(s) for %d timepoint(s) — %s repeat(s) — so the "
                "timepoints are named by position (t0, t1, …) rather than by "
                "elapsed time. Two timepoints sharing a label would write "
                "every image of the second over the first.",
                len(set(from_delta)), len(from_delta), ", ".join(collided),
            )

    return channel_names, timepoint_tokens, raw_names


# ``tifffile`` sizes a series' page list from the dimensions the *metadata*
# declares and leaves a slot ``None`` wherever the file carries no IFD for it —
# an acquisition stopped early, or a multi-file OME whose companion files are
# absent. Its own ``series.asarray()`` zero-fills those slots ("Missing data are
# zeroed"); this module reads pages individually, so a gap surfaced as
# ``AttributeError: 'NoneType' object has no attribute 'asarray'`` mid-run, after
# the wells before it had already been written. Only these three series builders
# can produce a gap, so only they are worth the scan.
_SPARSE_SERIES_KINDS = ("ome", "mmstack", "ndtiff")


def series_page_gaps(series) -> list[int]:
    """The indices of the page slots *series* declares that hold no image data.

    Empty for a complete series, and empty for any series kind that cannot carry
    a gap (see :data:`_SPARSE_SERIES_KINDS`) — which also keeps the scan off the
    contiguous-series path, where indexing every slot would walk the whole IFD
    chain of a file this converter otherwise reads a plane at a time.
    """
    if str(getattr(series, "kind", "")).lower() not in _SPARSE_SERIES_KINDS:
        return []
    gaps: list[int] = []
    for i in range(len(series.pages)):
        try:
            page = series.pages[i]
        except IndexError:
            page = None  # declared past the end of what the file holds
        if page is None:
            gaps.append(i)
    return gaps


@contextmanager
def _captured_tifffile_warnings():
    """Collect what ``tifffile`` logs while the block runs, as a list of strings.

    ``tifffile`` diagnoses every fault this module cares about — an invalid page
    offset, a ``TiffData`` index out of range, a companion file it could not
    read, frames it had to zero — and reports them through its logger, which
    nothing here was attached to. It is the file's own account of what is wrong
    with it, and it costs nothing to keep.
    """
    messages: list[str] = []
    # Only this thread's records. The logger is global and shared, so a page
    # reading a file on the UI thread while a conversion reads another on a
    # worker would otherwise each collect both — and ``validation_report``
    # quotes ``warnings[0]`` as the closing sentence of a refusal, so one file's
    # refusal could end by describing what is wrong with a different file.
    owner = threading.get_ident()

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if threading.get_ident() == owner:
                messages.append(record.getMessage())

    handler = _Collect()
    logger = logging.getLogger("tifffile")
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)


def _unreferenced_page_count(tf, series) -> int:
    """Physical IFDs in *tf* that no slot of *series* points at.

    Distinguishes the two shapes a hole in the index map can take. An
    acquisition that stopped early has fewer pages than the metadata declares
    and every page it does have is referenced, so this is zero. A file whose
    ``TiffData`` entries point at IFDs it does not have — or carry a coordinate
    outside the declared sizes, which ``tifffile`` drops — has the pixels on
    disk with nothing pointing at them, and this counts them.
    """
    mapped: set[int] = set()
    for s in series:
        if str(getattr(s, "kind", "")).lower() not in _SPARSE_SERIES_KINDS:
            return 0
        for i in range(len(s.pages)):
            try:
                page = s.pages[i]
            except IndexError:
                continue
            if page is not None:
                mapped.add(int(page.index))
    return max(0, len(tf.pages) - len(mapped))


def _declared_sizes(ome_xml: str | None) -> dict[str, int]:
    """``{"T": …, "C": …, "Z": …}`` as the OME-XML's first ``Pixels`` states them.

    Read from the document rather than from the series so the two can be
    compared: ``tifffile`` silently produces whatever series the *pages* support,
    which for a damaged file can be a fraction of what the metadata claims.
    """
    if not ome_xml:
        return {}
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return {}
    pixels = next((el for el in root.iter() if _localname(el.tag) == "Pixels"), None)
    if pixels is None:
        return {}
    out: dict[str, int] = {}
    for ax in ("T", "C", "Z"):
        raw = pixels.get(f"Size{ax}")
        if raw is None:
            continue
        try:
            out[ax] = int(raw)
        except ValueError:
            continue
    return out


# Length units an OME file may state for a stage position, as micrometres each.
# "reference frame" is deliberately absent: it is OME's default and means the
# microscope's own frame, which has no defined size.
_LENGTH_UNITS_UM = {
    "m": 1e6, "dm": 1e5, "cm": 1e4, "mm": 1e3,
    "µm": 1.0, "um": 1.0, "micron": 1.0, "micrometer": 1.0, "micrometre": 1.0,
    "nm": 1e-3, "pm": 1e-6, "Å": 1e-4, "angstrom": 1e-4,
    "in": 25400.0, "inch": 25400.0, "mi": 1.609344e9, "ft": 304800.0,
    "yd": 914400.0, "thou": 25.4, "li": 201168000.0,
}


def _unit_to_um(raw: str | None) -> float | None:
    """Micrometres per *raw* unit, or ``None`` if it is not a stated length."""
    if not raw:
        return None
    return _LENGTH_UNITS_UM.get(raw.strip()) or _LENGTH_UNITS_UM.get(
        raw.strip().lower())


def _stage_positions(ome_xml: str | None) -> tuple[list[tuple[float, float]], bool]:
    """``(x, y)`` per ``Plane``, in document order, for layout inference.

    Only planes carrying both coordinates are returned, and within one
    ``<Image>`` each ``(TheZ, TheC)`` combination collapses onto its ``TheT`` —
    the stage does not move between a plane's channels or Z-slices, so one
    coordinate per frame is what a caller counting *positions* wants.

    That collapse is **per Image**, not per document. A multi-image plate scan
    has one ``<Image>`` per stage position and every one of them numbers its
    planes from ``TheT="0"``, so a document-wide key keeps the first Image and
    throws the other ninety-five away — leaving one coordinate for a whole
    plate, or, for a multi-image timelapse, several coordinates that are all the
    same position.

    Returns ``(positions, in_micrometres)``. Coordinates are converted to
    micrometres whenever **every** plane states a length unit for both axes, and
    left exactly as written otherwise — a consumer comparing distances within
    one file needs no scale, but one asking "which well is this?" of a scattered
    selection cannot answer without one. Mixed or missing units are reported as
    not-converted rather than guessed: a scale that is wrong is worse than none,
    because it is the thing everything downstream then trusts.
    """
    if not ome_xml:
        return [], False
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return [], False
    order: list[tuple[float, float]] = []
    scales: list[float | None] = []
    images = [el for el in root.iter() if _localname(el.tag) == "Image"]
    for image in images or [root]:
        seen: set[int] = set()
        for el in image.iter():
            if _localname(el.tag) != "Plane":
                continue
            x, y = el.get("PositionX"), el.get("PositionY")
            if x is None or y is None:
                continue
            try:
                xy = (float(x), float(y))
            except ValueError:
                continue
            sx = _unit_to_um(el.get("PositionXUnit"))
            sy = _unit_to_um(el.get("PositionYUnit"))
            scale = sx if sx is not None and sx == sy else None
            the_t = el.get("TheT")
            try:
                key = int(the_t) if the_t is not None else len(seen)
            except ValueError:
                key = len(seen)
            if key in seen:
                continue
            seen.add(key)
            order.append(xy)
            scales.append(scale)
    if order and all(s is not None for s in scales) and len(set(scales)) == 1:
        factor = scales[0]
        return [(x * factor, y * factor) for x, y in order], True
    return order, False


# Vendor blocks ``tifffile`` parses in their own right, in the order they are
# worth trying. An OME-TIFF written by acquisition software usually carries the
# instrument's *original* metadata alongside the OME-XML — Bio-Formats copies it
# into ``StructuredAnnotations`` as ``OriginalMetadata`` key/value pairs, and
# tifffile parses the vendor's own tags separately — and the stage coordinates
# live there for plenty of files whose OME ``<Plane>`` elements carry none.
_VENDOR_BLOCKS = ("micromanager_metadata", "imagej_metadata", "metaseries_metadata",
                  "stk_metadata", "fluoview_metadata", "lsm_metadata",
                  "scanimage_metadata", "sis_metadata", "andor_metadata")

# A stage coordinate's key, as the instruments spell it. Vendors differ on word
# order, separators and whether a unit is appended, and the *stem* in front of
# the axis is what pairs an X with its own Y — "Position 3 XPos" belongs with
# "Position 3 YPos" and with nothing else.
_AXIS_KEY = re.compile(
    r"^(?P<stem>.*?)[\s_.\-]*"
    r"(?:(?P<pos>position|stage)[\s_.\-]*(?P<axis1>[xy])"
    r"|(?P<axis2>[xy])[\s_.\-]*(?:position|pos|stage)?)"
    r"(?:[\s_.\-]*[\(\[]?(?:um|µm|microns?|mm)[\)\]]?)?$",
    re.IGNORECASE,
)


def _flatten_metadata(value, prefix: str = "", out: dict | None = None) -> dict:
    """Flatten a nested vendor metadata mapping into ``{"a.b.c": leaf}``.

    Vendor blocks arrive as arbitrarily nested dicts and lists, and the keys
    worth finding sit at unpredictable depths. Flattening once here means the
    matchers below can be simple string tests rather than a walker each.
    """
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, sub in value.items():
            _flatten_metadata(sub, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _flatten_metadata(sub, f"{prefix}[{i}]", out)
    elif prefix:
        out[prefix] = value
    return out


def _original_metadata(ome_xml: str | None) -> dict:
    """Bio-Formats' copy of the instrument's own metadata, as ``{key: value}``.

    It writes the original file's metadata into the OME-XML as
    ``<OriginalMetadata><Key>…</Key><Value>…</Value></OriginalMetadata>``
    annotations, which is where the stage coordinates end up for files whose
    ``<Plane>`` elements have none.
    """
    if not ome_xml:
        return {}
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return {}
    out: dict = {}
    for el in root.iter():
        if _localname(el.tag) != "OriginalMetadata":
            continue
        key = next((c.text for c in el if _localname(c.tag) == "Key"), None)
        val = next((c.text for c in el if _localname(c.tag) == "Value"), None)
        if key:
            out[str(key)] = val
    return out


def _paired_coordinates(flat: dict) -> list[tuple[float, float]]:
    """``(x, y)`` per position from a flattened metadata mapping, or ``[]``.

    Pairs an X key with the Y that shares its stem — the same path in a nested
    block, or the same prefix in a flat one — so a per-position list keeps its
    order and a stray coordinate elsewhere in the block cannot pull a pair out
    of line. Nothing is returned below two complete pairs: one describes no
    layout, and a partial set is worse than none.
    """
    found: dict[str, dict[str, float]] = {}
    order: list[str] = []
    for key, value in flat.items():
        head, _, leaf = str(key).rpartition(".")
        match = _AXIS_KEY.match(leaf)
        if match is None:
            continue
        axis = (match.group("axis1") or match.group("axis2") or "").lower()
        if axis not in ("x", "y"):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        stem = f"{head}.{match.group('stem')}" if head else match.group("stem")
        if stem not in found:
            found[stem] = {}
            order.append(stem)
        found[stem][axis] = number
    pairs = [(found[s]["x"], found[s]["y"]) for s in order
             if "x" in found[s] and "y" in found[s]]
    return pairs if len(pairs) >= 2 else []


def _vendor_positions(tf, ome_xml: str | None) -> list[tuple[float, float]]:
    """Stage coordinates from the instrument's own metadata, or ``[]``.

    Tried only when the OME ``<Plane>`` elements carry none — which is common,
    since the OME-XML a converter writes need not repeat what the original file
    already recorded.
    """
    sources: list[dict] = [_original_metadata(ome_xml)]
    for name in _VENDOR_BLOCKS:
        block = getattr(tf, name, None)
        if block:
            sources.append(_flatten_metadata(block))
    for flat in sources:
        pairs = _paired_coordinates(flat)
        if pairs:
            return pairs
    return []


# How many keys to keep as a sample. Enough to recognise a vendor's spelling of
# a stage coordinate, few enough to paste into a message.
_KEY_SAMPLE = 40


def _metadata_inventory(tf, ome_xml: str | None) -> tuple[dict, list]:
    """``({source: key count}, [sample keys])`` for everything the file carries.

    Diagnostic only. A file that yields no layout should be able to say what it
    *does* hold — which blocks are present, how large they are, and what their
    keys look like — because "no stage coordinates found" and "looked in the
    wrong place" are otherwise the same sentence, and telling them apart
    currently needs the file itself.
    """
    blocks: dict[str, int] = {}
    sample: list[str] = []

    def _take(name: str, flat: dict) -> None:
        if not flat:
            return
        blocks[name] = len(flat)
        for key in list(flat)[:_KEY_SAMPLE]:
            if len(sample) < _KEY_SAMPLE:
                sample.append(f"{name}: {key}")

    if ome_xml:
        blocks["ome_xml_chars"] = len(ome_xml)
    _take("original_metadata", _flatten_metadata(_original_metadata(ome_xml)))
    for name in _VENDOR_BLOCKS:
        with_block = getattr(tf, name, None)
        if with_block:
            _take(name, _flatten_metadata(with_block))
    return blocks, sample


def _position_names(ome_xml: str | None) -> list[str]:
    """One name per ``<Image>``: its ``<StageLabel>``'s, else the Image's own.

    Acquisition software routinely writes the well into these — ``"B02"``,
    ``"Well B2, Site 1"``, ``"scan_B02_f0"`` — which is the only thing in an
    OME-TIFF that says where on the *plate* a position was, as opposed to where
    on the stage. Returned raw; reading a well out of one is the caller's job.
    """
    if not ome_xml:
        return []
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return []
    names: list[str] = []
    for image in (el for el in root.iter() if _localname(el.tag) == "Image"):
        label = next((el for el in image.iter()
                      if _localname(el.tag) == "StageLabel"), None)
        name = (label.get("Name") if label is not None else None) or image.get("Name")
        names.append(str(name or ""))
    return names


def _referenced_filenames(ome_xml: str | None) -> list[str]:
    """The companion filenames the OME-XML's ``UUID`` elements point at.

    A multi-file OME-TIFF stores its planes across several files and names them
    in the master's ``TiffData``/``UUID`` elements. Order-preserving and
    de-duplicated; an unparseable document yields nothing, since this only ever
    sharpens an error message.
    """
    if not ome_xml:
        return []
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError:
        return []
    names: list[str] = []
    for el in root.iter():
        if _localname(el.tag) != "UUID":
            continue
        fname = el.get("FileName")
        if fname and fname not in names:
            names.append(fname)
    return names


def _missing_companion_files(ome_xml: str | None, ome_path: Path) -> list[str]:
    """Companion files the OME-XML names that are not beside *ome_path*."""
    directory = ome_path.parent
    return [
        name for name in _referenced_filenames(ome_xml)
        if name.lower() != ome_path.name.lower()
        and not (directory / name).exists()
    ]


def read_ome_layout(ome_path: Path | str) -> OmeLayout:
    """Open *ome_path* and read its position/T/C/Z sizes and metadata labels.

    A multi-image file (several OME ``<Image>`` elements → several ``tifffile``
    series) has one series per stage position, and ``size_t`` is the timepoints
    per position. A single-image file has one series whose ``T`` axis packs every
    stage position into a flat frame sequence — ``single_image`` is True and
    ``n_frames`` is that ``SizeT`` (the split into positions × timepoints is
    decided at explode time). Sizes are taken from the first series (a plate scan
    is dimensionally uniform).

    The same pass audits the file — see :meth:`OmeLayout.validation_report` —
    so a conversion can refuse an unsound one up front rather than failing
    part-way through, or, worse, writing images that look like data. Everything
    it collects is metadata: page offsets, sizes and the OME-XML. No pixels are
    read, so the cost is one file open however large the plate is.
    """
    ome_path = Path(ome_path)
    with _captured_tifffile_warnings() as warnings:
        with tifffile.TiffFile(str(ome_path)) as tf:
            series = list(tf.series)
            if not series:
                raise ValueError(f"{ome_path.name} contains no image series.")
            axes_per_series = [str(s.axes).upper() for s in series]
            axes0 = axes_per_series[0]
            shape0 = series[0].shape

            def _sizes_of(ser, ax_str: str) -> dict[str, int]:
                shp = ser.shape
                return {
                    ax: (int(shp[ax_str.index(ax)]) if ax in ax_str else 1)
                    for ax in ("T", "C", "Z")
                }

            sizes_per_series = [
                _sizes_of(ser, ax) for ser, ax in zip(series, axes_per_series)
            ]

            def _size(ax: str) -> int:
                return int(shape0[axes0.index(ax)]) if ax in axes0 else 1

            size_t, size_c, size_z = _size("T"), _size("C"), _size("Z")
            ome_xml = tf.ome_metadata
            series_kind = str(getattr(series[0], "kind", "") or "")
            vendor_positions = _vendor_positions(tf, ome_xml)
            blocks, key_sample = _metadata_inventory(tf, ome_xml)
            gaps = [series_page_gaps(s) for s in series]
            total_planes = sum(len(s.pages) for s in series)
            unreferenced = _unreferenced_page_count(tf, series)

    single_image = len(series) == 1
    # For a single-image file the T axis is the frame sequence, not timepoints, so
    # don't manufacture size_t timepoint tokens (there could be thousands).
    channel_names, timepoint_tokens, raw_channel_names = _parse_ome_labels(
        ome_xml, size_c, 0 if single_image else size_t
    )
    ome_positions, positions_in_um = _stage_positions(ome_xml)
    return OmeLayout(
        n_positions=len(series),
        size_t=0 if single_image else size_t,
        size_c=size_c,
        size_z=size_z,
        channel_names=channel_names,
        timepoint_tokens=timepoint_tokens,
        axes_per_series=axes_per_series,
        sizes_per_series=sizes_per_series,
        single_image=single_image,
        n_frames=size_t if single_image else 0,
        total_planes=total_planes,
        missing_planes=sum(len(g) for g in gaps),
        incomplete_positions=[i for i, g in enumerate(gaps) if g],
        first_missing_plane=next((g[0] for g in gaps if g), -1),
        missing_files=_missing_companion_files(ome_xml, ome_path),
        named_ome=ome_path.name.lower().endswith((".ome.tif", ".ome.tiff")),
        has_ome_xml=bool(ome_xml),
        series_kind=series_kind,
        declared_sizes=_declared_sizes(ome_xml),
        unreferenced_pages=unreferenced,
        warnings=list(warnings),
        stage_positions=(ome_positions or vendor_positions),
        # Only the OME planes carry a unit. A vendor block's coordinates are in
        # whatever the instrument writes, so a scale read from the OME half must
        # not be claimed for the fallback half.
        stage_positions_in_um=(positions_in_um and bool(ome_positions)),
        position_names=_position_names(ome_xml),
        metadata_blocks=blocks,
        metadata_key_sample=key_sample,
        raw_channel_names=raw_channel_names,
    )


def _extract_plane(arr: np.ndarray, axes: str, t: int, c: int) -> np.ndarray:
    """Return the 2-D (Y, X) plane at timepoint *t*, channel *c*, Z max-projected.

    Indexes the T and C axes (when present), keeps Y/X/Z, and takes index 0 of
    any other axis (e.g. a sample axis). Z is collapsed by maximum projection;
    any residual leading axes are defensively reduced to leave a 2-D result.

    An out-of-range *t* or *c* **raises**, as :func:`_packed_plane` already does
    for a page it cannot find. It used to clamp with ``min(…, shape - 1)``, and
    the caller asks for ``t`` in ``range(layout.size_t)`` where ``size_t`` is
    read from **series[0] only** — so a position that legitimately holds fewer
    timepoints (an acquisition stopped early at one well) had its last frame
    written out again under every later timepoint's name. Quantified as a real
    second timepoint, that is a fold-change over time of exactly 1.000, and
    nothing distinguishes it from a measured null. A series with no T axis at
    all was worse: every timepoint got the same frame.
    """
    axes = axes.upper()
    sl: list = [slice(None)] * arr.ndim
    for pos, ax in enumerate(axes):
        if ax == "T":
            if t >= arr.shape[pos]:
                raise ValueError(
                    f"timepoint {t} requested from a series that holds "
                    f"{arr.shape[pos]} (axes {axes!r})")
            sl[pos] = t
        elif ax == "C":
            if c >= arr.shape[pos]:
                raise ValueError(
                    f"channel {c} requested from a series that holds "
                    f"{arr.shape[pos]} (axes {axes!r})")
            sl[pos] = c
        elif ax in ("Z", "Y", "X"):
            continue
        else:
            sl[pos] = 0
    # A series with no T (or C) axis at all reaches neither branch above, so
    # every timepoint would resolve to the same single plane — the same silent
    # duplication by a different route.
    if t and "T" not in axes:
        raise ValueError(
            f"timepoint {t} requested from a series with no T axis "
            f"(axes {axes!r})")
    if c and "C" not in axes:
        raise ValueError(
            f"channel {c} requested from a series with no C axis "
            f"(axes {axes!r})")
    sub = arr[tuple(sl)]

    remaining = [ax for pos, ax in enumerate(axes) if isinstance(sl[pos], slice)]
    if "Z" in remaining:
        sub = np.max(sub, axis=remaining.index("Z"))
        remaining.pop(remaining.index("Z"))
    sub = np.asarray(sub)
    while sub.ndim > 2:
        sub = sub[0]
    return sub


def _packed_plane(series, axes: str, frame: int, c: int) -> np.ndarray:
    """Read one 2-D plane (T=*frame*, C=*c*, Z max-projected) from a series' pages.

    A single-image packed file can be far too large to ``asarray`` whole, so the
    plane is read a page at a time. Pages are ordered by the series' leading axes
    (everything but Y/X), first axis slowest — so the flat page index is the
    mixed-radix ravel of the (T, C, Z) indices against those axis sizes, honouring
    whatever order the file's ``DimensionOrder`` produced.
    """
    axes = axes.upper()
    leading = [a for a in axes if a not in "YX"]
    sizes = {a: int(series.shape[axes.index(a)]) for a in leading}

    def _page_index(idx: dict[str, int]) -> int:
        pi = 0
        for a in leading:
            pi = pi * sizes[a] + idx.get(a, 0)
        return pi

    def _page(z: int):
        """The page at (frame, c, z), or a stated failure — never a silent gap."""
        page = series.pages[_page_index({"T": frame, "C": c, "Z": z})]
        if page is None:
            # A slot the metadata declares but the file has no data for. The
            # explode refuses such a file up front (``OmeLayout.missing_planes``),
            # so reaching here means the gap appeared after that check; say what
            # is missing rather than raising on ``None.asarray``.
            raise ValueError(
                f"This OME-TIFF declares a plane at frame {frame}, channel {c}, "
                f"Z {z} that carries no image data — the file is incomplete."
            )
        return page

    nz = sizes.get("Z", 1)
    if "Z" in leading and nz > 1:
        stack = [np.asarray(_page(z).asarray()) for z in range(nz)]
        return np.max(np.stack(stack), axis=0)
    plane = np.asarray(_page(0).asarray())
    while plane.ndim > 2:
        plane = plane[0]
    return plane


def _resolve_positions(
    total: int, n_wells: int, fovs_per_well: int | None, *, what: str, name: str,
) -> int:
    """Return the validated FOVs-per-well for *total* positions/frames.

    ``None`` derives it (``total`` must be an exact multiple of *n_wells*); a
    given value is cross-checked against ``n_wells * fovs_per_well == total``.
    """
    if fovs_per_well is None:
        if total % n_wells != 0:
            raise ValueError(
                f"{name} has {total} {what}, which is not divisible by the "
                f"well grid ({n_wells} well(s)). Fields-of-view per well must be "
                f"a whole number — check the grid dimensions."
            )
        return total // n_wells
    if n_wells * fovs_per_well != total:
        raise ValueError(
            f"{name} has {total} {what}, but the {n_wells} well(s) × "
            f"{fovs_per_well} FOV(s) per well = {n_wells * fovs_per_well}. "
            f"Check the grid dimensions and the FOVs-per-well value."
        )
    return fovs_per_well


def _expected_members(experiment: str, channels, well: str, fovs: int,
                      timepoints) -> set[str]:
    """Every member name a finished ``<well>.zip`` holds, for these settings.

    The exact set, not a count: an archive left by a run with a different grid
    holds the right *number* of images under the wrong names, and resuming onto
    it would leave a dataset stitched from two different well mappings.
    """
    return {
        f"{experiment}_{channel}_{well}_f{fov}_{tp}.tif"
        for channel in channels
        for fov in range(fovs)
        for tp in timepoints
    }


def _well_is_complete(path: Path, expected: set[str]) -> bool:
    """True when *path* is a readable archive already holding *expected*.

    Reads the central directory only, so this costs a couple of seeks however
    large the archive is. An unreadable or truncated zip is treated as absent —
    ``atomic_zip`` cannot leave one, but a half-copied folder can.
    """
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return expected.issubset(set(zf.namelist()))
    except (OSError, zipfile.BadZipFile):
        return False


def _pick_workers(workers: int, n_wells: int) -> int:
    """Resolve the worker-thread count (0 = auto), capped at the well count."""
    if n_wells <= 0:
        return 1
    if workers and workers > 0:
        return max(1, min(workers, n_wells))
    return max(1, min(8, os.cpu_count() or 4, n_wells))


def _resolve_single_image(
    layout, n_wells: int, fovs_per_well: "int | None", frame_order: str, name: str,
) -> "tuple[int, int, int]":
    """Return ``(n_positions, n_timepoints, fovs_per_well)`` for a packed file."""
    n_frames = layout.n_frames
    if frame_order == "single":
        fovs = _resolve_positions(n_frames, n_wells, fovs_per_well,
                                  what="frame(s)", name=name)
        return n_frames, 1, fovs
    if fovs_per_well is None:
        raise ValueError(
            f"{name} packs {n_frames} frames into one image with timepoints, so "
            f"FOVs per well is needed to separate positions from timepoints "
            f"(positions = wells x FOVs; timepoints = frames / positions)."
        )
    n_positions = n_wells * fovs_per_well
    n_timepoints, rem = divmod(n_frames, n_positions)
    if rem or n_timepoints < 1:
        raise ValueError(
            f"{name} has {n_frames} frame(s), which does not split evenly into "
            f"{n_wells} well(s) x {fovs_per_well} FOV(s) = {n_positions} "
            f"position(s) x a whole number of timepoints. Check the grid and "
            f"FOVs-per-well."
        )
    return n_positions, n_timepoints, fovs_per_well


def _frame_index(pos: int, t: int, n_positions: int, n_timepoints: int,
                 frame_order: str) -> int:
    """Map a (position, timepoint) to the packed T-axis frame index."""
    if frame_order == "pos_in_time":
        return t * n_positions + pos
    if frame_order == "time_in_pos":
        return pos * n_timepoints + t
    return pos  # "single" -- one timepoint, frame == position


def _encode_tiff(plane: np.ndarray) -> bytes:
    """Encode a 2-D plane as a single-page TIFF in memory (no temp file)."""
    buf = io.BytesIO()
    tifffile.imwrite(buf, plane)
    return buf.getvalue()


def explode_ome_tiff(
    ome_path: "Path | str",
    out_in_dir: "Path | str",
    *,
    grid_rows: int,
    grid_cols: int,
    origin_well: str = "A01",
    direction: str = "row",
    serpentine: bool = True,
    experiment: str = "ome",
    fovs_per_well: "int | None" = None,
    frame_order: str = "single",
    well_order: "list[str] | None" = None,
    workers: int = 0,
    log_fn: "Callable[[str], None] | None" = None,
    progress_fn: "Callable[[int, int], None] | None" = None,
    well_progress_fn: "Callable[[str, int, int], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
    resume: bool = False,
) -> ExplodeResult:
    """Explode a packed OME-TIFF into per-well ``<WELL>.zip`` archives in *out_in_dir*.

    For every (position, timepoint, channel) the Z stack is max-projected to a
    2-D plane and stored as a member named
    ``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` inside that well's
    archive (the default filename schema -- the layout the pipeline reads
    directly, so no ``WellPlateZipper`` pass is needed). Each stage position maps
    to a 96-well label by acquisition order: *well_order*, an explicit ordered
    list of wells (which need not be rectangular), is used verbatim when given;
    otherwise the label list is the serpentine sweep of the *grid_rows* x
    *grid_cols* imaged block. Each position is one well x FOV.

    Two source structures are handled (see :class:`OmeLayout`). A **multi-image**
    file has one series per position. A **single-image** file packs every position
    into one series' ``T`` axis; *frame_order* says how that flat frame sequence
    splits into positions x timepoints (see :data:`FRAME_ORDERS`), and planes are
    read one page at a time (the whole array can be far too large to load).

    Wells are converted in parallel: *workers* threads (0 = auto), each with its
    own read handle, each owning a disjoint set of wells and writing that well's
    archive alone (no locking on the zip handles). *fovs_per_well* is the
    fields-of-view per well; ``None`` derives it.

    *progress_fn* receives ``(planes_written, total_planes)`` as it proceeds, and
    *well_progress_fn* receives ``(well, planes_written_for_that_well,
    planes_per_well)`` — the same work split per well, so a caller can draw one
    well's share of the conversion rather than only the run's total. It fires
    once per plane and is deliberately not throttled here: what counts as "often
    enough to be worth a repaint" is a UI question, so the quantising belongs to
    the caller that owns a UI (``datasets.ome_import.apply``), not to the
    pipeline.
    *should_abort*, polled per plane, raises :class:`OmeExplodeCancelled` when it
    returns True -- each in-flight well's staged archive is discarded, so no
    partial ``<WELL>.zip`` is left (a re-run overwrites cleanly). Raises
    ``ValueError`` on any grid / FOVs / frame-order inconsistency, a grid that
    overflows the plate, or a source whose metadata declares planes the file does
    not contain (see :meth:`OmeLayout.missing_plane_report`) — all of them before
    anything is written.
    """
    ome_path = Path(ome_path)
    out_in_dir = Path(out_in_dir)

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    def _abort() -> bool:
        return bool(should_abort and should_abort())

    if frame_order not in FRAME_ORDERS:
        raise ValueError(
            f"Unknown frame order {frame_order!r}; expected one of {FRAME_ORDERS}."
        )

    layout = read_ome_layout(ome_path)
    # Refuse an unsound source before a single archive is written. Each fault
    # this catches otherwise reaches the dataset as images that look like data:
    # tifffile zero-fills a plane the file declares but does not hold, and a
    # file whose OME structure it could not use converts as whatever page
    # sequence it fell back to.
    unsound = layout.validation_report(ome_path.name)
    if unsound:
        raise ValueError(unsound)
    exp_tok = _safe_token(experiment, "ome")
    # An explicit ordering (click-defined on the plate mockup) is used verbatim;
    # it needn't be rectangular. Otherwise reconstruct the rectangular serpentine
    # from the grid — both raise before any archive is written.
    if well_order:
        well_labels = normalize_well_order(well_order)
    else:
        well_labels = block_well_labels(
            grid_rows, grid_cols, origin=origin_well, direction=direction,
            serpentine=serpentine,
        )
    n_wells = len(well_labels)

    if layout.single_image:
        n_positions, n_timepoints, fovs = _resolve_single_image(
            layout, n_wells, fovs_per_well, frame_order, ome_path.name)
        tp_tokens = [f"t{t}" for t in range(n_timepoints)]
        head = f"single image, {layout.n_frames} frame(s) ({frame_order})"
    else:
        fovs = _resolve_positions(layout.n_positions, n_wells, fovs_per_well,
                                  what="stage position(s)", name=ome_path.name)
        n_positions, n_timepoints = layout.n_positions, layout.size_t
        tp_tokens = list(layout.timepoint_tokens)
        head = f"{layout.n_positions} position(s)"

    out_in_dir.mkdir(parents=True, exist_ok=True)
    total = n_positions * n_timepoints * layout.size_c
    n_workers = _pick_workers(workers, n_wells)
    _log(
        f"[ome] {ome_path.name}: {head} -> {n_wells} well(s) x {fovs} FOV(s) x "
        f"{n_timepoints} timepoint(s), {layout.size_c} channel(s)"
        f"{f', {layout.size_z} Z-plane(s) max-projected' if layout.size_z > 1 else ''}"
        f" -- {n_workers} worker(s).\n"
    )

    # Wells a previous attempt already finished. A well's archive is published
    # atomically, so one that exists holding every member these settings call
    # for is complete and correct — there is no half-written archive to detect.
    # Off by default: silently leaving wells alone is only right when the caller
    # has established that the folder holds *this* run's interrupted output.
    resumable: set[str] = set()
    if resume:
        resumable = {
            label for label in well_labels
            if _well_is_complete(
                out_in_dir / f"{label}.zip",
                _expected_members(exp_tok, layout.channel_names, label,
                                  fovs, tp_tokens))
        }
        if resumable:
            _log(f"[ome] Resuming: {len(resumable)} of {n_wells} well(s) are "
                 f"already converted and will be left alone.\n")

    lock = threading.Lock()
    done = 0
    # Planes one well accounts for: its FOVs x timepoints x channels. Every well
    # writes the same number, so this is the denominator of each well's share.
    planes_per_well = max(1, fovs * n_timepoints * layout.size_c)

    def _bump(well: "str | None" = None, well_done: int = 0) -> None:
        nonlocal done
        with lock:
            done += 1
            d = done
        if progress_fn:
            progress_fn(d, total)
        # Outside the lock: each call names its own well, so interleaving is
        # harmless, and taking the lock per plane would serialise the parallel
        # per-well conversion this reports on.
        if well_progress_fn and well is not None:
            well_progress_fn(well, well_done, planes_per_well)

    if progress_fn:
        progress_fn(0, total)

    def _convert_wells(well_indices: "list[int]"):
        written = 0
        wells: list[str] = []
        resumed: list[str] = []
        read_s = write_s = 0.0
        with tifffile.TiffFile(str(ome_path)) as tf:
            series0 = tf.series[0]
            axes0 = str(series0.axes).upper()
            for i in well_indices:
                if _abort():
                    raise OmeExplodeCancelled()
                label = well_labels[i]
                if label in resumable:
                    # Report it full rather than silently absent: the plate map
                    # this drives would otherwise show a finished well as one
                    # that never ran.
                    resumed.append(label)
                    if well_progress_fn:
                        well_progress_fn(label, planes_per_well, planes_per_well)
                    for _ in range(fovs * n_timepoints * layout.size_c):
                        _bump()
                    continue
                well_written = 0
                base = i * fovs
                with atomic_zip(out_in_dir / f"{label}.zip",
                                compression=zipfile.ZIP_STORED) as zf:
                    for local_fov in range(fovs):
                        pos = base + local_fov
                        arr = axes = None
                        if not layout.single_image:
                            r0 = time.perf_counter()
                            arr = tf.series[pos].asarray()
                            axes = str(tf.series[pos].axes).upper()
                            read_s += time.perf_counter() - r0
                        for ti in range(n_timepoints):
                            for c in range(layout.size_c):
                                if _abort():
                                    raise OmeExplodeCancelled()
                                r0 = time.perf_counter()
                                if layout.single_image:
                                    frame = _frame_index(pos, ti, n_positions,
                                                         n_timepoints, frame_order)
                                    plane = _packed_plane(series0, axes0, frame, c)
                                else:
                                    plane = _extract_plane(arr, axes, ti, c)
                                read_s += time.perf_counter() - r0
                                w0 = time.perf_counter()
                                member = (f"{exp_tok}_{layout.channel_names[c]}_"
                                          f"{label}_f{local_fov}_{tp_tokens[ti]}.tif")
                                zf.writestr(member, _encode_tiff(plane))
                                write_s += time.perf_counter() - w0
                                written += 1
                                well_written += 1
                                _bump(label, well_written)
                wells.append(label)
        return written, wells, read_s, write_s, resumed

    groups = [g for g in (list(range(i, n_wells, n_workers))
                          for i in range(n_workers)) if g]
    result = ExplodeResult(
        channels=list(layout.channel_names),
        timepoints=tp_tokens,
        fovs_per_well=fovs,
    )
    all_wells: list[str] = []
    all_resumed: list[str] = []
    read_total = write_total = 0.0
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(_convert_wells, g) for g in groups]
        for fut in as_completed(futures):
            w, wells, rs, ws, resumed = fut.result()
            result.images_written += w
            all_wells.extend(wells)
            all_resumed.extend(resumed)
            read_total += rs
            write_total += ws
    result.wells = sorted(set(all_wells) | set(all_resumed))
    result.wells_resumed = sorted(set(all_resumed))

    wall = time.perf_counter() - t_start
    rate = result.images_written / wall if wall > 0 else 0.0
    kept = (f" ({len(result.wells_resumed)} well(s) kept from the previous run)"
            if result.wells_resumed else "")
    _log(
        f"[ome] Wrote {result.images_written} image(s) for "
        f"{len(result.wells)} well(s){kept} into {out_in_dir.name}/ in {wall:.1f}s "
        f"({rate:.0f} planes/s; read {read_total:.1f}s, write {write_total:.1f}s "
        f"summed across {n_workers} worker(s)).\n"
    )
    return result
