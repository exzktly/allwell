"""Read a Nikon ND2 plate scan's shape, wells, channels and timing.

The counterpart of :mod:`pipeline.czi_read`, and deliberately much shorter,
because the parsing is not ours. The CZI reader is a ZISRAW parser written
here for a measured reason — every maintained CZI reader on PyPI either ships
binary wheels the bundle would carry or cannot resolve against the numpy this
app pins. ND2 is the opposite case: ``nd2`` 0.9.0 is a pure-Python wheel with
an unpinned numpy, so this module is an **adapter** that turns what that
library reports into the same vocabulary the converters already speak —
positions, wells, channel tokens, a :class:`~pipeline.explode_common.TimeScan`
— and nothing more.

What the adapter is *for*, beyond translation:

* **A position's well comes from its own name.** A Nikon plate scan writes the
  well into each position's ``dPosName``, the ND2 analogue of a CZI scene
  name. Several positions naming one well is not a fault but the ordinary way
  an ND2 says "four fields of view in A01", so the wells and the fields per
  well are read out of that grouping rather than from a grid.
* **The elapsed times come from one position's series**, the same rule the
  other two converters follow and for the same measured reason: a plate scan
  reaches its wells minutes apart inside a single round, so a series assembled
  across positions folds the stage's traverse into the times it reports.
* **Every call into the library is guarded.** ``nd2`` 0.9.0's
  ``_get_excitation`` binds its ``excitation`` local only inside ``if probe:``
  and then reads it unconditionally, so ``ND2File.metadata`` raises
  ``UnboundLocalError`` on a file whose planes carry no fluorescent-probe
  block. Real NIS files carry one; a user's file is not obliged to, and a
  converter that dies on the channel names of an otherwise readable plate
  would be refusing a file it can convert. Channel names fall back to
  positional tokens, which is a name the run can carry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.explode_common import TimeScan
from util.filename_token import safe_token
from util.well_token import canonical_well_label


#: The axes an ND2 *loops* over, as opposed to the ones that live inside a
#: frame. ``nd2.AXIS.frame_coords()`` is ``{S, C, X, Y}``: a channel is part of
#: a frame, and **Z is not** — it is a coordinate like position and time, so a
#: Z stack multiplies the sequence count and each plane is fetched by its own
#: index. Reading one frame and projecting "whatever leads" collapses nothing,
#: because a frame never holds a stack.
_LOOP_AXES = ("P", "T", "Z")


log = logging.getLogger(__name__)

class Nd2ReadError(Exception):
    """A user-facing reason an ND2 cannot be read or converted."""


def _open(path: Path):
    """``nd2.ND2File(path)``, with the import failure turned into a refusal.

    Imported here rather than at module scope so that importing this module —
    which ``tests/_repo_scan`` and the remote manifest both do — does not
    require the dependency to be present. The pipeline is checked for
    self-sufficiency by ``test_architecture_contracts``, and a module-scope
    import of an optional reader would make every one of those checks depend
    on it being installed.
    """
    try:
        import nd2
    except ImportError as exc:      # pragma: no cover — depends on the env
        raise Nd2ReadError(
            "Reading ND2 files needs the 'nd2' package, which is not "
            "installed in this environment. Install it with "
            "`pip install nd2==0.9.0`, or convert the acquisition to "
            "OME-TIFF in NIS-Elements and use Convert OME-TIFF instead."
        ) from exc
    try:
        return nd2.ND2File(Path(path))
    except Nd2ReadError:
        raise
    except Exception as exc:
        raise Nd2ReadError(
            f"{Path(path).name} could not be opened as an ND2 file ({exc})."
        ) from exc


@dataclass
class Nd2Layout:
    """What an ND2 holds, in the vocabulary the converters share.

    ``n_positions`` is the stage-position count — the P axis — and is the
    axis wells are read from. ND2 has no separate mosaic-tile axis the way a
    CZI does: several fields of view in one well are simply several positions
    that name that well.
    """

    n_positions: int = 0
    size_t: int = 1
    size_c: int = 1
    size_z: int = 1
    width: int = 0
    height: int = 0
    dtype: str = ""
    #: Filename tokens, one per channel index — already through
    #: :func:`~util.filename_token.safe_token`.
    channel_names: list[str] = field(default_factory=list)
    #: The same channels as the file spells them, kept so a token collision can
    #: name the two channels the user has to go and rename.
    raw_channel_names: list[str] = field(default_factory=list)
    #: The well each position names, in position order; ``""`` where the file
    #: does not name one. A *list* rather than a mapping because every position
    #: has an entry and the order is the acquisition order.
    position_wells: list[str] = field(default_factory=list)
    #: The experiment's **loop axes, outermost first**, as they are nested in
    #: this file — some ordering of ``"P"``, ``"T"`` and ``"Z"``. This is the
    #: one fact that decides which sequence index holds which image, and it is
    #: a property of the file rather than of the format: ``nd2`` builds its
    #: index from ``self.experiment`` in order, and a plate scanned position
    #: by position (``["P", "T"]``) and a multipoint time-course that visits
    #: every position within each round (``["T", "P"]``) are both ordinary,
    #: both written by NIS-Elements, and indistinguishable once the order is
    #: discarded. A reader that assumed one of them read every plane of the
    #: other from the wrong place, with the archive complete, correctly named
    #: and reported as a success.
    #:
    #: An axis the file does not loop over is simply absent, which is why this
    #: is a list rather than a fixed triple.
    axis_order: list[str] = field(default_factory=list)
    #: True when each pixel carries several samples rather than one intensity.
    #: A fact, not a sentence, because ``validation_report`` has to refuse on
    #: it and a refusal keyed on the wording of a warning is a refusal waiting
    #: to be broken by an edit to that wording.
    is_rgb: bool = False
    #: Elapsed hours per timepoint, sampled from one position — ``None`` when
    #: this layout was read without asking for them.
    time_scan: "TimeScan | None" = None
    warnings: list[str] = field(default_factory=list)

    @property
    def timepoint_tokens(self) -> list[str]:
        """``t0``, ``t1``, … — the positional fallback.

        What the plate is named by when the file records no usable times.
        :mod:`pipeline.nd2_set`'s resolver replaces these with elapsed hours
        where the frame-times cache supports it.
        """
        return [f"t{t}" for t in range(self.size_t)]

    def axis_extent(self, axis: str) -> int:
        """How many values one loop axis takes — the stride source for an index."""
        return {"P": self.n_positions, "T": self.size_t,
                "Z": self.size_z}.get(axis, 1)

    @property
    def total_images(self) -> int:
        """Images a conversion writes: one per (position, timepoint, channel).

        Z is collapsed into each by the max projection, which is why it is not
        a factor here.
        """
        return self.n_positions * self.size_t * self.size_c

    def wells_from_positions(self) -> tuple[list[str], int]:
        """``(well labels, fields per well)`` from the positions' own names.

        Positions naming one well are that well's fields of view, which is how
        a Nikon plate scan with four fields per well describes itself. The
        grouping is over *runs* of equal names in acquisition order rather than
        over the set of names, because a plate that returns to a well later in
        the run is two visits to one well and not eight fields of it — and the
        difference decides whether the second visit's images overwrite the
        first's.

        ``([], 0)`` when the names cannot be used: any position unnamed, or the
        wells not all taking the same number of fields. Both are refusals the
        caller turns into a message pointing at the grid, because a mapping
        half from the file and half from a grid is right exactly where a user
        is most likely to spot-check it.
        """
        return self._wells_from_positions()[:2]

    def well_mapping_problem(self) -> str:
        """Why the positions' names cannot map this plate, or ``""``.

        Separate from the mapping itself because all four ways it can fail
        used to come back as the same empty result, and the caller then
        rendered one sentence for every one of them — a sentence asserting
        that the names were present and blaming field counts and repeats.
        Measured against five real shapes it was actively wrong for three:
        NIS multipoint naming (``A1_1``, ``A1_2``), a descriptive prefix
        (``Point A1``) and a 384-well plate (``I01``) all fail because
        ``canonical_well_label`` does not recognise the *name*, not because
        the fields are uneven, and the first two carry a mapping the file
        genuinely holds.
        """
        return self._wells_from_positions()[2]

    def _wells_from_positions(self) -> "tuple[list[str], int, str]":
        """``(wells, fovs per well, problem)`` — the one implementation."""
        raw = list(self.position_wells)
        if not raw:
            return [], 0, "the file names no stage positions at all"
        labels = [canonical_well_label(name) for name in raw]
        unreadable = [raw[i] or "(unnamed)"
                      for i, label in enumerate(labels) if not label]
        if unreadable:
            shown = ", ".join(repr(n) for n in sorted(set(unreadable))[:4])
            more = "" if len(set(unreadable)) <= 4 else ", …"
            named = sum(1 for label in labels if label)
            return [], 0, (
                f"{len(unreadable)} of {len(raw)} stage position(s) are named "
                f"in a way this converter cannot read as a 96-well position "
                f"({shown}{more}) — {named} of them do parse. Well names have "
                f"to be a row letter A–H and a column 1–12, so a 384-well "
                f"plate, a name carrying a field number such as 'A1_1', and a "
                f"descriptive name such as 'Point A1' are all outside what a "
                f"well label can be here. Switch the well source to the plate "
                f"grid and set the imaged rows and columns, which lays the "
                f"acquisition order over the plate instead.")
        runs: list[tuple[str, int]] = []
        for label in labels:
            if runs and runs[-1][0] == label:
                runs[-1] = (label, runs[-1][1] + 1)
            else:
                runs.append((label, 1))
        wells = [label for label, _count in runs]
        repeated = sorted({w for w in wells if wells.count(w) > 1})
        if repeated:
            return [], 0, (
                f"the acquisition returns to {', '.join(repeated)} later in "
                f"the run, so those are two visits to one well rather than "
                f"more fields of it, and the second visit's images would "
                f"overwrite the first's. Switch the well source to the plate "
                f"grid, or split the acquisition.")
        counts = sorted({count for _label, count in runs})
        if len(counts) != 1:
            uneven = ", ".join(f"{label}: {count}" for label, count in runs
                               if count != counts[-1])[:120]
            return [], 0, (
                f"the wells do not all take the same number of fields of "
                f"view ({counts[0]} to {counts[-1]}; {uneven}). A plate whose "
                f"wells carry different field counts has no single layout for "
                f"the pipeline to read. Switch the well source to the plate "
                f"grid, or re-export with the same fields in every well.")
        return wells, counts[0], ""

    def validation_report(self, name: str) -> str:
        """The one sentence a caller shows, or ``""`` when the file is sound.

        Everything here would otherwise reach the dataset as images that look
        like data, which is the bar the other two converters' refusals are set
        at.
        """
        if not self.n_positions or not self.size_c:
            return (f"{name} holds no images to convert — it reports "
                    f"{self.n_positions} position(s) and {self.size_c} "
                    f"channel(s).")
        if not self.width or not self.height:
            return f"{name} reports a zero-sized image ({self.width}×{self.height})."
        if self.is_rgb:
            return (f"{name} is RGB, so each pixel carries three samples "
                    f"rather than one intensity. There is no single value to "
                    f"quantify and picking one of the samples would be an "
                    f"unannounced decision about which. Export the "
                    f"acquisition channels as greyscale from NIS-Elements.")
        if self.dtype not in ("uint8", "uint16", "int32", "float32", "float64"):
            return (f"{name} stores pixel type {self.dtype!r}, which is not a "
                    f"single-intensity greyscale format this converter can "
                    f"quantify.")
        collision = self._colliding_channels()
        if collision is not None:
            token, names = collision
            return (f"{name} has {len(names)} channels whose names all reduce "
                    f"to the filename token {token!r} ({', '.join(names)}). "
                    f"Both would be written to one name inside a well's "
                    f"archive and only the last would be readable, while the "
                    f"run reported writing both. Rename them in NIS-Elements.")
        return ""

    def _colliding_channels(self) -> "tuple[str, list[str]] | None":
        """The first filename-token collision among the channels, or ``None``.

        Compared case-insensitively because every consumer of a channel token
        downstream folds case, so ``GFP`` and ``gfp`` collapse into one key
        even though they are two member names.
        """
        seen: dict[str, list[str]] = {}
        for i, token in enumerate(self.channel_names):
            raw = (self.raw_channel_names[i]
                   if i < len(self.raw_channel_names) else token)
            seen.setdefault(str(token).lower(), []).append(str(raw))
        for token, names in seen.items():
            if len(names) > 1:
                return token, names
        return None


def _position_names(handle) -> list[str]:
    """Each stage position's own name, in acquisition order.

    ``[]`` when the file declares no position loop at all, which is a single
    -position acquisition rather than a plate.
    """
    for loop in getattr(handle, "experiment", []) or []:
        if getattr(loop, "type", "") == "XYPosLoop":
            points = getattr(getattr(loop, "parameters", None), "points", []) or []
            return [str(getattr(pt, "name", "") or "") for pt in points]
    return []


def _channel_names(handle, size_c: int) -> tuple[list[str], list[str], str]:
    """``(tokens, raw names, reason)`` — positional where the file will not say.

    *reason* is ``""`` when the names were read, and otherwise the sentence
    the layout carries into its warnings.

    Guarded because ``ND2File.metadata`` raises on a file whose planes carry no
    fluorescent-probe block (see this module's docstring). Losing the channel
    *names* costs the run nothing it cannot carry — ``c0``, ``c1`` are names —
    where refusing the file outright would turn a library bug into a plate the
    user cannot convert.
    """
    raw: list[str] = []
    reason = ""
    try:
        channels = getattr(handle.metadata, "channels", None) or []
        raw = [str(getattr(getattr(ch, "channel", None), "name", "") or "")
               for ch in channels]
    except Exception as exc:
        # Recorded, not merely survived. Every other degradation route in this
        # module writes down a reason; this one left the user with c0/c1
        # archives and nothing on screen saying the file's own channel names
        # had been lost, which is §7's degraded-output-looking-like-good-output
        # in miniature — the names reach the filenames and the Analyze tokens.
        raw = []
        reason = (f"the channel names could not be read ({exc}), so the "
                  f"channels are named by position (c0, c1, …)")
    tokens = [safe_token(raw[i] if i < len(raw) else "", f"c{i}")
              for i in range(size_c)]
    return tokens, raw, reason


def read_nd2_layout(path: Path | str, *, read_times: bool = False,
                    report=None) -> Nd2Layout:
    """Read *path*'s layout without decoding a single pixel.

    With *read_times* it also samples one position's frame times into
    :attr:`Nd2Layout.time_scan`. That costs nothing extra on an ND2: the times
    live in one ``CustomData|AcqTimesCache!`` chunk the library has already
    read by the time the file is open, where a CZI has to be seeked once per
    timepoint. The flag exists so the two readers present the same shape to
    their converters, not because it saves anything here.
    """
    path = Path(path)

    # *report* is `core.worker_bridge`'s reporter, or ``None`` when the caller
    # is not a GUI. Named steps rather than a percentage: this read has a
    # handful of phases of very different cost and no useful total until the
    # timepoint count is known, and a page showing one unchanging sentence for
    # the whole read is indistinguishable from one that has hung.
    def _say(name: str) -> None:
        if report is not None:
            report.stage(name)

    _say(f"Opening {path.name}…")
    with _open(path) as handle:
        # Wrapped as a whole, not only around ``_open``. ``nd2`` parses
        # lazily: ``ND2File(path)`` succeeds on a file with the right magic
        # and a broken header, and the ``ValueError`` comes out of the first
        # attribute read instead. Without this, a truncated or half-written
        # file escaped as a bare ``ValueError`` past a caller that catches
        # this module's own error — which is the boundary this module exists
        # to hold, since everything above it is meant to see only
        # ``Nd2Layout``, ``Nd2Reader`` and ``Nd2ReadError``.
        try:
            sizes = dict(getattr(handle, "sizes", {}) or {})
            layout = Nd2Layout(
                # ``sizes`` is ordered to match ``shape`` (the library
                # documents it so, and builds both from ``self.experiment``
                # in order), so the loop axes in order are simply the keys
                # that are loop axes. Taking the counts with ``.get`` and
                # dropping the order is what made every index a guess.
                axis_order=[a for a in sizes if a in _LOOP_AXES],
                n_positions=int(sizes.get("P", 1)),
                size_t=int(sizes.get("T", 1)),
                size_c=int(sizes.get("C", 1)),
                size_z=int(sizes.get("Z", 1)),
                width=int(sizes.get("X", 0)),
                height=int(sizes.get("Y", 0)),
                dtype=str(np.dtype(handle.dtype)),
                position_wells=_position_names(handle),
            )
            _say("Reading the channel names…")
            (layout.channel_names, layout.raw_channel_names,
             channel_reason) = _channel_names(handle, layout.size_c)
            if channel_reason:
                layout.warnings.append(channel_reason)
        except Nd2ReadError:
            raise
        except Exception as exc:
            raise Nd2ReadError(
                f"{path.name} could not be read as an ND2 file ({exc})."
            ) from exc
        layout.is_rgb = bool(getattr(handle, "is_rgb", False))
        if read_times:
            _say("Reading the acquisition times…")
            layout.time_scan = scan_timepoint_hours(handle, layout,
                                                    report=report)
    return layout


def read_stage_calibration(path: Path | str) -> tuple[list, tuple[float, float] | None]:
    """``(stage per position, pixel size)`` for the acquisition record (tracker I5).

    The stage is each position-loop point's ``stagePositionUm`` — the format's own unit is µm — or an empty
    list when the file has no position loop. The pixel size is the calibration only when the file flags
    both axes calibrated: an uncalibrated ND2 reports ``1.0``, which is a placeholder, not a measurement.
    Never raises: a record that cannot be read is a record of unknowns.
    """
    stages: list = []
    pixel = None
    try:
        with _open(path) as handle:
            for loop in getattr(handle, "experiment", []) or []:
                if getattr(loop, "type", "") == "XYPosLoop":
                    for point in getattr(getattr(loop, "parameters", None), "points", []) or []:
                        at = getattr(point, "stagePositionUm", None)
                        stages.append(None if at is None else (float(at[0]), float(at[1])))
            # Separately guarded: ``metadata`` is the call ``nd2`` 0.9.0 raises from on a file without a
            # fluorescent-probe block (module docstring), and the stage positions are still good then.
            try:
                volume = handle.metadata.channels[0].volume
                if all(volume.axesCalibrated[:2]) and all(v > 0 for v in volume.axesCalibration[:2]):
                    pixel = (float(volume.axesCalibration[0]), float(volume.axesCalibration[1]))
            except Exception as exc:    # the record then says it does not know the pixel size
                log.warning("ND2 calibration could not be read from %s: %s", Path(path).name, exc)
    except Exception as exc:            # the images convert either way; the record says what it does not know
        log.warning("ND2 stage positions could not be read from %s: %s", Path(path).name, exc)
    return stages, pixel


def frame_index_for(layout: Nd2Layout, position: int, t: int, z: int = 0) -> int:
    """The sequence index of one coordinate, from the file's own loop order.

    Free function as well as :meth:`Nd2Reader.frame_index` because the time
    scan runs against a bare handle during :func:`read_nd2_layout`, before any
    reader exists, and the two must not be two implementations of one rule —
    that is exactly how the scan came to sample a different set of frames from
    the one a conversion writes.
    """
    coords = {"P": position, "T": t, "Z": z}
    index = 0
    for axis in (layout.axis_order or ["P", "T"]):
        index = index * max(1, layout.axis_extent(axis)) + coords[axis]
    return index


def scan_timepoint_hours(handle, layout: Nd2Layout, *,
                         report=None) -> TimeScan:
    """Elapsed hours per timepoint, from the first position's own series.

    **One position, and the first one scanned**, which is the rule both other
    converters follow. A plate scan reaches its wells minutes apart inside a
    single round — measured at 31 minutes across 96 positions on the CZI side —
    so a series assembled from whichever position answered first at each
    timepoint folds the stage's traverse into the elapsed times it reports.

    Never raises. Every failure comes back as an empty or partial
    :class:`~pipeline.explode_common.TimeScan` with the reason written down,
    because the caller's fallback — positional ``t0``/``t1`` tokens — is always
    available and always correct.

    The frames are located through :func:`frame_index_for`, the same function
    a conversion writes through, so the two cannot disagree about which
    sequence is "position 0 at timepoint t". Indexing ``frame_metadata`` by
    ``t`` alone was right only for a position-outer file; on a multipoint
    time-course it walked *positions* instead, and reported the stage's
    traverse across the plate as the experiment's whole duration — a
    twelve-hour timecourse named as a ninety-second one, with no reason
    recorded, so nothing anywhere said the times were not the file's.
    """
    size_t = layout.size_t
    if size_t <= 0:
        return TimeScan()
    stamps: dict[int, float] = {}
    try:
        for t in range(size_t):
            # Reported per timepoint: this is the one phase of the read with
            # a real total, and on a long timelapse it is also the slow one.
            if report is not None:
                report.progress(t, size_t)
            # Position 0, timepoint t, and the first Z plane of its stack —
            # every Z of one coordinate is acquired within the same instant
            # for this purpose, and the first is the one that exists.
            meta = handle.frame_metadata(frame_index_for(layout, 0, t, 0))
            channels = getattr(meta, "channels", None) or []
            if not channels:
                continue
            ms = getattr(getattr(channels[0], "time", None), "relativeTimeMs", None)
            if ms is not None:
                stamps[t] = float(ms) / 3_600_000.0
    except IndexError:
        # What a file with no frame-times cache raises: the library warns,
        # returns an empty list, and then indexes it. "list index out of
        # range" is honest and tells a user nothing they can act on, so the
        # case is named instead.
        return TimeScan(reasons=[
            "the file records no acquisition time for its frames"], scene=0)
    except Exception as exc:
        return TimeScan(reasons=[f"the frame times could not be read ({exc})"],
                        scene=0)
    if 0 not in stamps:
        return TimeScan(reasons=[
            f"{len(stamps)} of {size_t} timepoint(s) carry a readable "
            f"acquisition time, and the first is not among them, so there is "
            f"no zero to measure the rest from"], scene=0)
    origin = stamps[0]
    hours = {t: value - origin for t, value in stamps.items()}
    reasons: list[str] = []
    if len(hours) < size_t:
        reasons.append(f"only {len(hours)} of {size_t} timepoint(s) carry a "
                       f"readable acquisition time")
    return TimeScan(hours_by_index=hours, reasons=reasons, scene=0)


class Nd2Reader:
    """An open ND2, indexed by ``(position, timepoint, channel)``.

    Built once per reading thread — the handle is not shared, for the same
    reason the other two exploders give each worker its own: the seeks are the
    whole cost of a read, and serialising them behind a lock would undo the
    per-well parallelism the exploder exists to get.

    Used as a context manager::

        with Nd2Reader(path, layout) as reader:
            plane = reader.read_projection(position, t, c)
    """

    def __init__(self, path: Path | str, layout: Nd2Layout | None = None) -> None:
        self._path = Path(path)
        self._layout = layout if layout is not None else read_nd2_layout(self._path)
        self._handle = None

    def __enter__(self) -> "Nd2Reader":
        self._handle = _open(self._path)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def read_projection(self, position: int, t: int, channel: int) -> np.ndarray:
        """The Z stack at these coordinates, collapsed by maximum intensity.

        The standard reduction for fluorescence quantification and the same one
        the other two exploders apply, so a plate converted from an ND2 and one
        converted from a CZI or an OME-TIFF are measuring the same thing.

        Reads **one frame**, not the file. The first version of this called
        ``ND2File.asarray()`` and sliced the result, which is correct and
        reads the whole acquisition into memory once per plane — on a plate of
        any size that is the difference between a conversion and a machine
        that stops responding. ``read_frame`` takes the single frame at the
        sequence index instead.
        """
        if self._handle is None:      # pragma: no cover — guarded by callers
            raise Nd2ReadError("read_projection on a reader that is not open")
        # One frame per Z index, reduced as they arrive. Z is a *coordinate*
        # axis in ND2 (``AXIS.frame_coords()`` is ``{S, C, X, Y}``), so a
        # frame never holds a stack and there is nothing inside one to
        # project: the stack has to be gathered across sequence indices, the
        # way ``ome_explode`` and ``czi_read`` gather theirs. Reduced in the
        # loop rather than stacked and maxed at the end, so peak memory is two
        # planes however deep the stack is.
        out: np.ndarray | None = None
        for z in range(max(1, self._layout.size_z)):
            index = self.frame_index(position, t, z)
            try:
                frame = self._handle.read_frame(index)
            except Exception as exc:
                raise Nd2ReadError(
                    f"{self._path.name}: could not read the image at position "
                    f"{position}, timepoint {t}, Z {z}, channel {channel} "
                    f"({exc})."
                ) from exc
            plane = _plane_from_frame(frame, channel, self._layout.size_c)
            if out is None:
                # **Copied, not referenced.** ``read_frame`` hands back a view
                # into the file's memory map, so a plane returned as-is dies
                # when the handle closes — a segfault with no Python
                # traceback, in a caller that did nothing wrong. A stack
                # happened to be safe because ``np.maximum`` allocates, which
                # made the single-plane case (the common one) the only one
                # that could crash. Found by a test reading the plane after
                # the ``with`` block, which is what any caller may do.
                out = np.array(plane, copy=True)
            else:
                np.maximum(out, plane, out=out)
        if out is None:      # pragma: no cover — the range is never empty
            raise Nd2ReadError(
                f"{self._path.name}: no Z plane at position {position}, "
                f"timepoint {t}, channel {channel}.")
        return out

    def frame_index(self, position: int, t: int, z: int = 0) -> int:
        """The sequence index of one ``(position, timepoint, Z)`` coordinate.

        Built from **the file's own loop order** (:attr:`Nd2Layout.axis_order`)
        rather than from an assumed one. ``nd2`` numbers its sequences by a
        row-major walk of the experiment's loops as they are nested, so the
        index of a coordinate is only knowable once you know that nesting.

        The version this replaces was ``position * size_t + t``, which is
        correct for a file scanned position by position and wrong for every
        other. A multipoint time-course — the stage visiting each position
        within a round and coming back — is nested the other way, and on one
        of those it returned a sequence holding a different well at a
        different time. Nothing downstream could notice: the archive came out
        complete, correctly named, correctly counted, and reported as a
        success, which is the failure CLAUDE.md §7 is written against. It also
        omitted the Z stride, so a stack corrupted position and timepoint too.

        :func:`scan_timepoint_hours` samples through this same function, so
        the frames it reads and the frames a conversion writes cannot disagree
        about what a coordinate means.
        """
        return frame_index_for(self._layout, position, t, z)


def _plane_from_frame(frame: np.ndarray, channel: int, size_c: int) -> np.ndarray:
    """One channel's 2-D plane out of a single frame, Z projected.

    The axis order is taken from the *measured* shape rather than from the
    reader's ``_actual_frame_shape``, which describes ``(height, width,
    channels, components)`` — a two-channel 8×8 frame comes back ``(2, 8, 8)``,
    with the channels **leading**, because the reader transposes before
    returning. Reading the helper and indexing the last axis produced a plane
    that was the right shape, the right dtype, and channel 0's pixels under
    channel 1's name.

    The channel axis is therefore identified by its *length*: a leading axis
    matching the channel count is the channels.

    **Nothing here projects a Z stack**, because a frame never holds one — Z
    is a coordinate axis (``AXIS.frame_coords()`` is ``{S, C, X, Y}``), so the
    stack is gathered across sequence indices by
    :meth:`Nd2Reader.read_projection` instead. A previous version ended with
    ``while plane.ndim > 2: plane.max(axis=0)`` under a comment calling it
    "the max projection every converter applies". It was unreachable for any
    real ND2, so no projection happened at all — and on an RGB frame, whose
    sample axis is not a channel axis either, it was a predicate applied
    outside its domain and maxed an 8×8 image down to 8×3.

    A frame that is still not 2-D once the channel has been taken is therefore
    a shape this reader does not understand, and it says so rather than
    reducing an axis it cannot name.
    """
    plane = np.asarray(frame)
    while plane.ndim > 2 and plane.shape[-1] == 1:
        plane = plane[..., 0]
    if plane.ndim > 2 and size_c > 1 and plane.shape[0] == size_c:
        # No clamp on ``channel``: the guard above establishes that the axis
        # is exactly ``size_c`` long, and every caller indexes a channel of
        # that layout. A ``min(channel, shape[0] - 1)`` here read as a
        # safeguard and could only ever have silently substituted the last
        # channel's pixels for an out-of-range request, which is the one
        # outcome this function must not produce.
        plane = plane[channel]
    if plane.ndim != 2:
        raise Nd2ReadError(
            f"a frame came back with shape {tuple(np.shape(frame))}, which "
            f"does not resolve to one 2-D plane for channel {channel} of "
            f"{size_c}. Reducing an axis this reader cannot name would "
            f"produce a plausible image of the wrong pixels.")
    return plane
