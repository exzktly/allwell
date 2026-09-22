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

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.explode_common import TimeScan
from util.filename_token import safe_token
from util.well_token import canonical_well_label


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
        labels = [canonical_well_label(name) for name in self.position_wells]
        if not labels or not all(labels):
            return [], 0
        runs: list[tuple[str, int]] = []
        for label in labels:
            if runs and runs[-1][0] == label:
                runs[-1] = (label, runs[-1][1] + 1)
            else:
                runs.append((label, 1))
        wells = [label for label, _count in runs]
        if len(set(wells)) != len(wells):
            return [], 0
        counts = {count for _label, count in runs}
        if len(counts) != 1:
            return [], 0
        return wells, counts.pop()

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


def _channel_names(handle, size_c: int) -> tuple[list[str], list[str]]:
    """``(tokens, raw names)`` — positional where the file will not say.

    Guarded because ``ND2File.metadata`` raises on a file whose planes carry no
    fluorescent-probe block (see this module's docstring). Losing the channel
    *names* costs the run nothing it cannot carry — ``c0``, ``c1`` are names —
    where refusing the file outright would turn a library bug into a plate the
    user cannot convert.
    """
    raw: list[str] = []
    try:
        channels = getattr(handle.metadata, "channels", None) or []
        raw = [str(getattr(getattr(ch, "channel", None), "name", "") or "")
               for ch in channels]
    except Exception:
        raw = []
    tokens = [safe_token(raw[i] if i < len(raw) else "", f"c{i}")
              for i in range(size_c)]
    return tokens, raw


def read_nd2_layout(path: Path | str, *, read_times: bool = False) -> Nd2Layout:
    """Read *path*'s layout without decoding a single pixel.

    With *read_times* it also samples one position's frame times into
    :attr:`Nd2Layout.time_scan`. That costs nothing extra on an ND2: the times
    live in one ``CustomData|AcqTimesCache!`` chunk the library has already
    read by the time the file is open, where a CZI has to be seeked once per
    timepoint. The flag exists so the two readers present the same shape to
    their converters, not because it saves anything here.
    """
    path = Path(path)
    with _open(path) as handle:
        sizes = dict(getattr(handle, "sizes", {}) or {})
        layout = Nd2Layout(
            n_positions=int(sizes.get("P", 1)),
            size_t=int(sizes.get("T", 1)),
            size_c=int(sizes.get("C", 1)),
            size_z=int(sizes.get("Z", 1)),
            width=int(sizes.get("X", 0)),
            height=int(sizes.get("Y", 0)),
            dtype=str(np.dtype(handle.dtype)),
            position_wells=_position_names(handle),
        )
        layout.channel_names, layout.raw_channel_names = _channel_names(
            handle, layout.size_c)
        if getattr(handle, "is_rgb", False):
            layout.warnings.append(
                "the file is RGB, so each pixel carries three samples rather "
                "than one intensity")
        if read_times:
            layout.time_scan = scan_timepoint_hours(handle, layout)
    return layout


def scan_timepoint_hours(handle, layout: Nd2Layout) -> TimeScan:
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
    """
    size_t = layout.size_t
    if size_t <= 0:
        return TimeScan()
    stamps: dict[int, float] = {}
    try:
        for t in range(size_t):
            # Frame index for (position 0, timepoint t): the position loop is
            # outermost, so one position's series is contiguous.
            meta = handle.frame_metadata(t)
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
        index = self.frame_index(position, t)
        try:
            frame = self._handle.read_frame(index)
        except Exception as exc:
            raise Nd2ReadError(
                f"{self._path.name}: could not read the image at position "
                f"{position}, timepoint {t}, channel {channel} ({exc})."
            ) from exc
        return _plane_from_frame(frame, channel, self._layout.size_c)

    def frame_index(self, position: int, t: int) -> int:
        """The sequence index of one position's timepoint.

        The position loop is the outer one and the time loop nests inside it,
        which is the order a plate time-course is written in, so one position's
        series is contiguous. Named rather than inlined because
        :func:`scan_timepoint_hours` samples the *first* position's series and
        has to agree with this about what that means.
        """
        return position * max(1, self._layout.size_t) + t


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
    matching the channel count is the channels, and anything else leading is a
    Z stack to project.
    """
    plane = np.asarray(frame)
    while plane.ndim > 2 and plane.shape[-1] == 1:
        plane = plane[..., 0]
    if plane.ndim > 2 and size_c > 1 and plane.shape[0] == size_c:
        plane = plane[min(channel, plane.shape[0] - 1)]
    while plane.ndim > 2:
        # Whatever leads now is a Z stack: the max projection every converter
        # applies, so an ND2 plate and a CZI one measure the same thing.
        plane = plane.max(axis=0)
    return plane
