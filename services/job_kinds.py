"""What the Job Queue knows about each kind of job it can run.

Two tables, the chain plumbing, and two naming functions, all keyed by a job's
``kind``. They live here, Qt-free, rather than beside the queue page because none of it is a view
concern and all of it is worth testing without an event loop:

* :data:`KIND_LABEL` — the tool's human name.
* :func:`produced_for` — which of a finished job's three possible outputs
  (``out_dir`` / ``dataset_root`` / ``in_dir``) its controller's reported path
  should be recorded under.
* :data:`CHAIN_TARGET` — the dotted path *into a job's own spec* that a
  predecessor's output fills when the two are chained. This is the receiving
  end of the link :func:`produced_for` and :data:`CHAIN_KIND` describe the
  sending end of.

Icons are deliberately not here: those are a view concern and live with the
page that draws them.

:func:`describe` is what a queue row is actually called. Without it every row
of a kind reads identically — eight queued subsets all saying "Subset" — which
makes reordering, chaining and removing the right one guesswork. It is
basenames only, on the reasoning that a queue row is an identifier and not a
path; :func:`describe_detail` carries the full paths for the row's second line
and its tooltip.
"""

from __future__ import annotations

import json
from pathlib import Path

KIND_LABEL = {
    "analyze": "Analyze",
    "concatenate": "Concatenate",
    "ome_import": "Convert OME-TIFF",
    "czi_import": "Convert CZI",
    "nd2_import": "Convert ND2",
    "validate": "Validate",
    "subset": "Subset",
    "import_loose": "Zip loose",
    "merge_plates": "Merge plates",
}

# What a chained successor is always handed. Every tool here — Analyze
# included — takes a *dataset root*, the folder holding the `in/` + `out/`
# pair, and resolves its own way down from there. Nothing consumes a bare
# `out/`, which is why this is a constant rather than a per-kind table: a
# table invites picking the predecessor's most specific output, and handing
# Validate or Subset an `out/` folder makes them look for `out/in/` and find
# no wells at all.
CHAIN_KIND = "dataset_root"


def produced_for(kind: str, path: str) -> dict:
    """Record a finished job's output under every key it satisfies.

    A Datasets tool reports the dataset root it built. Analyze reports its
    `out/` folder (`AnalyzeTab` emits `_last_output_dir`), and the dataset
    root is that folder's parent — `services.input_resolution_service.
    resolve_input_output` returns `<root>/out` in every one of its layout
    branches, so the relationship holds by construction. Both are recorded:
    `out_dir` because it is what Analyze actually produced and is worth
    keeping on the job, `dataset_root` because it is what a successor needs.
    """
    if not path:
        return {}
    if kind == "analyze":
        return {"out_dir": path, "dataset_root": str(Path(path).parent)}
    return {"dataset_root": path}


# Where a predecessor's output lands in this job's spec. For the two-dataset
# tools it is the *first* dataset: chaining means "carry this result forward",
# and the result is what the combination starts from.
#: The kinds that write a dataset from a packed acquisition file, naming every
#: image themselves. Those names are ``<experiment>_<channel>_<well>_f<fov>_<tp>``
#: (``pipeline.explode_common`` and each ``*_explode`` module), with every
#: token passed through ``util.filename_token.safe_token`` so no token can
#: carry the separator — which is what lets :func:`produced_layout` state the
#: schema outright instead of scanning for it.
CONVERTER_KINDS = ("ome_import", "czi_import", "nd2_import")


#: The kinds whose output names its images exactly as their input did, so
#: what was known about the input's files is still true of the output's. A
#: Concatenate relabels dataset 2's timepoints but not the slots they sit in;
#: a Subset and a Merge copy images under their own names (a Merge's remap
#: changes which well a file belongs to, not how its name is built); Validate
#: reports on a dataset and writes no images.
LAYOUT_PRESERVING_KINDS = ("concatenate", "merge_plates", "subset", "validate")


def produced_layout(kind: str, spec: dict,
                    upstream: "list[dict | None] | None" = None) -> dict | None:
    """What a finished job knows about the files it wrote, or ``None``.

    A converter names every image itself: the schema and separator are its own
    naming, and the channel roles are the ones set on its page, which its
    queued spec carries as ``channel_roles`` (``[[name, role], …]``). A
    converter job queued before its page could read the source has no roles,
    and then only the schema is stated — a chained Analyze job scans for the
    rest.

    A :data:`LAYOUT_PRESERVING_KINDS` job passes on what its inputs' source
    jobs recorded. *upstream* holds one entry per input in
    :data:`INPUT_TARGETS` order — the layout that input's source job recorded,
    or ``None`` when the input was not linked to a job or its source recorded
    nothing. The layout is passed on only when **every** input's is known and
    they are identical: an input read from a folder on disk is unknown, and two
    conversions set up with different channel roles disagree. Either way
    nothing is passed on, and the Analyze job after it scans the files, as it
    would have with no queue at all.
    """
    if kind in LAYOUT_PRESERVING_KINDS:
        layouts = list(upstream or [])
        if len(layouts) != len(input_targets(kind)) or not layouts:
            return None
        if any(layout is None for layout in layouts):
            return None
        if any(layout != layouts[0] for layout in layouts[1:]):
            return None
        return json.loads(json.dumps(layouts[0]))
    if kind not in CONVERTER_KINDS:
        return None
    from util.channel_roles import channel_tokens_from_roles
    from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP

    layout: dict = {"schema": DEFAULT_SCHEMA, "separator": DEFAULT_SEP}
    pairs = [(str(pair[0]), str(pair[1]))
             for pair in (spec.get("channel_roles") or [])
             if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0]]
    if pairs:
        layout.update(channel_tokens_from_roles(pairs).as_pipeline_info())
    return layout


#: The converters and Zip loose are absent from both tables, on purpose: they
#: read acquisition files or a loose image folder, never a dataset, so no
#: earlier job's output can be their input. Offering "Input from ▸" on them
#: let a user link two conversions, and the second then failed at dequeue.
CHAIN_TARGET = {
    "analyze": "opts.raw",
    "concatenate": "dataset1",
    "validate": "dataset",
    "subset": "source",
    "merge_plates": "dataset1",
}

# Every input an earlier job's output can fill, in the order a user reads them,
# with the name a menu calls each one. CHAIN_TARGET above is the first of these
# — the one "Input from ▸" fills — and the two-dataset tools have a second,
# which is how a Concatenate can take dataset 1 from one conversion and
# dataset 2 from another.
INPUT_TARGETS = {
    "analyze": (("opts.raw", "Input"),),
    "concatenate": (("dataset1", "Dataset 1"), ("dataset2", "Dataset 2")),
    "validate": (("dataset", "Dataset"),),
    "subset": (("source", "Source"),),
    "merge_plates": (("dataset1", "Dataset 1"), ("dataset2", "Dataset 2")),
}


def input_targets(kind: str) -> tuple[str, ...]:
    """The dotted spec paths *kind* receives earlier jobs' outputs at."""
    return tuple(target for target, _label in INPUT_TARGETS.get(kind, ()))


def input_label(kind: str, target: str | None) -> str:
    """What a menu or a queue row calls the input at *target*."""
    return dict(INPUT_TARGETS.get(kind, ())).get(target or "", "Input")


def planned_output(kind: str, spec: dict) -> str | None:
    """The dataset folder a queued job is set up to produce, or ``None``.

    What a successor shows in its input field while the job has not run, so a
    user setting up "the Analyze of that Concatenate" sees which folder they
    are pointing at. It is a statement of intent, not of fact: the link is what
    carries the real answer, and ``JobQueue.materialise`` replaces this path
    with the folder the job reported when it finished. Analyze's is the folder
    it reads, since it writes ``out/`` beside the input of a dataset root — for
    other input layouts the real root differs, and the link still carries it.
    """
    spec = spec or {}

    def _get(key: str) -> str:
        return str(spec.get(key) or "").strip()

    if kind == "analyze":
        value = str((spec.get("opts") or {}).get("raw") or "").strip()
    elif kind == "concatenate":
        value = _get("dataset1") if spec.get("in_place") else _get("destination")
    elif kind == "import_loose":
        value = _get("source") if spec.get("in_place") else _get("destination")
    elif kind == "validate":
        value = _get("dataset")
    elif kind in (*CONVERTER_KINDS, "subset", "merge_plates"):
        value = _get("destination")
    else:
        value = ""
    return value or None


# Which spec keys name this kind's inputs and its output, in the order a
# description should read them. Keeping it as data rather than a function per
# kind is what lets `test_job_kinds` assert every kind is covered.
_DESCRIBE_KEYS = {
    "concatenate": (("dataset1", "dataset2"), "destination"),
    "ome_import": (("source",), "destination"),
    "czi_import": (("source",), "destination"),
    "nd2_import": (("source",), "destination"),
    "validate": (("dataset",), None),
    "subset": (("source",), "destination"),
    "import_loose": (("source",), "destination"),
    "merge_plates": (("dataset1", "dataset2"), "destination"),
}


#: The longest a job's name runs inside a sentence before its middle gives
#: way to "…". A converter's label carries the source file name and the
#: destination in full, and one long unbroken token cannot wrap, so a sentence
#: naming it was cut off before saying what happened to it.
NAME_IN_SENTENCE = 56


def short_label(label: str) -> str:
    """*label* shortened in the middle when it would not fit in a sentence,
    keeping both ends — the tool at the start, the output at the end."""
    if len(label) <= NAME_IN_SENTENCE:
        return label
    keep = NAME_IN_SENTENCE - 1
    return f"{label[: keep // 2]}…{label[-(keep - keep // 2):]}"


def _name(value: object) -> str:
    """A path's last component, or "" for anything empty or unset."""
    text = str(value or "").strip()
    return Path(text).name if text else ""


def _analyze_label(spec: dict) -> str:
    """Analyze's own name for a run, via the existing ``RunSpec.label``."""
    from services.run_spec import RunSpec

    try:
        return RunSpec.from_json(spec).label()
    except (KeyError, TypeError, ValueError, AttributeError):
        # A spec too malformed to load is still a row that has to render; the
        # bare kind name is a worse label than the real one and a much better
        # one than a traceback on the queue page.
        return KIND_LABEL["analyze"]


def _analyze_detail(spec: dict) -> str:
    """The input path, a note when the job reads its schema and channels
    from that input when it runs, and the remote host it runs on.

    The note is there because the row is the only place a queued job
    describes itself, and a job whose schema and channels are settled at run
    time otherwise reads exactly like one whose form fixed them.
    """
    raw = str((spec.get("opts") or {}).get("raw") or "").strip()
    parts = [raw] if raw else []
    if spec.get("auto_detect"):
        parts.append("schema and channels read from the input when it runs")
    remote = str(spec.get("remote_name") or "").strip()
    if remote:
        # Said on the row because it decides what may run beside the job: a
        # remote Analyze can run alongside a local one (services.job_lanes).
        parts.append(f"runs on “{remote}”")
    return " · ".join(parts)


def describe(kind: str, spec: dict) -> str:
    """A one-line name for this job, e.g. ``"Subset — plateA → plateA_sub"``.

    Falls back to the plain kind label whenever the spec does not name what it
    would need to say more — an incomplete job still gets a row, and a row
    reading "Subset" is honest about knowing nothing else.
    """
    label = KIND_LABEL.get(kind, kind)
    if kind == "analyze":
        return _analyze_label(spec)
    inputs, out_key = _DESCRIBE_KEYS.get(kind, ((), None))
    names = [n for n in (_name(spec.get(k)) for k in inputs) if n]
    if not names:
        return label
    text = f"{label} — {' + '.join(names)}"
    out = _name(spec.get(out_key)) if out_key else ""
    return f"{text} → {out}" if out else text


def describe_detail(kind: str, spec: dict) -> str:
    """The full paths behind :func:`describe`, for a tooltip or a second line.

    Empty when there is nothing to add — the caller hides the line rather than
    showing an arrow with nothing on either side of it.
    """
    if kind == "analyze":
        return _analyze_detail(spec)
    inputs, out_key = _DESCRIBE_KEYS.get(kind, ((), None))
    parts = [str(spec.get(k) or "").strip() for k in inputs]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    text = " + ".join(parts)
    out = str(spec.get(out_key) or "").strip() if out_key else ""
    return f"{text} → {out}" if out else text
