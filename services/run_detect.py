"""Detect an Analyze run's filename layout and channel roles from its input.

The Analyze form can read the schema, separator and channel tokens off the
files in an input folder (``util.schema_infer``), but only while the form is
open and the folder exists. A queued job can be set up long before either is
true: its input may be the folder a converter earlier in the queue has not
written yet. So a run can carry :data:`AUTO_DETECT_KEY` instead of trusting the
values the form held when it was queued, and :func:`resolve_detection` fills
them in from the input when the run starts — in ``AnalyzeRunMixin.start_run``,
which every entry point (Run, the queue, the resume banner) passes through.

Two rules decide what detection may write, and both are shared with the form's
own **Detect from filenames** through :func:`scan_pipeline_info`:

* the schema and separator only when the guess is *confident* — a well slot
  and a channel slot were both placed;
* the channel roles only when channels were found at all.

The form applies whatever part of that it gets, because a person is looking at
the result and can correct it. A run nobody is watching cannot, so
:func:`resolve_detection` requires the whole of it and raises
:class:`DetectionError` naming what was missing. It never falls back to the
values frozen into the job: those are exactly the values the job asked not to
use, and a run that silently used them would write a complete-looking result
for the wrong channels (CLAUDE.md §7).

When the job was chained after a converter, the queue hands over the layout the
converter *wrote* (:data:`UPSTREAM_KEY`, filled by
``services.job_queue.JobQueue.materialise``). That is knowledge rather than a
guess — the converter named the files and the user assigned the roles on its
page — so it is preferred, and the scan is used to check it against the files
on disk and to fill whatever the converter could not say.

Qt-free, like everything in ``services/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from util.channel_roles import (
    ChannelTokens,
    LABELFREE_METHODS,
    channel_tokens_from_roles,
    default_channel_roles,
    phase_role_conflicts,
)
from util.schema_infer import InferredSchema, _spread, infer_schema, sample_stems

#: The ``RunSpec`` JSON key that asks for detection at run time. Beside
#: ``opts`` rather than inside it, so it can never reach the pipeline's argv.
AUTO_DETECT_KEY = "auto_detect"

#: The spec key :meth:`services.job_queue.JobQueue.materialise` writes when a
#: converter earlier in the chain recorded the layout it produced.
UPSTREAM_KEY = "upstream_layout"

#: The ``opts`` keys detection owns. Everything it writes is one of these, and
#: nothing else in ``opts`` is touched.
DETECTED_OPTS = ("filename_schema", "filename_sep", "nuclear_token",
                 "fluor_tokens", "phase_token")


class DetectionError(Exception):
    """Detection could not decide. The message is user-facing and names why."""


@dataclass
class DetectedLayout:
    """What a run's input was found to carry, in ``opts`` vocabulary."""

    schema: str
    separator: str
    nuclear_token: str
    fluor_tokens: list[str] = field(default_factory=list)
    phase_token: str = ""
    #: Where the channel roles came from: ``"scan"`` or ``"upstream"``.
    channels_from: str = "scan"
    #: Where the schema came from, the same two words.
    schema_from: str = "scan"
    #: Why the segmentation channel is the one it is: ``"phase"`` (label-free),
    #: ``"upstream"`` (the converter's roles), ``"form"`` (the form's own
    #: Segmentation channel, which the input carries) or ``"names"`` (chosen
    #: from the channel names).
    segmentation_from: str = "names"
    #: The guess's own statement of the sample it did not match, or ``""``.
    shortfall: str = ""

    def as_opts(self) -> dict:
        return {
            "filename_schema": self.schema,
            "filename_sep": self.separator,
            "nuclear_token": self.nuclear_token,
            "fluor_tokens": list(self.fluor_tokens),
            "phase_token": self.phase_token,
        }

    def summary(self) -> str:
        """One line for the run log and the finished job's queue row."""
        parts = [f"schema {self.schema} (separator {self.separator!r})",
                 f"channels {', '.join(self.fluor_tokens) or 'none'}",
                 f"segmentation channel {self.nuclear_token}"
                 + _SEGMENTATION_WHY.get(self.segmentation_from, "")]
        if self.phase_token:
            parts.append(f"phase {self.phase_token}")
        text = "Detected " + "; ".join(parts)
        sources = {self.schema_from, self.channels_from}
        if "upstream" in sources:
            what = ("layout and channel roles" if sources == {"upstream"}
                    else "layout" if self.schema_from == "upstream"
                    else "channel roles")
            text += f" — {what} as recorded by the converter this job ran after"
        text += "."
        if self.shortfall:
            text += f" {self.shortfall}"
        return text


_SEGMENTATION_WHY = {
    "phase": " (the phase channel, for label-free)",
    "form": " (as set on the form)",
    "names": " (chosen from the channel names)",
}


def wants_detection(spec: dict) -> bool:
    """Whether a ``RunSpec`` JSON dict asks for run-time detection."""
    return bool((spec or {}).get(AUTO_DETECT_KEY))


def scan_pipeline_info(guess: InferredSchema) -> tuple[dict, ChannelTokens | None]:
    """What a folder scan may write, as ``pipeline_info``-shaped keys.

    The one statement of the confidence rule, used by the form's scan and by
    :func:`resolve_detection` alike: the schema only from a confident guess,
    the channel roles only when channels were found. Returns the dict and the
    channel assignment (``None`` when no channel slot was found) so a caller
    can report both halves.
    """
    info: dict = {}
    if guess.confident:
        info = {"separator": guess.separator, "schema": guess.schema_str}
    tokens = guess.channel_tokens() if guess.channels else None
    if tokens is not None:
        info.update(tokens.as_pipeline_info())
    return info, tokens


def _upstream_tokens(upstream: dict) -> ChannelTokens | None:
    nuclear = str(upstream.get("nuclear_token") or "").strip()
    fluor = [str(t).strip() for t in (upstream.get("fluor_tokens") or []) if str(t).strip()]
    phase = str(upstream.get("phase_token") or "").strip()
    if not nuclear and not fluor and not phase:
        return None
    return ChannelTokens(nuclear_token=nuclear, fluor_tokens=fluor, phase_token=phase)


#: How many names the schema is inferred from. Well above the form's
#: ``_SAMPLE_LIMIT`` — this runs once per run rather than per keystroke, and
#: measured at ~0.27 s for 20,000 names — so a pattern present in a small
#: minority of the plate still reaches the classifier.
_INFER_SAMPLE = 20_000

#: Every name is read for the channel list, up to this many. The channel list
#: decides what gets quantified, so a sample is the wrong source for it: a
#: channel present in some wells only, or one a stride happens to skip, would
#: be dropped from the run without a word. Reading every name costs no more
#: file I/O than the sample does — the sampler already opens every archive and
#: lists every folder — only the splitting.
_ENUMERATE_LIMIT = 500_000


def _channel_values(stems: list[str], separator: str, schema: str) -> list[str]:
    """Every distinct channel-slot value among *stems*, in first-seen order."""
    fields = [f.strip() for f in schema.split(":")]
    if "channel" not in fields:
        return []
    k, ci = len(fields), fields.index("channel")
    out: dict[str, None] = {}
    for stem in stems:
        parts = stem.split(separator)
        if len(parts) == k and parts[ci]:
            out.setdefault(parts[ci], None)
    return list(out)


def _cell_body_cellpose(method: str, cellpose_model: str) -> bool:
    """General Cellpose with a model that segments whole cells, not nuclei."""
    return method == "cellpose" and str(cellpose_model or "").strip().lower() != "nuclei"


def detect_layout(
    raw: Path | str,
    upstream: dict | None = None,
    *,
    method: str = "",
    requested_segmentation: str = "",
    cellpose_model: str = "",
) -> DetectedLayout:
    """Everything detection can establish about *raw*, or :class:`DetectionError`.

    The **schema** comes from *upstream* when a converter earlier in the chain
    stated it, and otherwise from a confident guess over a dense sample of the
    input's names. The **channel list** always comes from every name, never a
    sample (:data:`_ENUMERATE_LIMIT`).

    The **segmentation channel** depends on the method, because "which channel
    is segmented" is a choice, not a fact the filenames carry:

    * label-free methods segment the phase channel — the pipeline itself
      remaps the segmentation slot onto it — so that is what is written, and
      every other channel is quantified;
    * otherwise the converter's roles, when there are any, then
      *requested_segmentation* (the form's own Segmentation channel) when the
      input carries it, then the name heuristic in ``util.channel_roles``;
    * general Cellpose with a cell-body model is refused when only the
      heuristic could choose, because the heuristic picks a nuclear stain and
      that model would then segment nuclei as cells without complaint.

    The converter's channel names are checked against the enumerated list;
    a job edited after it was chained, or a folder written into by hand,
    would otherwise run under roles naming channels the files lack.
    """
    upstream = dict(upstream or {})
    name = Path(str(raw)).name or str(raw)
    try:
        stems = sample_stems(raw, limit=_ENUMERATE_LIMIT)
    except OSError as exc:
        raise DetectionError(
            f"The input folder {name!r} could not be read to detect its "
            f"filename layout: {exc}") from exc
    if not stems:
        raise DetectionError(
            f"No image filenames were found in {name!r} to detect a filename "
            "layout from. Check that the job before this one wrote its output "
            "there, or turn off run-time detection and set the schema and "
            "channels by hand.")
    guess = infer_schema(_spread(stems, _INFER_SAMPLE))
    if guess is None:
        raise DetectionError(f"No filename layout could be read from {name!r}.")

    schema = str(upstream.get("schema") or "").strip()
    separator = str(upstream.get("separator") or "")
    schema_from = "upstream" if schema and separator else "scan"
    if schema_from == "scan":
        if not guess.confident:
            raise DetectionError(
                f"Couldn't read a filename layout from {name!r}: "
                f"{guess.summary()} No well and channel pair was identified. "
                "Turn off run-time detection and set the schema and channels "
                "by hand for this job.")
        schema, separator = guess.schema_str, guess.separator

    carried = _channel_values(stems, separator, schema)
    if not carried:
        raise DetectionError(
            f"No channel names could be read from the filenames in {name!r}.")

    up = _upstream_tokens(upstream)
    if up is not None:
        named = [t for t in (up.nuclear_token, *up.fluor_tokens, up.phase_token) if t]
        missing = [t for t in dict.fromkeys(named) if t not in carried]
        if missing:
            raise DetectionError(
                f"The converter this job ran after assigned roles to "
                f"{', '.join(missing)}, but the files in {name!r} carry "
                f"{', '.join(carried)}. Turn off run-time detection and set "
                "this job's channels by hand.")
        roles, channels_from = up, "upstream"
        in_play = [c for c in carried if c in set(named)]
    else:
        roles = channel_tokens_from_roles(
            [(c, r) for c, r in default_channel_roles(carried).items()])
        channels_from, in_play = "scan", list(carried)

    phase = roles.phase_token
    if method in LABELFREE_METHODS:
        if not phase:
            raise DetectionError(
                "Label-free segmentation needs a phase/brightfield channel, and "
                f"none of the channels in {name!r} ({', '.join(carried)}) is "
                "marked or named as one.")
        nuclear, seg_from = phase, "phase"
    elif up is not None and up.nuclear_token:
        nuclear, seg_from = up.nuclear_token, "upstream"
    elif requested_segmentation and requested_segmentation in in_play:
        nuclear, seg_from = requested_segmentation, "form"
    else:
        nuclear, seg_from = roles.nuclear_token, "names"
        if _cell_body_cellpose(method, cellpose_model):
            raise DetectionError(
                f"Cellpose ({cellpose_model or 'cell-body model'}) segments whole "
                f"cells, and the Segmentation channel on the form "
                f"({requested_segmentation or 'blank'}) is not one of the "
                f"channels in {name!r} ({', '.join(carried)}). Name the "
                "cell-body channel there; a channel chosen from the names "
                "would be a nuclear stain.")
    if not nuclear:
        raise DetectionError(
            f"No channel in {name!r} could be given the segmentation role.")

    if method in LABELFREE_METHODS:
        fluor = list(in_play)          # the phase channel is quantified too
    else:
        fluor = [c for c in in_play if c != nuclear]
    if not fluor:
        raise DetectionError(
            f"Only one channel ({nuclear}) was found in {name!r}; a run needs "
            "at least one fluorescent channel besides the segmentation channel.")
    return DetectedLayout(
        schema=schema, separator=separator, nuclear_token=nuclear,
        fluor_tokens=fluor, phase_token=phase,
        channels_from=channels_from, schema_from=schema_from,
        segmentation_from=seg_from,
        shortfall=guess.shortfall() if schema_from == "scan" else "",
    )


def channel_option_errors(opts: dict) -> list[str]:
    """The options that name a channel the resolved run does not have.

    A queued job's segmentation settings were chosen against whatever channels
    the form held when it was queued. Once detection has replaced those, every
    option that names a channel has to be checked against the replacement,
    **the way the pipeline matches it**: smFISH and the watershed cytoplasm
    channel by exact membership of the fluorescent list
    (``process_microscopy``'s ``tok in set(smfish_tokens)`` and
    ``cytoplasm_token not in fluor_tokens``), the Cellpose nuclei channel
    case-insensitively. A case-insensitive check on the first two passed a
    ``gfp`` smFISH mark that the pipeline then silently never applied, and a
    ``mcherry`` cytoplasm channel that failed every field of the run. The
    Cellpose nuclei channel matters most of all: absent from the list, the
    pipeline ignores it silently and segments without the nucleus image it
    was asked for.
    """
    nuclear = str(opts.get("nuclear_token") or "").strip()
    fluor = [str(t) for t in (opts.get("fluor_tokens") or [])]
    have = ", ".join(fluor) or "none"
    method = str(opts.get("segmentation_method") or "")
    errors: list[str] = []

    smfish = [str(t).strip() for t in (opts.get("smfish_tokens") or []) if str(t).strip()]
    stray = [t for t in smfish if t not in fluor]
    if stray:
        errors.append(
            f"smFISH is marked on {', '.join(stray)}, which the input's "
            f"fluorescent channels ({have}) do not include.")

    if method == "stardist_seeded_watershed_cell":
        cyto = str(opts.get("cytoplasm_token") or "").strip()
        if not cyto:
            errors.append("Watershed mode requires a cytoplasm token.")
        elif cyto == nuclear:
            errors.append(
                f"The segmentation channel is {nuclear!r}, which is also the "
                "cytoplasm channel. Cytoplasm token must differ from nuclear token.")
        elif cyto not in fluor:
            errors.append(
                f"The cytoplasm channel is {cyto!r}, which is not one of the "
                f"input's fluorescent channels ({have}).")

    nuc_channel = str(opts.get("cellpose_nuclei_channel") or "").strip()
    if method.startswith("cellpose") and nuc_channel \
            and nuc_channel.lower() not in {t.lower() for t in fluor}:
        where = ("is the segmentation channel, not a fluorescent one"
                 if nuc_channel.lower() == nuclear.lower()
                 else f"is not one of the input's fluorescent channels ({have})")
        errors.append(f"The Cellpose nuclei channel is {nuc_channel!r}, which {where}.")

    phase = str(opts.get("phase_token") or "").strip()
    for slot in phase_role_conflicts(
            method, phase_token=phase, nuclear_token=nuclear,
            cytoplasm_token=str(opts.get("cytoplasm_token") or "")):
        errors.append(
            f"The phase channel {phase!r} is also the {slot} channel; a "
            "fluorescence routine cannot segment a brightfield image.")
    return errors


def missing_input_error(spec: dict) -> str | None:
    """Why a run cannot start for want of an input folder, or ``None``.

    A queued job may be added with no input so that chaining it after another
    job can supply one. Reaching the start of a run still without one is then
    a job that was never chained, and saying so beats the pipeline's own error
    about a path of ``.``.
    """
    raw = str(((spec or {}).get("opts") or {}).get("raw") or "").strip()
    if raw:
        return None
    return ("This Analyze job has no input folder. Chain it after a job that "
            "produces a dataset (the row's Run after… menu), or edit it to "
            "name one.")


def resolve_detection(spec: dict) -> tuple[dict, DetectedLayout | None]:
    """Return a copy of *spec* with detection applied, and what was detected.

    A spec that does not ask for detection comes back unchanged with ``None``.
    One that does has every key in :data:`DETECTED_OPTS` replaced from the
    input, and is then checked with :func:`channel_option_errors`. Anything
    detection cannot establish raises :class:`DetectionError`.
    """
    if not wants_detection(spec):
        return spec, None
    raw = str((spec.get("opts") or {}).get("raw") or "").strip()
    if not raw:
        raise DetectionError(missing_input_error(spec) or "")
    opts = dict(spec.get("opts") or {})
    layout = detect_layout(
        raw, spec.get(UPSTREAM_KEY),
        method=str(opts.get("segmentation_method") or ""),
        requested_segmentation=str(opts.get("nuclear_token") or "").strip(),
        cellpose_model=str(opts.get("cellpose_model") or ""),
    )
    out = dict(spec)
    opts.update(layout.as_opts())
    out["opts"] = opts
    errors = channel_option_errors(opts)
    if errors:
        raise DetectionError(
            f"{layout.summary()} But this job's settings do not fit those "
            "channels:\n" + "\n".join(f"• {e}" for e in errors))
    return out, layout
