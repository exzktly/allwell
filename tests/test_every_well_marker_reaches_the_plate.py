"""Every per-well line the pipeline can print reaches Analyze's run plate as the state it means.

Reported (tracker B3): the run plate painted partial wells complete. A well whose FOV-groups failed, whose
groups were never attempted after a storage failure, that produced no records, or — in nanowell mode — whose
timepoints all came out without rows, still prints "Well X — done.", because it did write a CSV and an out.zip
from what it had; the plate keyed on that line alone, so those wells were green (or blue, "processed") exactly
like whole ones, and the pipeline's own exit line had to say "partial wells look complete on the plate map".
Wells that were computing when a run died stayed amber "processing" on a plate whose run was over.

Two halves, both decided here:

* **The roster is the pipeline's, not mine.** Every string literal on the run's stream that names a well —
  ``Well <w> …``, ``SKIP well <w>``, ``FAILED well <w>`` — is collected from the source by AST
  (``process_microscopy.py``, ``pipeline/``, ``services/``) and must have a row in ``ROSTER``. A new marker
  added anywhere fails this test until somebody says what it means for the plate, so the next "PARTIAL"
  cannot be silently ignored the way these were.
* **Each row is driven through the real thing.** The sample line is rendered *from the template itself*
  (``template % args``), prefixed the way the pipeline's logger prints it, read by the real
  ``ProgressTracker`` and applied by the real ``AnalyzeTab``; the assertion is on what reached the plate
  widget — its ``setWellColor`` and ``setWellMarker`` calls — and on the hover text, never on a value computed
  behind them (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import ast
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tests._repo_scan import parse_source, python_sources, rel
from ui import theme

WELL = "A01"
_PREFIX = {"I": "2026-05-27 10:30:45  INFO      ", "W": "2026-05-27 10:30:45  WARNING   ",
           "E": "2026-05-27 10:30:45  ERROR     ", "-": ""}

# ── what the plate is told, per outcome ─────────────────────────────────────────
# (colour token or None, glyph or None). "complete" is a local run's processed well; "fetched" a remote one's.
_STATES = {
    "processing": ("run_processing", None),
    "complete": ("run_on_server", None),
    "fetched": ("run_done", None),
    "partial": ("run_partial", "!"),
    "failed": ("run_failed", "✕"),
    "pending": (None, None),
}

# ── the roster: template → [(args, prefix, outcome, tooltip fragment or "")] ───────────────────────────────
# Outcomes: a key of _STATES; "note" — the line changes nothing on the plate (the reason is the comment beside
# it); "progress" — it moves the well's dial (a well_fov / well_sites event); "reanchor" — the end-of-run
# re-anchoring's own events (tests/test_a_reanchored_well_is_not_shown_done.py drives those through the plate).
# Partial rows are followed by "Well A01 — done." unless the marker is itself terminal, because that is the
# order the pipeline prints them in.
W = WELL
ROSTER: dict[str, list[tuple]] = {
    # orchestrator / worker failures
    "FAILED well %s: %s.%s": [((W, "boom", ""), "E", "failed", "")],
    "FAILED well %s: %s — Bad file descriptor (errno 9).%s If the trace file is empty the worker's stderr was "
    "likely broken before the error site; the per-well trace file is the authoritative source.":
        [((W, "OSError", ""), "E", "failed", "")],
    "FAILED well %s: worker process died unexpectedly (%s: %s).%s This typically means the worker was killed by "
    "the OS — most commonly out-of-memory, signal, or a native crash inside a C extension (StarDist / TF / "
    "tifffile). Reduce --workers or the per-well memory footprint and try again.":
        [((W, "BrokenProcessPool", "x", ""), "E", "failed", "")],
    "Well %s failed (%s: %s); worker trace written to %s": [((W, "OSError", "x", "/o/errors/t.txt"), "E", "failed", "")],
    "Well %s failed (%s: %s); could not write worker trace file": [((W, "OSError", "x"), "E", "failed", "")],
    # skips — complete without computing
    "SKIP well %s — output already exists. Use --force to reprocess.": [((W,), "I", "complete", "")],
    "SKIP well {} — output already exists locally; not uploaded. Use --force to reprocess.\n":
        [((W,), "-", "fetched", "")],
    "SKIP well {} — empty-well reference only; already complete locally.\n": [((W,), "-", "fetched", "")],
    # the well's lifecycle
    "Well %s — starting (pid %d)": [((W, 12), "I", "processing", "")],
    "Well %s — starting (folder mode, pid %d)": [((W, 12), "I", "processing", "")],
    "Well %s — done.": [((W,), "I", "complete", "")],
    "Well {} — done. (finished on the remote by an earlier attempt)\n": [((W,), "-", "complete", "")],
    "Well %s — done. %d timepoint(s), %d without rows.": [
        ((W, 4, 0), "I", "complete", ""),
        ((W, 4, 4), "I", "partial", "4 of 4 timepoint(s) without rows"),
        ((W, 4, 1), "I", "partial", "1 of 4 timepoint(s) without rows")],
    "Well %s — up to date (no new FOVs).": [((W,), "I", "complete", "")],
    "Well %s — up to date (no new timepoints).": [((W,), "I", "complete", "")],
    "Well %s — not started (the run is suspending).": [((W,), "I", "pending", "")],
    "Well %s — not started (the run suspended before its first field).": [((W,), "I", "pending", "")],
    # a well whose output is short of its input
    "Well %s — PARTIAL: %d of %d FOV-group(s) FAILED (%s). The per-well CSV / out.zip cover only the %d "
    "successful FOV-group(s). Re-run with --incremental to recover the missing ones.%s":
        [((W, 9, 12, "f1, f2", 3, ""), "W", "partial", "9 of 12 FOV-group(s) failed")],
    "Well %s — NOT ATTEMPTED: %d FOV-group(s) were skipped after the well aborted (%s): %s. There is no per-FOV "
    "traceback for these — the reason is the abort itself.":
        [((W, 7, "3 consecutive I/O failures", "f4"), "W", "partial", "7 FOV-group(s) not attempted")],
    "Well %s — ABORTING after %s. The remaining FOV-group(s) are not attempted; re-run with --incremental once "
    "the storage problem is fixed.": [((W, "3 consecutive I/O failures"), "E", "partial", "storage")],
    "Well %s — storage failure: %s. The in-flight FOV-group(s) will finish or fail on their own; re-run with "
    "--incremental once it is fixed.": [((W, "3 consecutive I/O failures"), "E", "partial", "storage")],
    "Well %s — SUSPENDED part way: %d of %d FOV-group(s) not reached; resuming processes them.":
        [((W, 5, 12), "W", "partial", "5 of 12 FOV-group(s) not reached before the suspend")],
    "Well %s: no records produced; CSV not written.": [((W,), "W", "partial", "no records produced")],
    "Well %s: no records produced; CSV not updated.": [((W,), "W", "partial", "no records produced")],
    # terminal on its own: no "— done." follows it
    "Well %s: no image groups found in %s.": [((W, "A01.zip"), "W", "partial", "no image groups found in A01.zip")],
    # progress — the dial
    "Well %s — %d FOV-group(s)": [((W, 12), "I", "progress", "")],
    "Well %s — FOV %d/%d done": [((W, 3, 12), "I", "progress", "")],
    "Well %s — %d step(s): its %d timepoint(s), each located, then measured": [((W, 4, 2), "I", "progress", "")],
    "Well %s — step %d/%d done": [((W, 1, 4), "I", "progress", "")],
    "Well %s — %s: %d/%d nanowells segmented": [((W, "0h", 0, 400), "I", "progress", "")],
    "Well %s — %s: %d/%d nanowells segmented (%s)": [((W, "0h", 40, 400, "6.0 s each"), "I", "progress", "")],
    # the end-of-run re-anchoring (its own events and plate states)
    "Well %s — held for the end-of-run re-anchoring (%s unaddressed).": [((W, "T03"), "I", "reanchor", "")],
    "Well %s — re-anchoring %s from the plate's other wells.": [((W, "T03"), "I", "reanchor", "")],
    "Well %s — re-anchoring failed (%s: %s); its timepoints stay unaddressed.":
        [((W, "ValueError", "x"), "E", "reanchor", "")],
    "Well {} — re-anchored. {} timepoint(s), {} without rows.": [((W, 4, 0), "I", "reanchor", "")],
    "Well {} — re-anchoring placed none of {}; its earlier result stands.": [((W, "T03"), "I", "reanchor", "")],
    # notes: true, worth reading in the log, and no change to what the well is
    "Well %-12s -> %s  (%d rows)": [((W, "m_A01.csv", 10), "I", "note", "")],          # the CSV it wrote
    "Well %-12s -> %s  (+%d row(s), merged)": [((W, "m_A01.csv", 10), "I", "note", "")],
    "Well %s: %d image(s) -> %s": [((W, 3, "A01_out.zip"), "I", "note", "")],             # the out.zip it wrote
    "Well %s: merged %d new image(s) -> %s": [((W, 3, "A01_out.zip"), "I", "note", "")],
    "Well %s: compressed input folder -> %s (%d image(s))": [((W, "x.zip", 3), "I", "note", "")],
    "Well %s: compressed output folder -> %s (%d image(s))": [((W, "x.zip", 3), "I", "note", "")],
    "Well %s: output folder -> %s": [((W, "A01_out"), "I", "note", "")],
    "Well %s — temporary directories removed.": [((W,), "I", "note", "")],                # after "— done."
    "Well %s — staging directory removed.": [((W,), "I", "note", "")],
    "Well %s: removing stale tmp dir %s": [((W, ".tmp"), "W", "note", "")],
    "Well %s: resuming from %d FOV checkpoint(s) in %s": [((W, 3, ".s"), "I", "note", "")],
    "Well %s — keeping its %d FOV checkpoint(s) for the resume.": [((W, 3), "I", "note", "")],
    "Well %s — merging %d new FOV(s).": [((W, 3), "I", "note", "")],                       # incremental, computing
    "Well %s — no prior CSV; recovered %d done FOV-group(s) from processed outputs.": [((W, 3), "I", "note", "")],
    "Well %s — the recorded settings differ from this run's; recomputing every FOV-group.":
        [((W,), "I", "note", "")],
    # the well's own seal could not be withdrawn: the raise that follows fails the well ("Well X failed (…)")
    "Well %s: could not withdraw its completion seal %s (%s: %s); not rewriting the well, so its earlier output "
    "and seal are left as they were.": [((W, "s", "OSError", "x"), "E", "note", "")],
    "Well {}: could not withdraw its completion seal {} ({}: {}); the well was not rewritten":
        [((W, "s", "OSError", "x"), "-", "note", "")],
    # the host's own skip (review B1, F7): a reuse across builds is named before the SKIP line that marks the
    # well; a part-way well is named before its "starting" line. Neither changes the well by itself.
    "Well %s was finished by %s, not this build (%s); its output is reused as it is — use --force to recompute "
    "it with this build.": [((W, "build 9.1.0", "9.2.0"), "I", "note", "")],
    "Well %s has output here, but it was left part way (no completion seal) — running it again.":
        [((W,), "I", "note", "")],
    # the withdrawn-seal note's own bookkeeping, logged suppressed; the well's outcome comes from its own lines
    "Well {}: could not leave the note that its completion seal is withdrawn ({})": [((W, "n"), "-", "note", "")],
    "Well {}: clearing the note that its completion seal was withdrawn": [((W,), "-", "note", "")],
    # nanowell context: registration, anchors, the acquisition record, a reanalysis, a timepoint's own status
    # (a timepoint without rows is counted on the well's own "done." line, above)
    "Well %s timepoint %s: %s (%s)": [((W, "0h", "unaddressed", "no anchor"), "W", "note", "")],
    "Well %s — %s": [((W, "located 0h"), "I", "note", "")],
    "Well %s — %s.": [((W, "re-analysed because the array radius changed"), "W", "note", "")],
    "Well %s — anchor set by hand at %s.": [((W, "0h"), "I", "note", "")],
    # the empty reference well every confluence is read against (pipeline/nanowell_reference.py): notes, not states
    "Well %s — empty reference saved for %d timepoint(s); every other well's confluence is read against it.":
        [((W, 2), "I", "note", "")],
    "Well %s is the empty reference well, but it is not empty at %s: no well has a confluence there":
        [((W, "0h (loaded)"), "W", "note", "")],
    # the identity check (pipeline/nanowell_identity.py): a verdict on whether each address names the same nanowell
    # at every timepoint, kept in nanowells/<well>_identity.json and shown by Review's figures and Identity view. It
    # does not change what the run wrote, so the well's own outcome is its "done." line, as for an unaddressed
    # timepoint above
    "Well %s timepoint %s: nanowell identity %s against %s (%s)": [
        ((W, "24h", "contradicted", "0h", "every address is one lattice step off"), "W", "note", "")],
    "Well %s timepoint %s: nanowell identity contradicted by the stage positions (%s)": [
        ((W, "24h", "172 um off the plate's motion"), "W", "note", "")],
    "Well %s — no identity sidecar to record the stage cross-check in": [((W,), "W", "note", "")],
    "Well %s — the montage archive cannot be read for the identity check: %s": [((W, "bad zip"), "W", "note", "")],
    "Well %s — registration reused from %s for %d of %d timepoint(s): %s.": [((W, "o", 1, 2, "0h"), "I", "note", "")],
    # A plain run decides a well is not up to date because its confluence was read against another empty reference
    # (`nanowell_task.confluence_reference_current`): the well then runs and prints its own markers.
    "Well %s — its confluence at %s was read against another empty reference than the one saved now; it is analysed "
    "again.": [((W, "0h"), "I", "note", "")],
    "Well %s — the grid record %s could not be read, so its registration is not reused (%s).":
        [((W, "g.json", "x"), "W", "note", "")],
    "Well %s has no acquisition record: tiles placed from a %s grid, scan %s, %s%% overlap, %s um per pixel, "
    "fields in the order of their names": [((W, "3x3", "snake", 10, 0.5), "I", "note", "")],
    "Well %s: the acquisition record has no mosaic position or no pixel size for it, so it is placed from the "
    "described grid.": [((W,), "I", "note", "")],
    "Well %s: its nanowells measure %.1f um, %+.0f %% off the %.1f um the plate%s's other wells share; check the "
    "tile its diameter was measured on.": [((W, 40.0, 12.0, 36.0, ""), "W", "note", "")],
}

#: Where a per-well line can be printed onto the run's stream. ``util/`` holds only validation messages about a
#: well *order* (``plate_grid``), and ``pipeline_runner`` holds the patterns that read the markers, not markers.
_NOT_MARKERS = {"services/pipeline_runner.py", "util/plate_grid.py"}
_STARTS = re.compile(r"^(?:Well (?:%s|%-12s|\{\})|SKIP well (?:%s|\{\})|FAILED well (?:%s|\{\}))")


def _template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def _emitted_markers() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in python_sources(roots=("pipeline", "services")):
        if rel(path) in _NOT_MARKERS:
            continue
        tree = parse_source(path)
        inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
        for node in ast.walk(tree):
            if id(node) in inner:
                continue
            text = _template(node)
            if text and _STARTS.match(text):
                found.setdefault(text, []).append(f"{rel(path)}:{node.lineno}")
    return found


def test_every_per_well_marker_the_pipeline_prints_has_a_row():
    emitted = _emitted_markers()
    assert len(emitted) > 40, f"the scan found only {len(emitted)} markers; it is not reading the pipeline"
    unclassified = {t: where for t, where in emitted.items() if t not in ROSTER}
    stale = sorted(t for t in ROSTER if t not in emitted)
    assert not unclassified, (
        "per-well line(s) the run plate has no row for — say what each means for the well (and make "
        "ProgressTracker produce it) before it ships:\n" +
        "\n".join(f"  {t!r}  at {', '.join(w)}" for t, w in sorted(unclassified.items())))
    assert not stale, f"roster rows naming a line nothing prints any more: {stale}"


def _render(template: str, args: tuple) -> str:
    return template.format(*args) if "{}" in template else template % args


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    plate = t._dashboard._plate
    t.colours, t.glyphs = [], []
    real_colour, real_glyph = plate.setWellColor, plate.setWellMarker
    plate.setWellColor = lambda w, c: (t.colours.append((w, c)), real_colour(w, c))
    plate.setWellMarker = lambda w, g: (t.glyphs.append((w, g)), real_glyph(w, g))
    yield t
    t._alive = False


def _drive(tab, lines: list[str], *, remote: bool) -> list[tuple[str, object]]:
    from services.pipeline_runner import ProgressTracker

    tab._dashboard.reset(remote=remote, total=1)
    tab.colours.clear()
    tab.glyphs.clear()
    reader = ProgressTracker()
    events = []
    for line in lines:
        for kind, payload in reader.parse(line):
            events.append((kind, payload))
            tab._apply_progress_event(kind, payload)
    return events


def _token(colour: str | None) -> str | None:
    if colour is None:
        return None
    names = [k for k, v in vars(theme.Colors).items() if v == colour and not k.startswith("_")]
    # The run plate paints with the ``run_*`` tokens, some of which share a
    # value with a general token (``run_done`` is ``success``'s green), so the
    # plate's own name wins.
    names.sort(key=lambda k: not k.startswith("run_"))
    return names[0] if names else colour


def _last(calls: list, default=None):
    mine = [value for w, value in calls if w == WELL]
    return mine[-1] if mine else default


def test_each_marker_reaches_the_plate_as_the_state_it_means(tab):
    wrong = []
    for template, rows in ROSTER.items():
        for args, prefix, outcome, fragment in rows:
            line = _PREFIX[prefix] + _render(template, args).rstrip("\n")
            remote = outcome == "fetched" or prefix == "-" and template.startswith("SKIP")
            started = [_PREFIX["I"] + "Zip mode: 1 wells", _PREFIX["I"] + f"Well {WELL} — starting (pid 1)"]
            lines = [*started, line]
            if outcome == "partial" and " done." not in line and "no image groups" not in line:
                lines.append(_PREFIX["I"] + f"Well {WELL} — done.")
            if template.startswith("SKIP") or "not started (the run is suspending)" in template:
                lines = [_PREFIX["I"] + "Zip mode: 1 wells", line]   # never started; nothing came before it
            events = _drive(tab, lines, remote=remote)
            kinds = {k for k, _p in events}
            if outcome in _STATES:
                want = _STATES[outcome]
                got = (_token(_last(tab.colours)), _last(tab.glyphs))
                if got != want:
                    wrong.append(f"{line!r}: plate told {got}, wants {want} ({outcome})")
                tip = tab._dashboard._well_tooltip(WELL)
                if fragment and fragment not in tip:
                    wrong.append(f"{line!r}: hover text {tip!r} does not name {fragment!r}")
            elif outcome == "note":
                before = _drive(tab, started, remote=False) and (_token(_last(tab.colours)), _last(tab.glyphs))
                _drive(tab, lines, remote=False)
                after = (_token(_last(tab.colours)), _last(tab.glyphs))
                if after != before:
                    wrong.append(f"{line!r}: a note changed the well from {before} to {after}")
            elif outcome == "progress" and not kinds & {"well_fov", "well_sites"}:
                wrong.append(f"{line!r}: no dial event (got {sorted(kinds)})")
            elif outcome == "reanchor" and "well_reanchor" not in kinds:
                wrong.append(f"{line!r}: no re-anchoring event (got {sorted(kinds)})")
    assert not wrong, "\n".join(wrong)


def test_a_well_still_computing_when_the_run_ends_is_unfinished_not_processing(tab):
    _drive(tab, [_PREFIX["I"] + "Zip mode: 2 wells", _PREFIX["I"] + f"Well {WELL} — starting (pid 1)",
                 _PREFIX["I"] + f"Well {WELL} — 12 FOV-group(s)", _PREFIX["I"] + f"Well {WELL} — FOV 3/12 done"],
           remote=False)
    assert (_token(_last(tab.colours)), _last(tab.glyphs)) == ("run_processing", None)
    tab._log_q.put(("finished", None))      # the run's last event, through the tab's own drain
    tab._poll_log()
    assert (_token(_last(tab.colours)), _last(tab.glyphs)) == ("run_partial", "!")
    assert tab._dashboard._well_tooltip(WELL) == (
        f"{WELL}: unfinished — the run ended before this well finished; it has no complete output "
        "(FOV 3/12 reached)")


def test_the_completion_headline_counts_the_partial_wells(tab):
    _drive(tab, [_PREFIX["I"] + "Zip mode: 2 wells",
                 _PREFIX["I"] + f"Well {WELL} — starting (pid 1)",
                 _PREFIX["W"] + f"Well {WELL}: no records produced; CSV not written.",
                 _PREFIX["I"] + f"Well {WELL} — done.",
                 _PREFIX["I"] + "Well A02 — starting (pid 1)", _PREFIX["I"] + "Well A02 — done."], remote=False)
    tab._on_run_done("Pipeline completed successfully.\n", [])
    assert tab._dashboard.headline_text() == (
        "Complete — 2 well(s) processed; 1 partial or unfinished (marked ! on the plate)")
