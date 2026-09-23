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
    phase_role_conflicts,
)
from util.schema_infer import InferredSchema, infer_from_dir

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
                 f"segmentation channel {self.nuclear_token}"]
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


def _lower(tokens) -> list[str]:
    return [str(t or "").strip().lower() for t in tokens if str(t or "").strip()]


def _upstream_tokens(upstream: dict) -> ChannelTokens | None:
    nuclear = str(upstream.get("nuclear_token") or "").strip()
    fluor = [str(t).strip() for t in (upstream.get("fluor_tokens") or []) if str(t).strip()]
    if not nuclear and not fluor:
        return None
    return ChannelTokens(nuclear_token=nuclear, fluor_tokens=fluor,
                         phase_token=str(upstream.get("phase_token") or "").strip())


def detect_layout(raw: Path | str, upstream: dict | None = None) -> DetectedLayout:
    """Everything detection can establish about *raw*, or :class:`DetectionError`.

    *upstream* is the layout a converter earlier in the chain recorded, when
    there is one. It wins over the scan for the parts it states; the scan
    still has to agree with it about which channels the files carry, because
    a converter job that was edited after this one was chained, or a folder
    somebody wrote into by hand, would otherwise run under roles that name
    channels the files do not have.
    """
    upstream = dict(upstream or {})
    name = Path(str(raw)).name or str(raw)
    try:
        guess = infer_from_dir(raw)
    except OSError as exc:
        raise DetectionError(
            f"The input folder {name!r} could not be read to detect its "
            f"filename layout: {exc}") from exc
    if guess is None or guess.sampled == 0:
        raise DetectionError(
            f"No image filenames were found in {name!r} to detect a filename "
            "layout from. Check that the job before this one wrote its output "
            "there, or turn off run-time detection and set the schema and "
            "channels by hand.")

    schema = str(upstream.get("schema") or "").strip()
    separator = str(upstream.get("separator") or "")
    schema_from = "upstream" if schema and separator else "scan"
    if schema_from == "scan":
        if not guess.confident:
            raise DetectionError(
                f"Couldn't read a filename layout from {name!r}: "
                f"{guess.summary()} No well and channel pair was identified. "
                "Set the schema and channels by hand for this job.")
        schema, separator = guess.schema_str, guess.separator

    tokens = _upstream_tokens(upstream)
    channels_from = "upstream" if tokens is not None else "scan"
    if tokens is None:
        _info, tokens = scan_pipeline_info(guess)
        if tokens is None:
            raise DetectionError(
                f"No channel names could be read from the filenames in {name!r}. "
                "Set the channels by hand for this job.")
    elif guess.channels:
        carried = set(_lower(guess.channels))
        named = [tokens.nuclear_token, *tokens.fluor_tokens]
        missing = [t for t in named if t and t.lower() not in carried]
        if missing:
            raise DetectionError(
                f"The converter this job ran after assigned roles to "
                f"{', '.join(missing)}, but the files in {name!r} carry "
                f"{', '.join(guess.channels)}. Edit the job's channels by hand.")

    if not tokens.nuclear_token:
        raise DetectionError(
            f"No channel in {name!r} could be given the segmentation role.")
    if not tokens.fluor_tokens:
        raise DetectionError(
            f"Only one channel ({tokens.nuclear_token}) was found in {name!r}; "
            "a run needs at least one fluorescent channel besides the "
            "segmentation channel.")
    return DetectedLayout(
        schema=schema, separator=separator,
        nuclear_token=tokens.nuclear_token,
        fluor_tokens=list(tokens.fluor_tokens),
        phase_token=tokens.phase_token,
        channels_from=channels_from, schema_from=schema_from,
        shortfall=guess.shortfall() if schema_from == "scan" else "",
    )


def channel_option_errors(opts: dict) -> list[str]:
    """The options that name a channel the resolved run does not have.

    A queued job's segmentation settings were chosen against whatever channels
    the form held when it was queued. Once detection has replaced those, every
    option that names a channel has to be checked against the replacement:
    ``cellpose_nuclei_channel`` in particular is looked up in the pipeline
    and, when absent, silently ignored (``process_microscopy``'s Cellpose
    branch) — so a stale one does not fail, it quietly segments without the
    nucleus image it was asked for.
    """
    nuclear = str(opts.get("nuclear_token") or "").strip()
    fluor = [str(t) for t in (opts.get("fluor_tokens") or [])]
    carried = set(_lower([nuclear, *fluor]))
    method = str(opts.get("segmentation_method") or "")
    errors: list[str] = []

    smfish = [t for t in (opts.get("smfish_tokens") or []) if str(t).strip()]
    stray = [t for t in smfish if str(t).strip().lower() not in set(_lower(fluor))]
    if stray:
        errors.append(
            f"smFISH is marked on {', '.join(stray)}, which the input's "
            f"fluorescent channels ({', '.join(fluor) or 'none'}) do not include.")

    if method == "stardist_seeded_watershed_cell":
        cyto = str(opts.get("cytoplasm_token") or "").strip()
        if not cyto:
            errors.append("Watershed mode requires a cytoplasm token.")
        elif cyto.lower() not in carried:
            errors.append(
                f"The cytoplasm channel is {cyto!r}, which the input does not "
                f"carry (it has {', '.join([nuclear, *fluor])}).")
        elif cyto.lower() == nuclear.lower():
            errors.append(
                f"The detected segmentation channel is {nuclear!r}, which is "
                "also the cytoplasm channel. Cytoplasm token must differ from "
                "nuclear token.")

    if method in LABELFREE_METHODS and not str(opts.get("phase_token") or "").strip():
        errors.append(
            "Label-free segmentation needs a phase/brightfield channel, and "
            "none of the input's channels reads as one "
            f"({', '.join([nuclear, *fluor])}).")

    nuc_channel = str(opts.get("cellpose_nuclei_channel") or "").strip()
    if method.startswith("cellpose") and nuc_channel \
            and nuc_channel.lower() not in set(_lower(fluor)):
        errors.append(
            f"The Cellpose nuclei channel is {nuc_channel!r}, which is not one "
            f"of the input's fluorescent channels ({', '.join(fluor) or 'none'}).")

    phase = str(opts.get("phase_token") or "").strip()
    for slot in phase_role_conflicts(
            method, phase_token=phase, nuclear_token=nuclear,
            cytoplasm_token=str(opts.get("cytoplasm_token") or "")):
        errors.append(
            f"The detected phase channel {phase!r} is also the {slot} "
            "channel; a fluorescence routine cannot segment a brightfield image.")
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
    layout = detect_layout(raw, spec.get(UPSTREAM_KEY))
    out = dict(spec)
    opts = dict(spec.get("opts") or {})
    opts.update(layout.as_opts())
    out["opts"] = opts
    errors = channel_option_errors(opts)
    if errors:
        raise DetectionError(
            f"{layout.summary()} But this job's settings do not fit those "
            "channels:\n" + "\n".join(f"• {e}" for e in errors))
    return out, layout
