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


def produced_layout(kind: str, spec: dict) -> dict | None:
    """What a finished converter job knows about the files it wrote, or ``None``.

    The schema and separator are the converters' own naming; the channel roles
    are the ones set on the converter's page, which its queued spec carries as
    ``channel_roles`` (``[[name, role], …]``). A converter job queued before
    its page could read the source has no roles, and then only the schema is
    stated — the chained Analyze job scans for the rest.
    """
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


CHAIN_TARGET = {
    "analyze": "opts.raw",
    "concatenate": "dataset1",
    "ome_import": "source",
    "czi_import": "source",
    "nd2_import": "source",
    "validate": "dataset",
    "subset": "source",
    "import_loose": "source",
    "merge_plates": "dataset1",
}

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
    """The input path, plus a note when the job reads its schema and channels
    from that input when it runs.

    The note is there because the row is the only place a queued job
    describes itself, and a job whose schema and channels are settled at run
    time otherwise reads exactly like one whose form fixed them.
    """
    raw = str((spec.get("opts") or {}).get("raw") or "").strip()
    parts = [raw] if raw else []
    if spec.get("auto_detect"):
        parts.append("schema and channels read from the input when it runs")
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
