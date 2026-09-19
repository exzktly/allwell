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
from util.dir_scan import files_in, subdirs_in
from util.image_exts import is_run_input_member
from util.fov import parse_fov
from util.schema import DEFAULT_SEP
from util.timepoint import parse_hours
from util.well_token import canonical_well_label, is_valid_well_name

_SEP_CANDIDATES = ("_", "-", ".")
_MIN_SLOT_FRACTION = 0.7      # a slot must parse this uniformly to earn a role
_SAMPLE_LIMIT = 400           # names sampled — "briefly scanning", not a full walk
_MISS_EXAMPLES = 3            # unparsed names quoted in the readout, not counted

_TP_RE = re.compile(r"(?i)^t\d+$")
_FOV_RE = re.compile(r"(?i)^f\d+$")
_HAS_ALPHA_RE = re.compile(r"[A-Za-z]")
_BARE_NUM_RE = re.compile(r"^\d+$")


@dataclass
class InferredSchema:
    """The schema + channels guessed from a folder's filenames."""

    separator: str
    fields: list[str] = field(default_factory=list)   # per slot; SCHEMA_FIELDS vocab
    channels: list[str] = field(default_factory=list)  # distinct channel tokens, in order
    confident: bool = False   # a well AND a channel slot were both identified
    #: How many filename stems were sampled to reach this guess.
    sampled: int = 0
    #: How many of them parse *under this guess* — they split into the right
    #: number of tokens and every slot the guess claimed accepts its token.
    matched: int = 0
    #: Up to :data:`_MISS_EXAMPLES` of the ones that did not, each paired with
    #: the reason, so a user reading the readout can see what the guess trips
    #: over rather than only how often.
    misses: tuple[tuple[str, str], ...] = ()
    #: How many of the unmatched names failed on the **well** position
    #: specifically. Tracked apart from the rest because it is the only
    #: mismatch with a consequence the software can state: grouping places a
    #: file by its well token, so a name whose well does not parse lands in no
    #: archive. See :meth:`shortfall`.
    well_misses: int = 0

    @property
    def schema_str(self) -> str:
        return ":".join(self.fields)

    def channel_tokens(self) -> ChannelTokens:
        """The channel role assignment seeded from the guessed channel tokens."""
        roles = default_channel_roles(self.channels)
        return channel_tokens_from_roles([(c, roles[c]) for c in self.channels])

    def summary(self) -> str:
        """One neutral sentence: what was detected, and how much of the sample
        it matched.

        The whole reason this exists: the guess is applied to two form fields
        "whose new values look no different from hand-entered ones"
        (``analyze/domain/input.py``), so without a sentence saying what it was
        derived from and how well it fits, a wrong guess is invisible until it
        turns up in a sidecar after a run.

        Says *matched* rather than *parsed*. "Parse" is programmer vocabulary,
        and the app already has a word for this — the Zip Loose Folder page has
        always said "didn't match the schema". One word per concept.
        """
        head = f"Detected {self.schema_str}, separator {self.separator!r}."
        if not self.sampled:
            return f"{head} No filenames were sampled."
        if self.matched == self.sampled:
            n = self.sampled
            return (f"{head} All {n:,} filename{'' if n == 1 else 's'} sampled "
                    f"matched.")
        return f"{head} Matched {self.matched:,} of {self.sampled:,} sampled."

    def shortfall(self) -> str:
        """The sampled names this guess does not fit, or ``""`` when there are none.

        Two things this sentence used to claim and could not support.

        It said the files **"will be skipped"**. That is true of a name whose
        *well* position does not parse — grouping places a file by its well
        token, so such a name lands in no archive — and false of everything
        else. The pipeline applies no predicate to the timepoint or FOV slots
        at all: ``process_microscopy._collect_available_schema_values`` reads
        those tokens **positionally, unvalidated**, and the only per-file test
        ``_is_nuclear_filename`` makes is on the channel slot. A plate whose
        fields were written ``fov01`` instead of ``f01`` processes in full while
        this sentence announced eight files lost, which would send someone off
        to rename files that are fine.

        And it said **"N files"** where ``N`` counts a *sample*: the scan reads
        at most :data:`_SAMPLE_LIMIT` names, drawn by stride. On a 2,304-image
        plate with one row mis-named, the honest 288 came out as 4. The
        qualifier :meth:`summary` already carries belongs here too, and more so,
        because this is the register styled so the reader cannot skim it.

        So the headline states the mismatch, which is measured, and the
        consequence is named only for the slot that has one.
        """
        missing = self.sampled - self.matched
        if missing <= 0:
            return ""
        verb = "does" if missing == 1 else "do"
        head = (f"{missing:,} of the {self.sampled:,} filenames sampled {verb} "
                f"not fit this layout")
        if self.misses:
            shown = "; ".join(f"{stem} — {why}" for stem, why in self.misses)
            more = missing - len(self.misses)
            tail = f", and {more:,} more" if more > 0 else ""
            body = f"{head}: {shown}{tail}."
        else:
            body = f"{head}."
        if self.well_misses:
            # Says "is not a valid well" rather than "does not parse" for the
            # same reason the headline says "matched" rather than "parsed":
            # one vocabulary, and not the programmer's one.
            body += (" A name whose well position is not a valid well is not "
                     "grouped into any archive, so it is not analysed.")
        return body


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


def _is_bare_number(tok: str) -> bool:
    """The predicate behind the bare-number FOV slot in :func:`_classify_slots`.

    Named rather than inlined so the match report can check such a slot with
    the same rule that claimed it.
    """
    return bool(_BARE_NUM_RE.match(tok or ""))


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
    stems = [f.stem for f in files_in(root) if is_run_input_member(f.name)]
    return _spread(stems, limit)


def _zip_stems_in(root: Path, limit: int) -> list[str]:
    """Sample member stems across the folder's zips — a few from each, so the
    sample spans multiple wells (each per-well zip holds one well) as well as
    every channel within a well."""
    stems: list[str] = []
    # A well archive is a file, the same rule `_well_zip_among` applies on the
    # planner side. Consistency rather than a fix: a *directory* named
    # `A01.zip` was handed to `zipfile.ZipFile`, raised, and was skipped by the
    # handler below, so nothing downstream ever saw it. Asked the same way in
    # both modules, it is simply never a candidate.
    zips = [p for p in files_in(root)
            if p.match("*.zip")] if root.is_dir() else []
    per_zip = max(4, (limit // max(1, len(zips))) if zips else limit)
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                members = [Path(m).stem for m in zf.namelist()
                           if is_run_input_member(m)]
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
        wells = [p for p in subdirs_in(root)
                 if is_valid_well_name(p.name)] if root.is_dir() else []
    except OSError:
        return []
    stems: list[str] = []
    per_well = max(4, (limit // max(1, len(wells))) if wells else limit)
    for well in wells:
        try:
            names = [f.stem for f in files_in(well)
                     if is_run_input_member(f.name)]
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


def _classify_slots(rows: list[list[str]], k: int):
    """``(fields, well_i, chan_i, checks)`` for *rows* split into *k* slots.

    ``checks`` maps a slot index to the predicate that earned it its role, and
    exists so :func:`_match_report` can ask each slot the same question that
    claimed it. Asking a different one is the out-of-domain mistake this module
    already has one scar from: the bare-number FOV pass below claims a slot
    ``_is_fov`` itself rejects, so a report checking ``_is_fov`` there would
    call every well in the plate unparsed. Only the slots claimed by a token
    predicate appear — ``channel`` and ``experiment`` are defined *from* the
    rows, so every row satisfies them by construction, and ``ignore`` asks
    nothing.
    """
    cols = [[r[i] for r in rows] for i in range(k)]
    fields = ["ignore"] * k
    used: set[int] = set()
    checks: dict[int, object] = {}

    # Order matters, and it runs strictest-predicate-first. ``t<n>`` and
    # ``f<n>`` can only be what they look like; a well label cannot. The reverse
    # is not true — ``f01``..``f12`` *are* valid well labels — so classifying
    # well first let the FOV slot answer a question it is not entitled to
    # answer, and the real well column was then left over as ``ignore``.
    tp_i = _best_slot(cols, _is_timepoint, used)
    if tp_i is not None:
        fields[tp_i] = "timepoint"; used.add(tp_i); checks[tp_i] = _is_timepoint
    fov_i = _best_slot(cols, _is_fov, used)
    if fov_i is not None:
        fields[fov_i] = "fov"; used.add(fov_i); checks[fov_i] = _is_fov
    well_i = _best_slot(cols, _is_well, used)
    if well_i is not None:
        fields[well_i] = "well"; used.add(well_i); checks[well_i] = _is_well
    for i in range(k):
        if i not in used and len(set(cols[i])) == 1:
            fields[i] = "experiment"; used.add(i)
            break
    chan_i = _pick_channel_slot(cols, used)
    if chan_i is not None:
        fields[chan_i] = "channel"; used.add(chan_i)

    # Last pass: a bare-number FOV slot. ``_is_fov`` accepts only ``f<n>``,
    # deliberately — a bare number also reads as a timepoint or an experiment
    # index, and letting the FOV predicate claim one would be the same
    # out-of-domain judgement that put every image in well F01. So a plate
    # written ``exp_gfp_A01_3_t2`` left the FOV slot as ``ignore``, and every
    # field of view in a well merged into one.
    #
    # Claimed here rather than in ``_is_fov`` because it is only decidable once
    # the other slots are taken: a leftover numeric column, with the timepoint
    # slot already found (so this is not it), with more than one distinct value
    # (so the single-value experiment rule already passed on it). Everything
    # else stays ``ignore``.
    if fov_i is None and tp_i is not None:
        for i in range(k):
            if i in used:
                continue
            col = cols[i]
            if len(set(col)) < 2:
                continue
            if all(_BARE_NUM_RE.match(v or "") for v in col):
                fields[i] = "fov"; fov_i = i; used.add(i)
                checks[i] = _is_bare_number
                break
    return fields, well_i, chan_i, checks


def _channels_in_order(rows: list[list[str]], chan_i: int) -> list[str]:
    return list(dict.fromkeys(r[chan_i] for r in rows))


#: Human wording per slot role, for the reason attached to an unmatched name.
#: The reason names the **position number** as well, because that is the word
#: the control itself uses — the picker's dropdowns are labelled "Position 1"
#: through "Position 5", so a message inventing "slot" for the same thing asks
#: the reader to translate a term they are looking at.
_ROLE_WORD = {"well": "a well", "timepoint": "a timepoint", "fov": "an FOV"}


def _match_report(stems: list[str], sep: str, k: int, fields: list[str],
                  checks: dict,
                  ) -> tuple[int, tuple[tuple[str, str], ...], int]:
    """How many of *stems* parse under this guess, and why the rest do not.

    A name parses when it splits into *k* tokens under *sep* **and** every slot
    the guess claimed by a token predicate accepts its token. The second half is
    the part worth having: a slot is claimed at
    :data:`_MIN_SLOT_FRACTION` agreement, so up to three names in ten can carry
    a token the slot's own rule rejects and the guess still looks clean. Those
    names reach the pipeline and are bucketed under whatever the token happens
    to parse as — which is exactly the kind of quiet shortfall a count makes
    visible.

    The predicates come from :func:`_classify_slots` rather than from the field
    name, so each slot is asked the question that claimed it.
    """
    matched = 0
    well_misses = 0
    misses: list[tuple[str, str]] = []
    for stem in stems:
        parts = stem.split(sep)
        if len(parts) != k:
            if len(misses) < _MISS_EXAMPLES:
                n_pos = len(parts)
                misses.append(
                    (stem, f"has {n_pos} position{'' if n_pos == 1 else 's'}, "
                           f"not {k}"))
            continue
        why = ""
        bad_field = ""
        for i, fld in enumerate(fields):
            pred = checks.get(i)
            if pred is not None and not pred(parts[i]):
                why = (f"position {i + 1} is {parts[i]!r}, not "
                       f"{_ROLE_WORD.get(fld, fld)}")
                bad_field = fld
                break
        if not why:
            matched += 1
            continue
        if bad_field == "well":
            well_misses += 1
        if len(misses) < _MISS_EXAMPLES:
            misses.append((stem, why))
    return matched, tuple(misses), well_misses


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
    best_ctx: tuple[str, int, list[str], dict] | None = None
    for sep in _SEP_CANDIDATES:
        splits = [s.split(sep) for s in stems]
        counts = Counter(len(p) for p in splits)
        k, freq = counts.most_common(1)[0]
        if k < 2:
            continue
        rows = [p for p in splits if len(p) == k]
        fields, well_i, chan_i, checks = _classify_slots(rows, k)
        channels = _channels_in_order(rows, chan_i) if chan_i is not None else []
        score = ((well_i is not None) + (chan_i is not None), freq)
        if score > best_score:
            best_score = score
            best_ctx = (sep, k, fields, checks)
            best = InferredSchema(
                separator=sep, fields=fields, channels=channels,
                confident=well_i is not None and chan_i is not None,
                sampled=len(stems),
            )
    # Counted once, for the winner only. Scoring still uses `freq` — how many
    # names split consistently — because changing what picks the separator is a
    # change to which schema is proposed, and this row only adds a statement
    # about the schema already being proposed.
    if best is not None and best_ctx is not None:
        sep, k, fields, checks = best_ctx
        best.matched, best.misses, best.well_misses = _match_report(
            stems, sep, k, fields, checks)
    return best


def infer_from_dir(input_dir: Path | str) -> InferredSchema | None:
    """Sample *input_dir* and infer its schema, or ``None`` if nothing was found."""
    stems = sample_stems(input_dir)
    return infer_schema(stems) if stems else None
