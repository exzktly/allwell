"""Guess a filename schema and channel set by scanning an input folder.

Analyze is otherwise schema-*first*: the user declares the filename layout
(``experiment:channel:well:fov:timepoint`` and a separator) before the pipeline
can parse a single name. This module infers that layout instead, by sampling the
names already on disk — loose ``.tif`` files, a single ``.zip``, the
folder-of-per-well-``.zip`` layout the OME converter emits, or per-well
``<WELL>/`` folders — so the Analyze page can pre-fill the schema and channel
fields when a folder is picked.

The inference is deliberately conservative and reuses the app's own per-token
parsers so a slot is only labelled when the sampled tokens actually parse that
way:

* **well** — the slot most of whose tokens are valid 96-well labels
  (:func:`util.well_token.canonical_well_label`).
* **timepoint** — a ``t<n>`` / ``<n>h`` style slot (:func:`util.timepoint.parse_hours`
  with an explicit prefix or unit, so a bare integer is not mistaken for one).
* **fov** — an explicit ``f<n>`` slot. A slot of bare integers is deliberately
  *not* claimed: the same evidence would equally support a timepoint index or a
  site number, and this module leaves what it cannot identify as ``ignore``
  rather than pre-filling the Analyze form with a confident wrong guess.
* **experiment** — a slot constant across every sampled name.
* **channel** — the remaining small, mostly-alphabetic categorical slot; its
  distinct values are the channel tokens, seeded into roles by
  :mod:`util.channel_roles`.

Anything it can't place stays ``ignore``. The guess is only reported *confident*
when both a well and a channel slot were found; the caller pre-fills regardless
but can treat a non-confident guess as advisory. Stdlib-only (Qt-free), so it
sits in ``util`` alongside the parsers it composes.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from util.channel_roles import ChannelTokens, channel_tokens_from_roles, default_channel_roles
from util.fov import parse_fov
from util.schema import DEFAULT_SEP
from util.timepoint import parse_hours
from util.well_token import canonical_well_label, is_valid_well_name

_TIF_SUFFIXES = (".tif", ".tiff")
_SEP_CANDIDATES = ("_", "-", ".")
_MIN_SLOT_FRACTION = 0.7      # a slot must parse this uniformly to earn a role
_SAMPLE_LIMIT = 400           # names sampled — "briefly scanning", not a full walk

_TP_RE = re.compile(r"(?i)^t\d+$")
_FOV_RE = re.compile(r"(?i)^f\d+$")
_HAS_ALPHA_RE = re.compile(r"[A-Za-z]")


@dataclass
class InferredSchema:
    """The schema + channels guessed from a folder's filenames."""

    separator: str
    fields: list[str] = field(default_factory=list)   # per slot; SCHEMA_FIELDS vocab
    channels: list[str] = field(default_factory=list)  # distinct channel tokens, in order
    confident: bool = False   # a well AND a channel slot were both identified

    @property
    def schema_str(self) -> str:
        return ":".join(self.fields)

    def channel_tokens(self) -> ChannelTokens:
        """The channel role assignment seeded from the guessed channel tokens."""
        roles = default_channel_roles(self.channels)
        return channel_tokens_from_roles([(c, roles[c]) for c in self.channels])


# ── token classifiers ───────────────────────────────────────────────────────
def _is_well(tok: str) -> bool:
    return canonical_well_label(tok) is not None


def _is_timepoint(tok: str) -> bool:
    if _TP_RE.match(tok or ""):
        return True
    # A unit-bearing token ("48h", "90min", "2d") — but not a bare number, which
    # parse_hours also accepts and which would shadow an fov/experiment slot.
    return bool(re.search(r"(?i)\d\s*(h|hr|hours?|min|m|d|days?)$", tok or "")) \
        and parse_hours(tok) is not None


def _is_fov(tok: str) -> bool:
    return bool(_FOV_RE.match(tok or "")) and parse_fov(tok) is not None


# ── sampling ─────────────────────────────────────────────────────────────────
def _spread(names: list[str], take: int) -> list[str]:
    """Up to *take* entries drawn evenly across *names*, keeping their order.

    A leading slice is the wrong sample here. Names arrive sorted, and a plate is
    routinely written so that one slot varies slowest — channel-first filenames,
    or a per-well archive holding all of one channel's fields before the next.
    The first N names of such a listing can hold a single channel token, and a
    slot with one distinct value is not recognised as the channel slot at all, so
    the scan reports no channels rather than the wrong ones. Striding the listing
    reaches every value of the slowest-varying slot whatever it happens to be.
    """
    if take <= 0 or not names:
        return []
    if len(names) <= take:
        return list(names)
    step = len(names) / take
    return [names[int(i * step)] for i in range(take)]


def _tif_stems_in(root: Path, limit: int) -> list[str]:
    if not root.is_dir():
        return []
    stems = [f.stem for f in sorted(root.iterdir())
             if f.is_file() and f.suffix.lower() in _TIF_SUFFIXES]
    return _spread(stems, limit)


def _zip_stems_in(root: Path, limit: int) -> list[str]:
    """Sample member stems across the folder's zips — a few from each, so the
    sample spans multiple wells (each per-well zip holds one well) as well as
    every channel within a well."""
    stems: list[str] = []
    zips = sorted(root.glob("*.zip")) if root.is_dir() else []
    per_zip = max(4, (limit // max(1, len(zips))) if zips else limit)
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                members = [Path(m).stem for m in zf.namelist()
                           if m.lower().endswith(_TIF_SUFFIXES)]
        except (zipfile.BadZipFile, OSError):
            continue
        stems.extend(_spread(members, min(per_zip, limit - len(stems))))
        if len(stems) >= limit:
            break
    return stems


def _well_folder_stems_in(root: Path, limit: int) -> list[str]:
    """Sample TIF stems out of *root*'s per-well ``<WELL>/`` subfolders.

    The third of the three grouped layouts Analyze accepts, and the one that
    used to yield nothing: a dataset laid out as ``in/<WELL>/*.tif`` has no
    loose TIFs and no zips at either level, so the schema and channel fields
    stayed blank and the run then failed on a schema the user was never
    prompted to fill in.

    Takes a few names from each well the way :func:`_zip_stems_in` takes a few
    members from each zip, and strides each well's listing through
    :func:`_spread` for the same reason it does — a well folder is written with
    one slot varying slowest, so its leading names can hold a single channel
    token and a slot with one distinct value is not recognised as the channel
    slot at all.
    """
    try:
        wells = sorted(
            p for p in root.iterdir()
            if p.is_dir() and is_valid_well_name(p.name)
        ) if root.is_dir() else []
    except OSError:
        return []
    stems: list[str] = []
    per_well = max(4, (limit // max(1, len(wells))) if wells else limit)
    for well in wells:
        try:
            names = [f.stem for f in sorted(well.iterdir())
                     if f.is_file() and f.suffix.lower() in _TIF_SUFFIXES]
        except OSError:
            continue
        stems.extend(_spread(names, min(per_well, limit - len(stems))))
        if len(stems) >= limit:
            break
    return stems


def sample_stems(input_dir: Path | str, *, limit: int = _SAMPLE_LIMIT) -> list[str]:
    """Return up to *limit* filename stems from *input_dir* (or its ``in/`` child).

    Prefers loose TIFs, then members of the folder's per-well zips, then the
    files inside its per-well folders — the three grouped layouts
    ``services.input_resolution_service.plan_input_layout`` accepts, in the
    order that costs least to read. Each tier looks in ``input_dir`` and
    ``input_dir/in``, since the user may select either the dataset folder or the
    ``in/`` inside it.
    """
    base = Path(input_dir)
    for sample in (_tif_stems_in, _zip_stems_in, _well_folder_stems_in):
        for root in (base, base / "in"):
            stems = sample(root, limit)
            if stems:
                return stems
    return []


# ── slot classification ──────────────────────────────────────────────────────
def _fraction(col: list[str], pred) -> float:
    return sum(1 for t in col if pred(t)) / len(col) if col else 0.0


def _best_slot(cols, pred, used: set[int]) -> int | None:
    """The unused column index best matching *pred* above the min fraction.

    Ties go to the **leftmost** column. The comparison used to be ``>=``
    against a running best, which handed a tie to the *rightmost* one — and ties
    are the normal case, not an edge case: two slots that both parse perfectly
    both score 1.0. On the default ``experiment:channel:well:fov:timepoint``
    layout that gave the ``well`` role to the FOV slot, because ``f01``–``f12``
    are also valid 96-well labels (row F), so the real well column was left
    unclassified and every image was attributed to well ``F01``.
    """
    best_i, best_f = None, None
    for i, col in enumerate(cols):
        if i in used:
            continue
        f = _fraction(col, pred)
        if f < _MIN_SLOT_FRACTION:
            continue
        if best_f is None or f > best_f:
            best_i, best_f = i, f
    return best_i


def _pick_channel_slot(cols, used: set[int]) -> int | None:
    """The unused slot with the fewest distinct values that reads as a small
    alphabetic categorical set; leftmost on a tie.

    Fewest-distinct rather than leftmost outright: a plate has more wells than
    channels, so on any layout where both slots survive the earlier passes the
    channel slot is the narrower one. The earlier passes are what make that safe
    — the well slot is claimed by :func:`_classify_slots` before this runs, and
    when it is not (a folder with fewer wells than channels), the narrower slot
    genuinely is the better guess."""
    best_i, best_distinct = None, None
    for i, col in enumerate(cols):
        if i in used:
            continue
        distinct = list(dict.fromkeys(col))
        if not (2 <= len(distinct) <= 12):
            continue
        if not all(_HAS_ALPHA_RE.search(v) for v in distinct):
            continue
        if best_distinct is None or len(distinct) < best_distinct:
            best_i, best_distinct = i, len(distinct)
    return best_i


def _classify_slots(rows: list[list[str]], k: int) -> tuple[list[str], int | None, int | None]:
    cols = [[r[i] for r in rows] for i in range(k)]
    fields = ["ignore"] * k
    used: set[int] = set()

    # Order matters, and it runs strictest-predicate-first. ``t<n>`` and
    # ``f<n>`` can only be what they look like; a well label cannot. The reverse
    # is not true — ``f01``..``f12`` *are* valid well labels — so classifying
    # well first let the FOV slot answer a question it is not entitled to
    # answer, and the real well column was then left over as ``ignore``.
    tp_i = _best_slot(cols, _is_timepoint, used)
    if tp_i is not None:
        fields[tp_i] = "timepoint"; used.add(tp_i)
    fov_i = _best_slot(cols, _is_fov, used)
    if fov_i is not None:
        fields[fov_i] = "fov"; used.add(fov_i)
    well_i = _best_slot(cols, _is_well, used)
    if well_i is not None:
        fields[well_i] = "well"; used.add(well_i)
    for i in range(k):
        if i not in used and len(set(cols[i])) == 1:
            fields[i] = "experiment"; used.add(i)
            break
    chan_i = _pick_channel_slot(cols, used)
    if chan_i is not None:
        fields[chan_i] = "channel"; used.add(chan_i)
    return fields, well_i, chan_i


def _channels_in_order(rows: list[list[str]], chan_i: int) -> list[str]:
    return list(dict.fromkeys(r[chan_i] for r in rows))


def infer_schema(stems, *, default_sep: str = DEFAULT_SEP) -> InferredSchema | None:
    """Infer an :class:`InferredSchema` from filename *stems*, or ``None``.

    Tries each candidate separator, keeps the names that split into the modal
    field count, classifies the slots, and scores the result by whether a well and
    a channel slot were found (then by how many names split consistently). Returns
    ``None`` only when there is nothing to work with.
    """
    stems = [s for s in stems if s]
    if not stems:
        return None
    best: InferredSchema | None = None
    best_score = (-1, -1)
    for sep in _SEP_CANDIDATES:
        splits = [s.split(sep) for s in stems]
        counts = Counter(len(p) for p in splits)
        k, freq = counts.most_common(1)[0]
        if k < 2:
            continue
        rows = [p for p in splits if len(p) == k]
        fields, well_i, chan_i = _classify_slots(rows, k)
        channels = _channels_in_order(rows, chan_i) if chan_i is not None else []
        score = ((well_i is not None) + (chan_i is not None), freq)
        if score > best_score:
            best_score = score
            best = InferredSchema(
                separator=sep, fields=fields, channels=channels,
                confident=well_i is not None and chan_i is not None,
            )
    return best


def infer_from_dir(input_dir: Path | str) -> InferredSchema | None:
    """Sample *input_dir* and infer its schema, or ``None`` if nothing was found."""
    stems = sample_stems(input_dir)
    return infer_schema(stems) if stems else None
