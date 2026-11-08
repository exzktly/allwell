"""What a Saved Plots report says about its dataset: the cover and the history.

A gallery PDF used to open with a title, a dataset name and a plot count. That
cover could not answer the questions someone handed the PDF actually asks:
which run produced these numbers, with what settings, whether the plate was
complete, and whether anything changed between the moment a plot was saved and
the moment it was redrawn. The headless report (``allwell_report.py``) redraws
every plot from the dataset **as it stands now**, so that last question is not
academic: a gate moved after saving changes the figure, and nothing on the page
said so.

This module assembles a :class:`ReportCover` from four sources the dataset
already keeps, and never decides anything from them — it reports:

* ``pipeline_info.json`` — the run's settings (``execution_options``), channels,
  timepoints and fields, the completeness record (``run_check``) and the field
  quality block;
* ``provenance.jsonl`` (:mod:`util.provenance_log`) — the append-only history of
  runs, analysis edits, resets, derivations, repairs and earlier reports;
* the saved-plot records and what became of each in this report (drawn, or not
  drawn and why);
* the loaded wells and their row counts.

**Degraded input is stated, never smoothed over** (CLAUDE.md §7). No history
file, unreadable lines, lines from a newer build, a history that begins after a
plot was saved, an incomplete plate, a run that ended early, plots the report
could not draw: each becomes a sentence in :attr:`ReportCover.warnings` or on
the plot's own page. A cover that reads clean must mean the sources were clean.

:func:`build_cover` is pure (plain data in, a frozen dataclass out) so the fast
lane drives every sentence. :func:`cover_for` is the thin ctx wrapper, reached
as the ``saved_plots.report_cover`` service so the headless report names no
module of this pane.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

#: The event types that change what a redrawn plot shows. ``report_generated``
#: is not one: an earlier report changed nothing about the dataset.
CHANGE_EVENTS = ("run_finished", "analysis_changed", "reset", "derived_from",
                 "repaired", "recipe_applied")

#: Human words for each analysis domain the provenance log records
#: (``review.persistence.provenance.STORED_AT``). A domain missing here falls
#: back to its identifier with spaces; ``tests/test_report_cover.py`` holds
#: every recorded domain to a row so that fallback is never what a reader sees.
DOMAIN_LABELS: dict[str, str] = {
    "sample_definitions": "sample definitions",
    "sample_variables": "sample variables",
    "cell_gating": "cell gating",
    "smfish": "smFISH thresholds",
    "plot_groups": "saved selections",
    "qc_params": "QC thresholds",
    "crop_region": "crop region",
    "ratios": "ratios",
    "export_groups": "export groups",
    "field_exclusions": "field exclusions",
    "triage_decisions": "QC triage decisions",
    "cell_overrides": "per-cell curation",
    "cell_sets": "saved cells",
}

#: How many history rows the PDF prints before saying how many it left out.
#: The manifest carries every row.
MAX_HISTORY_ROWS_PRINTED = 40

#: Settings worth a line on the cover, in the order they are printed, each with
#: its label. Read from ``pipeline_info.json::execution_options`` (the run's
#: whole argument namespace); an option the run did not record is left out
#: rather than printed as a default the run may not have used.
SETTING_LABELS: tuple[tuple[str, str], ...] = (
    ("segmentation_method", "Segmentation"),
    ("cellpose_model", "Cellpose model"),
    ("stardist_prob_thresh", "StarDist probability threshold"),
    ("stardist_nms_thresh", "StarDist overlap (NMS) threshold"),
    ("min_nucleus_area_px", "Minimum nucleus area (px)"),
    ("background", "Background correction"),
    ("flatfield", "Flat-field correction"),
    ("qc_remove_debris", "Debris removal"),
    ("pos_control_well", "Positive control well"),
    ("neg_control_well", "Negative control well"),
)

#: Options that only mean something under one segmentation method.
_METHOD_ONLY = {
    "cellpose_model": ("cellpose",),
    "stardist_prob_thresh": ("stardist",),
    "stardist_nms_thresh": ("stardist",),
}


@dataclass(frozen=True)
class HistoryRow:
    """One line of the printed history, already in words."""

    when: str          # local time, "YYYY-MM-DD HH:MM", or "" when unknown
    source: str        # analyze / review / datasets / report
    what: str
    app_version: str = ""


@dataclass(frozen=True)
class ReportCover:
    """Everything the report's cover and history pages print.

    Each section is a tuple of ``(label, value)`` pairs, already worded, so the
    renderer lays text out and decides nothing.
    """

    dataset_name: str
    dataset_path: str
    generated_at: str
    app_version: str | None
    timezone: str
    dataset: tuple[tuple[str, str], ...] = ()
    settings: tuple[tuple[str, str], ...] = ()
    last_run: tuple[tuple[str, str], ...] = ()
    analysis: tuple[tuple[str, str], ...] = ()
    report: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
    history: tuple[HistoryRow, ...] = ()
    history_status: str = ""
    #: Per saved-plot id: the sentence printed under that plot's header, saying
    #: what changed in the dataset after the plot was saved. Absent for a plot
    #: nothing changed under.
    since_saved: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


# ── time ────────────────────────────────────────────────────────────────────


def _parse_time(text) -> datetime | None:
    """An aware datetime for *text*, or ``None`` when it does not parse.

    Provenance timestamps carry ``+00:00``. A saved plot's ``created_at`` is
    written naive, in the saving machine's local time, so a naive value is
    read as this machine's local time — exact when the report runs where the
    plot was saved, and off by the zone difference when it does not.
    """
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return moment.astimezone() if moment.tzinfo is None else moment


def _local(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else ""


def _zone_label() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return f"UTC{offset[:3]}:{offset[3:]}" if offset else (now.tzname() or "local")


# ── words ───────────────────────────────────────────────────────────────────


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _listed(items, limit: int = 6) -> str:
    items = [str(i) for i in items if str(i)]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def _capital(text: str) -> str:
    """*text* with its first letter raised, and nothing else lowered ("QC")."""
    return text[:1].upper() + text[1:]


def domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, str(domain).replace("_", " "))


def _scope_words(scope: dict | None) -> str:
    """What a ``run_started`` scope says about which wells a run touched."""
    scope = scope or {}
    parts = []
    if scope.get("only_wells"):
        parts.append("only " + _listed(scope["only_wells"]))
    if scope.get("incremental"):
        parts.append("incremental")
    if scope.get("force"):
        parts.append("forced")
    elif scope.get("force_wells"):
        parts.append("forced " + _listed(scope["force_wells"]))
    if scope.get("skip_wells"):
        parts.append("skipping " + _listed(scope["skip_wells"]))
    return ", ".join(parts)


def _run_check_words(run_check: dict | None) -> str:
    """One clause for a ``run_finished`` event's completeness summary."""
    if not isinstance(run_check, dict):
        return "no completeness record for this run"
    if run_check.get("skipped"):
        return "completeness check turned off"
    totals = run_check.get("totals") or {}
    from pipeline.completeness import plate_total
    plate = plate_total(totals)
    text = (f"{int(totals.get('complete') or 0):,} of {plate:,} well(s) complete"
            if plate else "no wells recorded")
    files = int((run_check.get("errors") or {}).get("files") or 0)
    if files:
        text += f", {_plural(files, 'error record')}"
    if run_check.get("suspended"):
        text += ", suspended"
    return text


def _describe(event: dict) -> str:
    """One event's sentence. Unknown types are named, not dropped."""
    kind = event.get("event")
    d = event.get("detail") or {}
    if kind == "run_finished":
        text = f"Analysis run ({d.get('mode') or 'unknown mode'}) {d.get('outcome') or 'ended'}"
        return f"{text}: {_run_check_words(d.get('run_check'))}"
    if kind == "run_started":
        return (f"Analysis run ({d.get('mode') or 'unknown mode'}) started; "
                f"no end was recorded")
    if kind == "reset":
        return "Reset: " + (_listed(domain_label(s) for s in d.get("scopes") or ())
                            or "nothing named")
    if kind == "derived_from":
        sources = [Path(str(s)).name or str(s) for s in d.get("sources") or ()]
        return f"Created by {d.get('tool') or 'a Datasets tool'} from {_listed(sources) or 'unnamed sources'}"
    if kind == "repaired":
        actions = d.get("actions") or ()
        return f"Repaired by Validate & Repair ({_plural(len(actions), 'action')})"
    if kind == "recipe_applied":
        text = (f"Analysis copied from {Path(str(d.get('source') or '')).name or 'another dataset'}: "
                f"{_listed(domain_label(s) for s in d.get('scopes') or ()) or 'nothing'}")
        failed = d.get("failed") or ()
        if failed:
            text += f"; did not copy: {_listed(domain_label(s) for s in failed)}"
        return text
    if kind == "report_generated":
        return (f"Headless report: {d.get('status') or 'unknown'} "
                f"({int(d.get('drawn') or 0)} drawn, {int(d.get('not_drawn') or 0)} not drawn)")
    return f"{kind or 'unnamed event'} (this build has no wording for it)"


def history_rows(events: list[dict]) -> list[HistoryRow]:
    """The log as printed rows, oldest first.

    Two kinds of collapse, both so the page reads as a history rather than a
    keystroke log, and neither drops anything a reader needs:

    * a ``run_started`` followed by its ``run_finished`` is one row (the finish,
      carrying the start's scope); a start with no finish before the next start
      or the end of the log is its own row saying no end was recorded;
    * consecutive ``analysis_changed`` events are one row naming each domain
      and how many saves it took, timed at the last of them.
    """
    rows: list[HistoryRow] = []
    pending_start: dict | None = None
    edits: list[dict] = []

    def row(event: dict, what: str) -> HistoryRow:
        return HistoryRow(when=_local(_parse_time(event.get("ts"))),
                          source=str(event.get("source") or ""), what=what,
                          app_version=str(event.get("app_version") or ""))

    def flush_edits() -> None:
        if not edits:
            return
        counts: dict[str, int] = {}
        for e in edits:
            dom = str((e.get("detail") or {}).get("domain") or "")
            counts[dom] = counts.get(dom, 0) + 1
        what = "Analysis edited: " + ", ".join(
            domain_label(dom) + (f" ×{n}" if n > 1 else "") for dom, n in counts.items())
        rows.append(row(edits[-1], what))
        edits.clear()

    def flush_start() -> None:
        nonlocal pending_start
        if pending_start is not None:
            rows.append(row(pending_start, _describe(pending_start)))
            pending_start = None

    for event in events:
        kind = event.get("event")
        if kind != "analysis_changed":
            flush_edits()
        if kind == "analysis_changed":
            edits.append(event)
        elif kind == "run_started":
            flush_start()
            pending_start = event
        elif kind == "run_finished":
            what = _describe(event)
            scope = _scope_words((pending_start or {}).get("detail", {}).get("scope"))
            if scope:
                what += f" [{scope}]"
            pending_start = None
            rows.append(row(event, what))
        else:
            rows.append(row(event, _describe(event)))
    flush_edits()
    flush_start()
    return rows


def since_saved_sentence(created_at, events: list[dict], *,
                         history_known: bool) -> str:
    """What changed in the dataset after a plot was saved, or ``""``.

    *history_known* is ``False`` when there is no readable history at all; the
    cover says that once, so a plot's page stays quiet rather than repeating
    it. A history whose first line is later than the plot is said on the page:
    "nothing recorded since" would otherwise read as "nothing happened".
    """
    saved = _parse_time(created_at)
    if saved is None or not history_known:
        return ""
    stamped = [(e, _parse_time(e.get("ts"))) for e in events]
    stamped = [(e, t) for e, t in stamped if t is not None]
    if not stamped:
        return ""
    if stamped[0][1] > saved:
        return (f"The dataset's history begins {_local(stamped[0][1])}, after this "
                f"plot was saved ({_local(saved)}); changes before then are not recorded.")
    # ``>=``: both stamps are to the second, so an edit made in the same second
    # as the save cannot be ordered against it. It is reported, because a
    # change wrongly named costs a reader a second look, and a change wrongly
    # left out lets a redrawn figure pass for the one that was saved.
    later = [(e, t) for e, t in stamped if t >= saved and e.get("event") in CHANGE_EVENTS]
    if not later:
        return ""
    runs = [t for e, t in later if e.get("event") == "run_finished"]
    domains: list[str] = []
    other: list[str] = []
    for e, _t in later:
        kind = e.get("event")
        d = e.get("detail") or {}
        if kind == "analysis_changed":
            label = domain_label(str(d.get("domain") or ""))
            if label not in domains:
                domains.append(label)
        elif kind == "reset":
            other.append("reset (" + _listed(domain_label(s) for s in d.get("scopes") or ()) + ")")
        elif kind == "recipe_applied":
            other.append("analysis copied from another dataset")
        elif kind == "repaired":
            other.append("repaired by Validate & Repair")
        elif kind == "derived_from":
            other.append(f"rewritten by {d.get('tool') or 'a Datasets tool'}")
    parts = []
    if runs:
        times = "once" if len(runs) == 1 else f"{len(runs)} times"
        parts.append(f"re-analysed {times} (last {_local(runs[-1])})")
    if domains:
        parts.append("changed " + _listed(domains))
    for text in other:
        if text not in parts:
            parts.append(text)
    return (f"Redrawn from the dataset as it is now. Since this plot was saved "
            f"({_local(saved)}): " + "; ".join(parts) + ".")


# ── sections ────────────────────────────────────────────────────────────────


def _tokens(values) -> str:
    return ", ".join(str(v) for v in values or () if str(v)) or ""


def _dataset_section(info: dict, *, n_wells: int, n_cells: int | None) -> list:
    out = [("Wells loaded", f"{n_wells:,}")]
    if n_cells is not None:
        out.append(("Cells measured", f"{n_cells:,} (rows in the per-well tables, "
                                       f"before any gate or exclusion)"))
    if info.get("nuclear_token"):
        out.append(("Nuclear channel", str(info["nuclear_token"])))
    if info.get("fluor_tokens"):
        out.append(("Quantified channels", _tokens(info["fluor_tokens"])))
    if info.get("smfish_tokens"):
        out.append(("smFISH channels", _tokens(info["smfish_tokens"])))
    if info.get("phase_token"):
        out.append(("Phase channel", str(info["phase_token"])))
    tps = list(info.get("available_timepoints") or ())
    if tps:
        span = f" ({tps[0]} to {tps[-1]})" if len(tps) > 1 else f" ({tps[0]})"
        out.append(("Timepoints", f"{len(tps)}{span}"))
    fovs = list(info.get("available_fovs") or ())
    if fovs:
        out.append(("Fields per well", str(len(fovs))))
    if info.get("schema"):
        out.append(("Filename schema", f"{info['schema']} (separator {info.get('separator', '')!r})"))
    return out


def _settings_section(info: dict) -> list:
    opts = info.get("execution_options")
    if not isinstance(opts, dict):
        opts = {}
    method = str(opts.get("segmentation_method") or info.get("segmentation_method") or "")
    out = []
    for key, label in SETTING_LABELS:
        value = opts.get(key, info.get(key) if key in ("segmentation_method",
                                                        "min_nucleus_area_px") else None)
        if value is None or value == "" or value == []:
            continue
        needs = _METHOD_ONLY.get(key)
        if needs and not any(n in method for n in needs):
            continue
        if isinstance(value, bool):
            value = "on" if value else "off"
        out.append((label, str(value)))
    return out


def _last_run_section(info: dict, warnings: list[str]) -> list:
    block = info.get("run_check")
    if not isinstance(block, dict) or not block:
        warnings.append("No completeness record: whether every well and field was "
                        "processed is unknown. Datasets → Validate & Repair can check it.")
        return []
    if block.get("skipped"):
        warnings.append("The completeness check was turned off for the last run: "
                        "whether every well and field was processed is unknown.")
        return [("Completeness", "not checked (turned off for that run)")]
    from pipeline.completeness import plate_total, unreadable_note
    totals = block.get("totals") or {}
    out = []
    when = _local(_parse_time(block.get("finished_at")))
    if when:
        out.append(("Finished", when))
    if block.get("mode"):
        out.append(("Mode", str(block["mode"])))
    if block.get("host"):
        out.append(("Host", str(block["host"])))
    if block.get("duration_s"):
        out.append(("Duration", f"{float(block['duration_s']) / 60:.1f} min"))
    plate = plate_total(totals)
    complete = int(totals.get("complete") or 0)
    text = f"{complete:,} of {plate:,} well(s) complete"
    note = unreadable_note(totals)
    if note:
        text += f"; {note}"
    out.append(("Wells", text))
    short = []
    for key, word in (("partial", "partial"), ("missing_csv", "missing their table")):
        n = int(totals.get(key) or 0)
        if n:
            short.append(f"{n:,} {word}")
    if short or note:
        warnings.append(f"Plate incomplete: {', '.join(short) or note}. Plots drawn "
                        f"from those wells use fewer fields than the plate holds.")
    if block.get("ended_early"):
        warnings.append("The last run did not reach its own end (stopped, suspended, "
                        "or failed); wells it never reached have no data.")
    if block.get("partial_scan"):
        warnings.append("The completeness check stopped part way; some wells were "
                        "never checked.")
    errors = block.get("errors") or {}
    files = int(errors.get("files") or 0)
    if files:
        kinds = ", ".join(f"{k} ×{v}" for k, v in sorted((errors.get("by_type") or {}).items()))
        out.append(("Error records", f"{files:,}" + (f" ({kinds})" if kinds else "")))
        warnings.append(f"The last run left {_plural(files, 'error record')} in "
                        f"errors/: some fields failed and are missing from the tables.")
    from pipeline.field_quality_record import FIELD_QUALITY_BLOCK, summarize
    quality = summarize(info.get(FIELD_QUALITY_BLOCK))
    if quality:
        out.append(("Field quality", quality))
    return out


def _analysis_section(events: list[dict]) -> list:
    """The last recorded change to each analysis domain, newest first."""
    last: dict[str, datetime] = {}
    for e in events:
        if e.get("event") != "analysis_changed":
            continue
        t = _parse_time(e.get("ts"))
        dom = str((e.get("detail") or {}).get("domain") or "")
        if t is not None and dom:
            last[dom] = t
    ordered = sorted(last.items(), key=lambda kv: kv[1], reverse=True)
    return [(_capital(domain_label(dom)), f"last changed {_local(t)}") for dom, t in ordered]


def _history_status(read, rows: list[HistoryRow], events: list[dict],
                    warnings: list[str]) -> str:
    if read is None or getattr(read, "missing", True):
        warnings.append("No history (provenance.jsonl) was found for this dataset: "
                        "which runs and edits produced these numbers cannot be shown.")
        return "No history recorded for this dataset."
    first = _local(_parse_time(events[0].get("ts"))) if events else ""
    text = (f"{_plural(len(events), 'event')} recorded"
            + (f" since {first}" if first else "") + f", shown as {_plural(len(rows), 'row')}.")
    gaps = []
    if read.unreadable:
        gaps.append(f"{_plural(read.unreadable, 'line')} could not be read")
    if read.foreign:
        gaps.append(f"{_plural(read.foreign, 'line')} came from a newer version of All-Well")
    if gaps:
        text += " Incomplete: " + "; ".join(gaps) + "."
        warnings.append("The dataset's history is incomplete: " + "; ".join(gaps) + ".")
    return text


def build_cover(*, info: dict, read, records, outcomes: dict[str, tuple[str, str]],
                dataset_name: str, dataset_path: str, n_wells: int,
                n_cells: int | None, app_version: str | None,
                generated_at: datetime | None = None) -> ReportCover:
    """The cover for one report. Pure.

    *read* is a :class:`util.provenance_log.ProvenanceRead` (or ``None`` for no
    log). *outcomes* maps a saved plot's id to ``(status, reason)``, status
    ``"drawn"`` or ``"not_drawn"``.
    """
    info = info if isinstance(info, dict) else {}
    events = list(getattr(read, "events", None) or [])
    warnings: list[str] = []
    rows = history_rows(events)
    last_run = _last_run_section(info, warnings)
    status = _history_status(read, rows, events, warnings)
    records = list(records or ())

    drawn = [r for r in records if (outcomes.get(r.id) or ("",))[0] == "drawn"]
    not_drawn = [(r, (outcomes.get(r.id) or ("", "not attempted"))[1])
                 for r in records if r not in drawn]
    report = [("Saved plots", f"{len(records):,}"), ("Drawn", f"{len(drawn):,}")]
    if not_drawn:
        report.append(("Not drawn", f"{len(not_drawn):,}"))
        for rec, reason in not_drawn:
            report.append(("  " + (rec.title or rec.tab or rec.id), reason or "no reason given"))
        warnings.append(f"{_plural(len(not_drawn), 'saved plot')} could not be redrawn "
                        f"and {'is' if len(not_drawn) == 1 else 'are'} not in this PDF "
                        f"(listed under Report).")

    history_known = read is not None and not getattr(read, "missing", True)
    since = {}
    for rec in drawn:
        text = since_saved_sentence(getattr(rec, "created_at", ""), events,
                                    history_known=history_known)
        if text:
            since[rec.id] = text
    if since:
        warnings.append(f"{_plural(len(since), 'plot')} {'was' if len(since) == 1 else 'were'} "
                        f"saved before the dataset last changed; each page says what changed.")

    return ReportCover(
        dataset_name=dataset_name, dataset_path=dataset_path,
        generated_at=_local(generated_at or datetime.now().astimezone()),
        app_version=app_version, timezone=_zone_label(),
        dataset=tuple(_dataset_section(info, n_wells=n_wells, n_cells=n_cells)),
        settings=tuple(_settings_section(info)),
        last_run=tuple(last_run),
        analysis=tuple(_analysis_section(events)),
        report=tuple(report),
        warnings=tuple(warnings),
        history=tuple(reversed(rows)),
        history_status=status,
        since_saved=since,
    )


def cover_for(ctx, records, outcomes, *,
              app_version: str | None = None) -> ReportCover:
    """The ``saved_plots.report_cover`` service: :func:`build_cover` for the open dataset."""
    from review.domain.viewer_state import resolve_pipeline_info_path
    from util.provenance_log import read_events

    dataset = ctx.state.dataset
    data_dir = Path(dataset.data_dir)
    read = read_events(resolve_pipeline_info_path(data_dir).parent)
    try:
        n_cells = sum(int(s.n_rows) for s in dataset.well_summaries())
    except Exception:  # a summary that cannot be read is an unknown count, not zero
        import logging
        logging.getLogger("review").warning(
            "report cover: could not count cells", exc_info=True)
        n_cells = None
    from review.domain.dataset_name import dataset_display_name
    return build_cover(
        info=dict(dataset.pipeline_info or {}), read=read, records=records,
        outcomes=outcomes, dataset_name=dataset_display_name(data_dir),
        # Where the sidecar and the history live: the dataset root for the
        # in/out layout, the loaded folder itself for a flat one.
        dataset_path=str(resolve_pipeline_info_path(data_dir).parent),
        n_wells=len(dataset.well_paths or ()),
        n_cells=n_cells, app_version=app_version)
