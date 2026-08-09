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

#: What the plate paints, over and above the recorded health states. These are
#: *renderings*, derived from numbers already in the record — not a second
#: opinion about whether a well is complete. ``pipeline.completeness.health``
#: remains the only classifier.
CELL_COMPLETE = "complete"
CELL_PARTIAL = "partial"
CELL_FAILED = "failed"
CELL_INPUT_ONLY = "input_only"
CELL_NO_MEASUREMENTS = "no_measurements"
CELL_UNCHECKED = "unchecked"
CELL_ABSENT = "absent"


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
        "name": data_dir.name if data_dir is not None else "",
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

    The five painted states are derived from figures already in the record —
    ``covered_any == 0`` separates a staged-but-never-processed well from a
    half-finished one, ``failed`` marks wells with tracebacks — rather than
    from a second reading of the plate. Without a record every well is
    ``unchecked``: a neutral state that is not "fine" and not "broken", because
    neither is known.
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
        elif health == "missing":
            cells[well] = CELL_NO_MEASUREMENTS
        elif health == "complete":
            cells[well] = CELL_COMPLETE
        else:
            cells[well] = CELL_ABSENT
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
    """
    cells = plate_cells(ctx)
    order = (
        (CELL_COMPLETE, "complete"),
        (CELL_PARTIAL, "partial"),
        (CELL_FAILED, "failed"),
        (CELL_INPUT_ONLY, "input only"),
        (CELL_NO_MEASUREMENTS, "no measurements"),
        (CELL_UNCHECKED, "not checked"),
    )
    counts = {}
    for state in cells.values():
        counts[state] = counts.get(state, 0) + 1
    return [(state, label, counts.get(state, 0)) for state, label in order
            if counts.get(state, 0) or state != CELL_UNCHECKED]


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
        n_wells = int(totals.get("wells") or 0)
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
        pct = int(covered * 100 / intent) if intent else 0
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

    rows.append({"label": "Background subtraction",
                 "value": str(qc.get("background") or "—").replace("_", " ")})
    rows.append({"label": "Flat-field",
                 "value": str(qc.get("flatfield") or "none").replace("_", " ")})
    if qc.get("qc_remove_debris"):
        rows.append({"label": "Debris gating",
                     "value": f"area {qc.get('qc_min_area', '—')} – "
                              f"{qc.get('qc_max_area', '—')} px²"})
    block = _record(ctx)
    if block.get("host"):
        rows.append({"label": "Ran on", "value": str(block["host"])})
    if block.get("duration_s"):
        rows.append({"label": "Duration",
                     "value": _duration(float(block["duration_s"]))})
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
    labelled = sum(1 for value in labels.values() if str(value or "").strip())
    notes = str(getattr(selection, "notes_text", "") or "")

    info = _sidecar(ctx)
    smfish = info.get("smfish_thresholds") or {}
    ratios = info.get("ratios") or []

    return [
        {"key": "replicates", "label": "Replicate sets", "count": len(sets)},
        {"key": "plot_groups", "label": "Plot groups", "count": len(groups)},
        {"key": "labels", "label": "Wells labelled", "count": labelled,
         "total": len(labels) or None},
        {"key": "smfish", "label": "smFISH thresholds", "count": len(smfish)},
        {"key": "ratios", "label": "Ratios", "count": len(ratios)},
        {"key": "notes", "label": "Notes", "count": 1 if notes.strip() else 0},
    ]


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

    cells = plate_cells(ctx)
    failed = sum(1 for s in cells.values() if s == CELL_FAILED)
    partial = sum(1 for s in cells.values() if s == CELL_PARTIAL)
    unstarted = sum(1 for s in cells.values() if s == CELL_INPUT_ONLY)
    if failed:
        return {"severity": "danger",
                "text": f"{failed} well(s) recorded failures during the run.",
                "action": "errors", "label": "Show them"}
    if partial:
        return {"severity": "warn",
                "text": f"{partial} well(s) cover fewer field groups than their "
                        "input held.",
                "action": "partial", "label": "Show them"}
    if unstarted:
        return {"severity": "warn",
                "text": f"{unstarted} well(s) were staged and never processed.",
                "action": "analyze", "label": "Open in Analyze"}

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
