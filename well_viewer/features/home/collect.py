"""Everything the Home page displays, computed from state alone.

Qt-free and matplotlib-free by design: the page is otherwise the hardest thing
in Review to test, and putting every number here means the whole of it is
exercised in the fast lane against a plain namespace rather than a real
application.

**Home reports; it does not measure.** Per-well coverage costs a zip
central-directory read and a full CSV parse per well, which a landing page
cannot pay on every open — and a cached answer that silently ages is worse than
none. The pipeline records the answer at the end of every run, into
``pipeline_info.json``'s ``run_check`` block (§6.2), and everything below is a
reading of that block plus the sidecar it sits in. Nothing here opens a file.

One rule runs through all of it: **a figure this page could not obtain is
:class:`Missing`, never zero.** "Not computed" and "computed as zero" are
different facts, and the whole point of the completeness record is that a
dataset stops conflating them. A real zero — the user has defined no replicate
sets — is a computed fact and renders as itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline import completeness
from well_viewer.core.lazy import lazy
from well_viewer.domain.dataset_name import dataset_display_name

# Saved Plots is removable, so this is a proxy rather than an import: it resolves
# on first call and only in a build that ships the feature. The guard above every
# call site is ``ctx.features.get("Saved Plots")``, so the proxy is never touched
# in a build without it.
_saved_plots_store = lazy("well_viewer.features.saved_plots.state", "get_store")

#: What the plate paints, over and above the recorded health states. These are
#: *renderings*, derived from numbers already in the record — not a second
#: opinion about whether a well is complete. ``pipeline.completeness.health``
#: remains the only classifier.
CELL_COMPLETE = "complete"
CELL_PARTIAL = "partial"
CELL_FAILED = "failed"
CELL_INPUT_ONLY = "input_only"
CELL_NO_MEASUREMENTS = "no_measurements"
#: Measurements are whole and the output archive they came from is gone, so the
#: rows cannot be reviewed, re-gated or re-thresholded. Distinct from
#: ``CELL_COMPLETE`` because it is not finished, and distinct from
#: ``CELL_NO_MEASUREMENTS`` because that is the mirror case.
CELL_CSV_ONLY = "csv_only"
CELL_UNCHECKED = "unchecked"
#: The record's fifth health state (``pipeline.completeness.HEALTH_STATES``):
#: the well has no evidence at all — no staged input, no output. Named for the
#: health value it renders so the two cannot drift apart.
CELL_NO_OUTPUT = "no_output"


@dataclass(frozen=True)
class Missing:
    """A figure the page could not obtain, and why.

    Rendered as an em-dash with *reason* beside or behind it. It is a distinct
    type rather than ``None`` so that a caller cannot accidentally format it as
    a number, and so a test can assert the difference between this and a real
    zero without inspecting prose.
    """

    reason: str

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Stat:
    """One figure on the page: a label, a value, and a line underneath.

    *value* is either a computed value or a :class:`Missing`. *sub* carries the
    denominator or the reason — deliberately on screen rather than in a
    tooltip, because a coverage percentage whose denominator is hidden is the
    kind of number that has been quietly wrong here before.
    """

    key: str
    label: str
    value: object
    sub: str = ""

    @property
    def available(self) -> bool:
        return not isinstance(self.value, Missing)


def _record(ctx) -> dict:
    info = getattr(getattr(ctx.state, "dataset", None), "pipeline_info", None) or {}
    block = info.get("run_check")
    return block if isinstance(block, dict) else {}


def _sidecar(ctx) -> dict:
    return getattr(getattr(ctx.state, "dataset", None), "pipeline_info", None) or {}


def record_state(ctx) -> str:
    """Which of the four states the completeness record is in.

    ``none``
        no record — the dataset predates the check, or a remote run's
        client-side check could not run. The page says so and offers Validate.
    ``skipped``
        the user turned the check off for that run. A different thing with a
        different fix, which is why the pipeline writes a stub rather than
        nothing.
    ``partial``
        the check started and stopped; some wells were never read.
    ``ok``
        a complete record.
    """
    block = _record(ctx)
    if not block:
        return "none"
    if block.get("skipped"):
        return "skipped"
    if block.get("partial_scan"):
        return "partial"
    return "ok"


def has_dataset(ctx) -> bool:
    """Whether there is a dataset to report on.

    The wells, not ``data_dir``. The two disagree for a whole moment of every
    load — ``data_dir`` is set when the load *begins* and the wells arrive as it
    runs — and they disagree permanently for a folder that opened but yielded
    nothing readable. Home's empty state is flipped by the shared refresher,
    which tests the wells (``core/ui_helpers.refresh_empty_state_pages``), so a
    page testing something else could contradict the placeholder covering it.
    """
    dataset = getattr(ctx.state, "dataset", None)
    return bool(getattr(dataset, "well_paths", None))


def record_reason(ctx) -> str:
    """One sentence naming why a figure is unavailable, for the em-dashes."""
    return {
        "none": "no completeness record for this dataset",
        "skipped": "the completeness check was turned off for this run",
        "partial": "the completeness check did not finish",
    }.get(record_state(ctx), "")


def identity(ctx) -> dict:
    """The masthead: what this dataset is, and when it was last run.

    ``finished_at`` is shown rather than implied because the record is a
    snapshot of the moment the check ran — a dataset repaired by hand since
    still reads as it did then, and the page must not pretend to be live.

    ``source`` says which check produced it. The pipeline's own end-of-run check
    describes the run that had just finished; the Datasets Validate tool
    describes the folder as it stood when someone looked at it, possibly long
    afterwards. Both are true statements about completeness and they are not
    the same statement, so the masthead names which one it is showing rather
    than letting a scan from last week read as a run from last week.
    """
    ds = getattr(ctx.state, "dataset", None)
    data_dir = getattr(ds, "data_dir", None) if ds is not None else None
    info, block = _sidecar(ctx), _record(ctx)
    schema = str(info.get("schema") or "")
    return {
        # The shared rule, not ``data_dir.name``: Review loads
        # ``<dataset_root>/out``, so the folder's own name is the literal "out"
        # on every pipeline dataset and the masthead titled all of them that.
        "name": dataset_display_name(data_dir),
        "path": str(data_dir) if data_dir is not None else "",
        "schema": schema,
        "separator": str(info.get("separator") or ""),
        "schema_version": info.get("schema_version"),
        "finished_at": str(block.get("finished_at") or ""),
        "duration_s": block.get("duration_s"),
        "host": str(block.get("host") or ""),
        "suspended": bool(block.get("suspended")),
        # Absent on records written before the field existed, which were all
        # written by the pipeline — so its default is the honest one.
        "source": str(block.get("source") or ("run" if block else "")),
    }


def plate_cells(ctx) -> dict:
    """Every well the record knows, as the state the plate paints it in.

    The painted states are derived from figures already in the record —
    ``covered_any == 0`` separates a staged-but-never-processed well from a
    half-finished one, ``failed`` marks wells with tracebacks — rather than
    from a second reading of the plate. Without a record every well is
    ``unchecked``: a neutral state that is not "fine" and not "broken", because
    neither is known.

    Each of the record's five health states maps to a painted state by name,
    ``no_output`` included: a well with no evidence at all is in the record and
    produced nothing, which is a narrower claim than being absent from the
    dataset, and the legend has a row for it. A health value this build does not
    recognise falls back to ``unchecked`` — the state that asserts nothing —
    rather than to one that would claim something specific about the well.
    """
    block = _record(ctx)
    wells = block.get("wells") if isinstance(block.get("wells"), dict) else {}
    if not wells:
        labels = getattr(getattr(ctx.state, "dataset", None), "well_paths", None) or {}
        return dict.fromkeys(labels, CELL_UNCHECKED)

    cells: dict[str, str] = {}
    for well, rec in wells.items():
        if not isinstance(rec, dict):
            continue
        health = str(rec.get("health") or "")
        if rec.get("failed"):
            cells[well] = CELL_FAILED
        elif health == "partial" and not int(rec.get("covered_any") or 0):
            cells[well] = CELL_INPUT_ONLY
        elif health == "partial":
            cells[well] = CELL_PARTIAL
        elif health == "csv_only":
            cells[well] = CELL_CSV_ONLY
        elif health == "missing":
            cells[well] = CELL_NO_MEASUREMENTS
        elif health == "complete":
            cells[well] = CELL_COMPLETE
        elif health == "no_output":
            cells[well] = CELL_NO_OUTPUT
        else:
            cells[well] = CELL_UNCHECKED
    # Wells the dataset holds but the record does not. They used to be left out
    # of this dict entirely, and ``_paint_plate`` calls ``setEnabledWells`` with
    # its keys — so a well loaded into Review but absent from the completeness
    # record was disabled on Home's plate, painted nothing, and had no legend
    # row to say why. It could not be selected there at all. A record written
    # before the well was added, or one covering a subset of a merged dataset,
    # both produce that. ``unchecked`` is the state for it: not fine, not
    # broken, nothing known.
    for label in (getattr(getattr(ctx.state, "dataset", None), "well_paths", None) or {}):
        cells.setdefault(label, CELL_UNCHECKED)
    return cells


def plate_fractions(ctx) -> dict:
    """How much of each partial well is covered, for its coverage arc.

    Only wells that are genuinely part-done get one: a well covering nothing
    would draw an empty arc indistinguishable from no arc at all, and a
    complete well needs none.
    """
    out: dict[str, float] = {}
    for well, rec in (_record(ctx).get("wells") or {}).items():
        if not isinstance(rec, dict):
            continue
        intent = int(rec.get("intent") or 0)
        covered = int(rec.get("covered_any") or 0)
        if intent and 0 < covered < intent:
            out[well] = covered / intent
    return out


def plate_legend(ctx) -> list:
    """Legend rows, each with the count the record actually holds.

    Rows whose count is zero stay on screen — a legend that hides its empty
    states makes the reader work out what a colour would have meant.

    The two states that describe an *absence* (``unchecked``, ``no_output``) are
    the exception: they appear only when the plate actually holds one. Both can
    be on screen at once — ``unchecked`` is what a plate with no record is made
    of, and it is also what a well the record does not mention gets, while
    ``no_output`` only ever comes from a record. Either way no well is left
    without a row to explain it, which is the property that matters.
    """
    cells = plate_cells(ctx)
    order = (
        (CELL_COMPLETE, "complete"),
        (CELL_PARTIAL, "partial"),
        (CELL_FAILED, "failed"),
        (CELL_INPUT_ONLY, "input only"),
        (CELL_NO_MEASUREMENTS, "no measurements"),
        (CELL_CSV_ONLY, "measurements, no output archive"),
        (CELL_NO_OUTPUT, "no output yet"),
        (CELL_UNCHECKED, "not checked"),
    )
    only_when_present = (CELL_UNCHECKED, CELL_NO_OUTPUT)
    counts = {}
    for state in cells.values():
        counts[state] = counts.get(state, 0) + 1
    return [(state, label, counts.get(state, 0)) for state, label in order
            if counts.get(state, 0) or state not in only_when_present]


def stat_tiles(ctx) -> list:
    """The scale strip: the numbers you need before drawing anything.

    Every figure that depends on the completeness record becomes a
    :class:`Missing` carrying the reason when there is no record, rather than a
    zero that would read as a measurement.
    """
    info, block = _sidecar(ctx), _record(ctx)
    totals = block.get("totals") if isinstance(block.get("totals"), dict) else {}
    reason = record_reason(ctx)
    tiles = []

    if totals:
        # The plate, not the subset the scan recorded: a well that raised is
        # missing from ``totals["wells"]``, so this tile read "94 of 94" for a
        # plate of 96 with two unreadable.
        n_wells = completeness.plate_total(totals)
        complete = int(totals.get("complete") or 0)
        tiles.append(Stat("wells", "Wells", complete, f"of {n_wells} complete"))
    else:
        n_paths = len(getattr(getattr(ctx.state, "dataset", None), "well_paths", None) or {})
        tiles.append(Stat("wells", "Wells", Missing(reason),
                          f"{n_paths} loaded" if n_paths else reason))

    tps = list(info.get("available_timepoints") or [])
    tiles.append(Stat(
        "timepoints", "Timepoints", len(tps) if tps else Missing("none recorded"),
        f"{tps[0]} → {tps[-1]}" if len(tps) > 1 else (tps[0] if tps else "")))

    fovs = list(info.get("available_fovs") or [])
    tiles.append(Stat("fovs", "Fields per well",
                      len(fovs) if fovs else Missing("none recorded"), ""))

    shapes = list(info.get("shape_features") or [])
    tiles.append(Stat(
        "shape_features", "Shape features", len(shapes) if shapes else 0,
        ", ".join(shapes[:2]) + (f", +{len(shapes) - 2}" if len(shapes) > 2 else "")))

    if totals:
        intent = int(totals.get("intent") or 0)
        covered = int(totals.get("covered_any") or 0)
        # Rendered with its unit. Every other tile on the strip is a count, so a
        # bare "90" next to "Wells 3" and "Timepoints 3" reads as ninety of
        # something — the one number on the page that is a proportion has to say
        # so where it is read, not only in the line beneath it.
        pct = f"{int(covered * 100 / intent)}%" if intent else ""
        tiles.append(Stat("coverage", "Coverage",
                          pct if intent else Missing("nothing to compare against"),
                          f"{covered:,} of {intent:,} groups" if intent else reason))
    else:
        tiles.append(Stat("coverage", "Coverage", Missing(reason), reason))

    return tiles


def channel_chips(ctx) -> list:
    """The dataset's channels, with the segmentation channel flagged.

    That one is what nuclei are *found* with, so it behaves differently
    everywhere downstream — an ON/OFF brightness cut on it has no meaning — and
    it is worth being able to see which it is without opening another page.
    """
    info = _sidecar(ctx)
    seg = str(info.get("nuclear_token") or "").strip().lower()
    cyto = str(info.get("cytoplasm_token") or "").strip().lower()
    phase = str(info.get("phase_token") or "").strip().lower()
    smfish = {str(t).strip().lower() for t in (info.get("smfish_tokens") or [])}
    chips = []
    for token in (info.get("fluor_tokens") or []):
        low = str(token).strip().lower()
        role = ""
        if low == cyto:
            role = "cyto"
        elif low in smfish:
            role = "smfish"
        elif low == phase:
            role = "phase"
        chips.append({"token": str(token), "role": role, "segmentation": low == seg})
    return chips


def health_rows(ctx) -> list:
    """What needs attention, and nothing else.

    A row appears only when its condition holds, so a healthy dataset shows an
    all-clear line rather than a card full of zeros. Severity is carried
    per-row so the page can spend colour on exceptions alone.
    """
    block = _record(ctx)
    rows = []
    state = record_state(ctx)
    if state != "ok":
        rows.append({
            "severity": "info" if state == "skipped" else "warn",
            "label": "Completeness",
            "value": record_reason(ctx),
            "wells": [],
        })
        if state == "none":
            return rows

    wells = block.get("wells") or {}
    cells = plate_cells(ctx)

    def _named(state_name):
        return sorted(w for w, s in cells.items() if s == state_name)

    for state_name, severity, label, detail in (
        (CELL_FAILED, "danger", "Wells with failures",
         "the run recorded failed field-groups for these wells"),
        (CELL_PARTIAL, "warn", "Partial wells",
         "their CSVs cover fewer field groups than the input held"),
        (CELL_INPUT_ONLY, "warn", "Staged, never processed",
         "input is present and nothing was written"),
        (CELL_NO_MEASUREMENTS, "warn", "Images, no measurements",
         "output images exist with no readable CSV behind them"),
    ):
        named = _named(state_name)
        if named:
            rows.append({"severity": severity, "label": label,
                         "value": detail, "wells": named})

    errors = block.get("errors") if isinstance(block.get("errors"), dict) else {}
    if int(errors.get("files") or 0):
        by_type = errors.get("by_type") or {}
        detail = ", ".join(f"{name} · {count}" for name, count
                           in sorted(by_type.items(), key=lambda kv: -kv[1]))
        rows.append({"severity": "danger", "label": "Errors",
                     "value": f"{int(errors['files'])} traceback file(s)"
                              + (f" — {detail}" if detail else ""),
                     "wells": []})

    for finding in (block.get("findings") or []):
        if isinstance(finding, dict):
            rows.append({"severity": finding.get("severity") or "warn",
                         "label": str(finding.get("code") or "").replace("_", " "),
                         "value": str(finding.get("message") or ""),
                         "wells": []})

    if block.get("suspended"):
        rows.append({"severity": "info", "label": "Run was suspended",
                     "value": "the wells it never reached are not failures",
                     "wells": []})

    if not rows and wells:
        rows.append({"severity": "ok", "label": "No problems recorded",
                     "value": f"{len(wells)} well(s) checked", "wells": []})
    return rows


def run_parameters(ctx) -> list:
    """The run receipt, condensed.

    Two things this exists to make unambiguous. The background method is named
    in full rather than shown as a parameter, because top-hat and sliding-disk
    have been mistaken for each other. And a threshold the user chose is
    distinguished from one that came from the pretrained weights' own defaults —
    ``default`` is a different kind of fact from a number somebody picked, and
    the negative sentinel the pipeline stores for it would otherwise render as
    a nonsensical value.
    """
    info = _sidecar(ctx)
    qc = info.get("qc_options") if isinstance(info.get("qc_options"), dict) else {}
    execution = (info.get("execution_options")
                 if isinstance(info.get("execution_options"), dict) else {})
    rows = [{"label": "Segmentation",
             "value": str(info.get("segmentation_method") or "—").replace("_", " ")}]

    for label, key in (("Object probability", "stardist_prob_thresh"),
                       ("Overlap (NMS)", "stardist_nms_thresh")):
        raw = execution.get(key)
        if raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if number < 0:
            rows.append({"label": label, "value": "the model's own default",
                         "is_default": True})
        else:
            rows.append({"label": label, "value": f"{number:g}", "is_default": False})

    background = str(qc.get("background") or "—")
    rows.append({"label": "Background subtraction",
                 "value": background.replace("_", " ")})
    rows.extend(_correction_parameter_rows(background, qc, execution,
                                           which="background"))

    flatfield = str(qc.get("flatfield") or "none")
    rows.append({"label": "Flat-field", "value": flatfield.replace("_", " ")})
    rows.extend(_correction_parameter_rows(flatfield, qc, execution,
                                           which="flatfield"))

    if qc.get("remove_debris"):
        rows.append({"label": "Debris gating",
                     "value": f"area {_number(qc.get('min_area'))} – "
                              f"{_number(qc.get('max_area'))} px²"})
    block = _record(ctx)
    if block.get("host"):
        rows.append({"label": "Ran on", "value": str(block["host"])})
    if block.get("duration_s"):
        rows.append({"label": "Duration",
                     "value": _duration(float(block["duration_s"]))})
    return rows


def _number(value, fallback: str = "—") -> str:
    """A sidecar number as the user would write it — ``100``, not ``100.0``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return f"{number:g}"


def _per_channel(qc: dict, execution: dict, nuclear_key: str, fluor_key: str,
                 unit: str) -> str:
    """One value when both channel roles share it, both named when they differ.

    The pipeline takes these per channel role — a nuclear stain and a
    fluorescence readout can want different sizes — and they are usually left
    equal. Printing "100 px (nuclear), 100 px (fluorescence)" for the common
    case buries the number in punctuation; printing one value when they differ
    would be a different run than the one that produced the plate.
    """
    nuclear = _number(qc.get(nuclear_key, execution.get(nuclear_key)))
    fluor = _number(qc.get(fluor_key, execution.get(fluor_key)))
    if nuclear == fluor:
        return f"{nuclear} {unit}" if nuclear != "—" else "—"
    return f"{nuclear} {unit} (nuclear), {fluor} {unit} (fluorescence)"


def _correction_parameter_rows(method: str, qc: dict, execution: dict, *,
                               which: str) -> list:
    """The settings *method* actually consulted, and nothing else.

    Which those are is :data:`pipeline.background.BACKGROUND_METHOD_PARAMS` /
    :data:`pipeline.flatfield.FLATFIELD_MODE_PARAMS`, read off the dispatch that
    defines them, so this cannot list a mesh ratio beside ``sliding_disk``
    (which never sees one) or drop the ratio from ``robust_mesh`` (which is
    built on it). A receipt that names a setting the run ignored asserts an
    effect that did not happen, which is the same kind of untruth as leaving
    out the ones that did.

    Imported inside the call: this is the only place in the Review side that
    needs the imaging package's rosters, and a collect module should not pull
    the pipeline in at import time.
    """
    if which == "flatfield":
        from pipeline.flatfield import flatfield_params_used as params_used
    else:
        from pipeline.background import background_params_used as params_used

    rows = []
    for name in params_used(method):
        if name == "radius":
            rows.append({"label": "Radius",
                         "value": _per_channel(qc, execution, "tophat_radius_nir",
                                               "tophat_radius_fluor", "px")})
        elif name == "fft_small":
            rows.append({"label": "Small-structure cutoff",
                         "value": _per_channel(qc, execution, "fft_small_nir",
                                               "fft_small_fluor", "px")})
        elif name == "mesh_ratio":
            rows.append({"label": "Mesh ratio",
                         "value": _number(qc.get("mesh_ratio",
                                                 execution.get("mesh_ratio")))})
        elif name == "mesh_sigma":
            rows.append({"label": "Mesh sigma",
                         "value": _number(qc.get("mesh_sigma",
                                                 execution.get("mesh_sigma")))})
        elif name == "background_well":
            from util.well_token import split_background_wells
            wells = split_background_wells(
                qc.get("background_well", execution.get("background_well")))
            label = ("Reference well(s)" if which == "flatfield"
                     else "Empty well(s)")
            if wells:
                rows.append({"label": label, "value": ", ".join(wells)})
            else:
                # The run asked for a reference-well correction and named no
                # well, so no field was built and every frame passed through
                # untouched — output bit-for-bit identical to a corrected one.
                # Saying "—" here would read as a formatting gap.
                rows.append({"label": label, "severity": "warn",
                             "value": "none named — the correction did not run"})
    return rows


def _duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} h {minutes:02d} m"
    return f"{minutes} m" if minutes else f"{int(seconds)} s"


def your_work(ctx) -> list:
    """What the user has already done here.

    The zeros in this block are **real**: "no replicate sets defined" is a
    computed fact, not a figure that could not be obtained, so it reads as
    "None yet" and never as an em-dash. That distinction is the whole reason
    :class:`Missing` is a separate type.
    """
    selection = getattr(ctx.state, "selection", None)
    dataset = getattr(ctx.state, "dataset", None)
    sets = list(getattr(selection, "selections", None) or [])
    groups = list(getattr(selection, "plot_groups", None) or [])
    labels = dict(getattr(dataset, "well_labels", None) or {})
    loaded_wells = dict(getattr(dataset, "well_paths", None) or {})
    # Counted over the *loaded* wells, which is also what the denominator below
    # measures. The label map is hydrated additively and is never pruned to the
    # plate, so a dataset re-run over fewer wells still carries labels for the
    # ones that are gone — counting those would put the numerator above the
    # denominator and print something like "12 of 6".
    labelled = sum(1 for well, value in labels.items()
                   if well in loaded_wells and str(value or "").strip())
    notes = str(getattr(selection, "notes_text", "") or "")

    info = _sidecar(ctx)
    smfish = info.get("smfish_thresholds") or {}
    # Ratios are read from the live channel state, which the loader has already
    # populated from ``persistence.json`` (``persistence/ratios.py`` writes
    # them there through ``_doc.set_section``). This row used to read
    # ``pipeline_info["ratios"]`` — a key nothing has ever written — so it said
    # "None yet" however many ratios the user had defined. Reading the doc
    # directly would fix the count and break this page's other promise: Home
    # reports a record and opens no files.
    ratios = list(getattr(getattr(ctx.state, "channel", None),
                          "ratio_metrics", None) or [])

    return [
        {"key": "replicates", "label": "Replicate sets", "count": len(sets)},
        {"key": "plot_groups", "label": "Plot groups", "count": len(groups)},
        # The denominator is the plate's loaded wells, not the size of the
        # label map: that map holds only wells that *have* a label, so
        # ``labelled of len(labels)`` could only ever read "N of N" and told
        # the user nothing about how much of the plate was still unlabelled.
        {"key": "labels", "label": "Wells labelled", "count": labelled,
         "total": len(loaded_wells) or None},
        {"key": "smfish", "label": "smFISH thresholds", "count": len(smfish)},
        # Read from the live channel state, so the count is right in a build
        # with the Ratios editor profiled out: the ratio model is core, and a
        # dataset's persisted ratios stay live as virtual channels in every
        # plot whether or not the tab that defines them shipped. These rows
        # report, they do not navigate, so nothing here offers a route to a tab
        # that is gone.
        {"key": "ratios", "label": "Ratios", "count": len(ratios)},
        {"key": "notes", "label": "Notes", "count": 1 if notes.strip() else 0},
    ]


#: The most thumbnails the Home strip offers. Home is a summary, and a gallery
#: of ninety figures on the landing page is the gallery, not a summary of it —
#: the strip says how many are not shown so the number is never the reader's to
#: guess.
SAVED_PLOT_STRIP_LIMIT = 12


def saved_plots_strip(ctx) -> dict:
    """The saved plots to show on Home, newest first, and how many there are.

    Returns ``{"available": bool, "total": int, "shown": [...]}``. ``available``
    is False when this build has no Saved Plots feature at all — the strip is
    then absent rather than empty, because "you have saved no plots" is a lie in
    a build where saving one is impossible.

    Each entry carries ``id`` / ``title`` / ``tab`` / ``created_at`` and an
    ``image`` **path**, not image bytes. That keeps this module's rule intact:
    Home reads the record and opens no files, and the one place that turns a
    path into pixels is the view, which is where every other image in the app is
    loaded. It also means a thumbnail whose file has gone missing is the view's
    problem to render, not a figure this module has to invent a value for.
    """
    if ctx.features.get("Saved Plots") is None:
        return {"available": False, "total": 0, "shown": []}
    store = _saved_plots_store(ctx)
    store.ensure_loaded(ctx)
    records = list(reversed(store.records))     # gallery order: newest first
    shown = [
        {
            "id": rec.id,
            "title": rec.title or rec.tab or "Saved plot",
            "tab": rec.tab,
            "created_at": rec.created_at,
            "image": store.image_path(ctx, rec),
        }
        for rec in records[:SAVED_PLOT_STRIP_LIMIT]
    ]
    return {"available": True, "total": len(records), "shown": shown}


def wells_needing_force(ctx) -> list[str]:
    """Wells an incremental re-run would skip forever, so it must be told.

    A well with processed images and no measurements CSV has no *missing*
    ``(fov, timepoint)`` pairs — its output archive already covers everything
    its input held — so the incremental diff calls it done and the run skips it.
    That is exactly the well whose CSV needs regenerating, and only a per-well
    force does it. Wells that are merely partial are deliberately absent: an
    incremental run does fix those, at a fraction of the cost of re-segmenting
    them whole. Same rule as ``datasets.validate.validate_compute``'s, read off
    the record rather than off a fresh scan.
    """
    wells = (_record(ctx).get("wells") or {})
    return sorted(
        well for well, rec in wells.items()
        if isinstance(rec, dict) and rec.get("health") == "missing"
    )


def next_step(ctx) -> dict:
    """The one contextual action, chosen by what most needs doing.

    First match wins, and the ladder runs from "this dataset is broken" down to
    "there is nothing to fix". The row is always present: a block that appears
    and disappears between datasets moves everything below it and destroys the
    reader's sense of where things are.
    """
    if not has_dataset(ctx):
        return {"severity": "accent", "text": "No dataset open.",
                "action": "open", "label": "Open a dataset"}

    state = record_state(ctx)
    if state == "none":
        return {"severity": "warn",
                "text": "This dataset has no completeness record — it was "
                        "produced before the check existed, or the check could "
                        "not run.",
                "action": "validate", "label": "Check it now"}
    if state == "skipped":
        return {"severity": "info",
                "text": "The completeness check was turned off for this run.",
                "action": "validate", "label": "Check it now"}
    if state == "partial":
        return {"severity": "warn",
                "text": "The completeness check did not finish, so some wells "
                        "were never read.",
                "action": "validate", "label": "Check it now"}

    # Every shortfall below is fixed by running the pipeline again over what is
    # missing, so all three offer the same thing: Analyze, configured to finish
    # this run. Sending the reader to a page that merely *shows* the gap again
    # asks them to go and rebuild the run's settings by hand, and the settings
    # that matter — the schema, the separator, the CSV prefix — are the ones
    # whose mismatch silently reprocesses the whole plate.
    cells = plate_cells(ctx)
    failed = sum(1 for s in cells.values() if s == CELL_FAILED)
    partial = sum(1 for s in cells.values() if s == CELL_PARTIAL)
    unstarted = sum(1 for s in cells.values() if s == CELL_INPUT_ONLY)
    no_measurements = sum(1 for s in cells.values() if s == CELL_NO_MEASUREMENTS)
    if failed:
        return {"severity": "danger",
                "text": f"{failed} well(s) recorded failures during the run.",
                "action": "complete", "label": "Finish this run"}
    if partial:
        return {"severity": "warn",
                "text": f"{partial} well(s) cover fewer field groups than their "
                        "input held.",
                "action": "complete", "label": "Finish this run"}
    if no_measurements:
        return {"severity": "warn",
                "text": f"{no_measurements} well(s) have images but no "
                        "measurements — an ordinary re-run skips them.",
                "action": "complete", "label": "Finish this run"}
    if unstarted:
        return {"severity": "warn",
                "text": f"{unstarted} well(s) were staged and never processed.",
                "action": "complete", "label": "Finish this run"}

    work = {row["key"]: row["count"] for row in your_work(ctx)}
    if not work.get("labels"):
        return {"severity": "accent",
                "text": "No sample labels yet. Naming wells makes every plot "
                        "legible.",
                "action": "samples", "label": "Define samples"}
    if not work.get("replicates"):
        return {"severity": "accent",
                "text": "No replicate sets yet.",
                "action": "samples", "label": "Set them up"}
    return {"severity": "ok", "text": "Nothing needs attention.",
            "action": "plot", "label": "Open Plotting"}
