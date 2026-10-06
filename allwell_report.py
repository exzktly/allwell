"""The headless report: draw a dataset's saved plots without showing a window.

    python allwell_report.py --dataset <folder> --output <folder> [--no-pdf]

(in the frozen app: the executable with ``--run-report`` first, dispatched by
``all_well_launcher``). Writes ``figures/NN_<title>.png`` + ``.svg`` for every
saved plot it can draw, ``report.pdf``, and ``manifest.json`` listing every
saved plot with what became of it, and exits with a code a script can branch on.

It is an entry point beside ``allwell.py`` rather than a module inside
``review/`` because it does what only the shell does: it constructs the Review
pane. A module inside ``review/`` may not import ``ReviewApp``
(``tests/test_architecture_contracts.py::test_nothing_imports_reviewapp_at_runtime``),
and the launcher may not reach into a pane for its dispatch.

**The real app, offscreen.** Measured before choosing this route:
constructing ``ReviewApp`` under the offscreen platform and loading the mini
dataset takes about 7 s and 400 MB, shows no window, and raises no modal — and
it draws every plot exactly as the app does, because it *is* the app. Each saved
plot is replayed from its parameters on the dataset's current data through the
``saved_plots.render`` service, the same replay the PowerPoint export's repair
uses; the PDF is the gallery's own builder (``saved_plots.report_pdf``). Every
feature is reached through a service, so a build without Saved Plots gets an
exit code and a sentence rather than an ``ImportError``.

**A plot that is not drawn is a manifest entry with the reason**, never an
absence, so a reader of the output folder can tell a gallery of three from a
gallery of five with two failures.

**The dataset is not changed.** The whole run sits inside a write suspension
(``review.persistence._io.writes_suspended``, reached through
``ctx.persistence``) over the dataset folder and, when the sidecar sits one
level above it, that file (:func:`suspended_roots`). A suspended write raises
``WritesSuspended`` — an ``OSError`` — so a caller that would otherwise go on
to delete what it thinks it has just rewritten (the legacy-sidecar migration)
treats it as the failure it is. Pending debounced saves are flushed inside the
suspension, whatever happens. Measured, replays that re-adopt a plot's groups
or move its gating schedule schedule no save at all, so this is defence in
depth; the count is in the manifest. The one write to the dataset is a
``report_generated`` line in its provenance log — history, not state. Each run
first clears what an earlier run of this command left in ``--output``.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from util.json_write import write_json_atomic

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

#: Exit codes, one per outcome a script or scheduler needs to branch on.
EXIT_COMPLETE = 0        # every saved plot was drawn
EXIT_FAILED = 1          # nothing was drawn, or the run itself failed
EXIT_INVALID_INPUT = 2   # no dataset there, or this build has no Saved Plots
EXIT_PARTIAL = 3         # some plots drawn, some not — each named in the manifest
EXIT_NOTHING_TO_DO = 4   # the dataset has no saved plots

STATUS_FOR_EXIT = {
    EXIT_COMPLETE: "complete",
    EXIT_FAILED: "failed",
    EXIT_INVALID_INPUT: "invalid_input",
    EXIT_PARTIAL: "partial",
    EXIT_NOTHING_TO_DO: "nothing_to_report",
}


@dataclass
class PlotEntry:
    id: str
    title: str
    tab: str
    status: str                 # "drawn" | "not_drawn"
    reason: str = ""            # why it was not drawn
    note: str = ""              # what is different about a drawn one
    png: str = ""               # relative to the output folder
    svg: str = ""
    copied_from: str = ""


@dataclass
class Manifest:
    dataset: str
    output: str
    generated_at: str
    app_version: str | None
    plots: list[PlotEntry] = field(default_factory=list)
    pdf: str = ""
    message: str = ""
    writes_suppressed: int = 0
    #: ``None`` until decided. The runner sets it outright for a run that never
    #: reached the plots (invalid input, a crash); otherwise :meth:`finish`
    #: derives it from the entries.
    exit_code: int | None = None

    def finish(self) -> int:
        """Decide :attr:`exit_code` from the entries, unless already decided."""
        if self.exit_code is not None:
            return self.exit_code
        drawn = sum(1 for p in self.plots if p.status == "drawn")
        if not self.plots:
            self.exit_code = EXIT_NOTHING_TO_DO
        elif drawn == len(self.plots):
            self.exit_code = EXIT_COMPLETE
        elif drawn:
            self.exit_code = EXIT_PARTIAL
        else:
            self.exit_code = EXIT_FAILED
        return self.exit_code

    def to_json(self) -> dict:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        out["status"] = STATUS_FOR_EXIT.get(self.finish(), "failed")
        out["drawn"] = sum(1 for p in self.plots if p.status == "drawn")
        out["not_drawn"] = sum(1 for p in self.plots if p.status != "drawn")
        return out


_logger = logging.getLogger("review")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:48] or "plot"


def _app_version() -> str | None:
    try:
        from version import __version__
    except ImportError:
        return None
    return str(__version__)


def suspended_roots(dataset: Path) -> list[Path]:
    """Everything a report must not write: the dataset folder, and its sidecar
    when that sits one level above the folder given.

    ``--dataset`` may be the results folder itself, and for the Analyze layout
    ``pipeline_info.json`` then sits in its parent, outside the folder — where
    every definitions, gating and variables write lands. The file alone is
    suspended there, not the parent folder, which may hold other datasets.
    The rule is the loader's (``review.domain.viewer_state.resolve_pipeline_info_path``,
    applied to ``out/`` when the folder has one), restated here because this
    entry point may not import the pane's internals.
    """
    dataset = Path(dataset)
    data_dir = dataset / "out" if (dataset / "out").is_dir() else dataset
    roots = [dataset]
    sidecar = data_dir.parent / "pipeline_info.json"
    if not (data_dir / "pipeline_info.json").exists() and sidecar.exists() \
            and data_dir.parent.resolve() != dataset.resolve():
        roots.append(sidecar)
    return roots


def clear_previous_output(out_dir: Path) -> None:
    """Remove what an earlier run of this command left in *out_dir*.

    Only the files this command writes — ``figures/NN_*.png`` / ``.svg``,
    ``report.pdf``, ``manifest.json`` — and never anything else in the folder.
    Without it, a run that draws fewer plots, or none, leaves the previous
    run's figures and PDF beside a manifest that does not list them: an old
    report reading as a current one.
    """
    figures = out_dir / "figures"
    if figures.is_dir():
        for path in figures.iterdir():
            if path.suffix in (".png", ".svg") and re.match(r"\d{2,}_", path.name):
                path.unlink()
    for name in ("report.pdf", MANIFEST_FILENAME):
        (out_dir / name).unlink(missing_ok=True)


def _write(manifest: Manifest, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest.finish()
    write_json_atomic(out_dir / MANIFEST_FILENAME, manifest.to_json())


def _draw_all(ctx, manifest: Manifest, out_dir: Path) -> list:
    """Draw every saved plot into *out_dir*; return the records drawn."""
    records = ctx.features.query("saved_plots.gallery", ctx, default=None)
    drawn = []
    figures = out_dir / "figures"
    for n, rec in enumerate(records or [], start=1):
        entry = PlotEntry(id=rec.id, title=rec.title or rec.tab, tab=rec.tab,
                          status="not_drawn",
                          copied_from=getattr(rec, "copied_from", "") or "")
        result = ctx.features.query("saved_plots.render", ctx, rec, default=None)
        if result is None or not result.png:
            entry.reason = (result.reason if result is not None
                            else "Saved Plots is not part of this build")
        else:
            figures.mkdir(parents=True, exist_ok=True)
            stem = f"{n:02d}_{_slug(entry.title)}"
            (figures / f"{stem}.png").write_bytes(result.png)
            entry.png = f"figures/{stem}.png"
            if result.svg:
                (figures / f"{stem}.svg").write_bytes(result.svg)
                entry.svg = f"figures/{stem}.svg"
            entry.status = "drawn"
            entry.note = getattr(result, "note", "") or ""
            drawn.append((rec, out_dir / entry.png))
        manifest.plots.append(entry)
    return drawn


def _write_pdf(ctx, drawn, manifest: Manifest, out_dir: Path) -> None:
    if not drawn:
        return
    paths = {id(rec): path for rec, path in drawn}
    built = ctx.features.query(
        "saved_plots.report_pdf", [rec for rec, _p in drawn],
        lambda r: paths.get(id(r)), out_dir / "report.pdf",
        dataset_name=Path(manifest.dataset).name,
        summary_for=lambda r: r.summary, default=None)
    if built is not None:
        manifest.pdf = "report.pdf"


def run_report(dataset: Path | str, out_dir: Path | str, *, pdf: bool = True) -> Manifest:
    """Draw *dataset*'s saved plots into *out_dir*; write and return the manifest."""
    dataset = Path(dataset)
    out_dir = Path(out_dir)
    manifest = Manifest(dataset=str(dataset), output=str(out_dir),
                        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        app_version=_app_version())
    clear_previous_output(out_dir)
    if not dataset.is_dir():
        manifest.exit_code = EXIT_INVALID_INPUT
        manifest.message = f"{dataset} is not a folder"
        _write(manifest, out_dir)
        return manifest

    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    pers = None
    try:
        from review.runtime_app import ReviewApp
        app = ReviewApp()
        ctx = app._ctx
        pers = ctx.persistence
        before = pers.suppressed_writes()
        with pers.writes_suspended(*suspended_roots(dataset)):
            try:
                app._load_path(dataset)
                if not ctx.state.dataset.well_paths:
                    manifest.exit_code = EXIT_INVALID_INPUT
                    manifest.message = f"no wells could be loaded from {dataset}"
                elif ctx.features.get("Saved Plots") is None:
                    manifest.exit_code = EXIT_INVALID_INPUT
                    manifest.message = "Saved Plots is not part of this build"
                else:
                    drawn = _draw_all(ctx, manifest, out_dir)
                    if pdf:
                        _write_pdf(ctx, drawn, manifest, out_dir)
            finally:
                # Inside the suspension whatever happened above: a save still
                # queued on the background writer must meet the suspension,
                # not run after it lifts.
                pers.flush_all()
        manifest.writes_suppressed = pers.suppressed_writes() - before
    except Exception as exc:
        _logger.exception("headless report failed")
        manifest.exit_code = EXIT_FAILED
        manifest.message = f"the report run failed: {exc}"
    _write(manifest, out_dir)
    if pers is not None:
        summary = manifest.to_json()
        # History, not state: the one write the report makes to the dataset.
        pers.record_event("report_generated", source="report", detail={
            "output": str(out_dir), "status": summary["status"],
            "drawn": summary["drawn"], "not_drawn": summary["not_drawn"]})
    return manifest


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python allwell_report.py",
        description="Draw every saved plot of a dataset from its current data, "
                    "without opening a window, and write the figures, a PDF and "
                    "a manifest. The dataset is not changed.")
    ap.add_argument("--dataset", type=Path, required=True,
                    help="the dataset folder (the one holding in/ and out/, or "
                         "the results folder itself)")
    ap.add_argument("--output", type=Path, required=True,
                    help="where to write figures/, report.pdf and manifest.json")
    ap.add_argument("--no-pdf", action="store_true",
                    help="write the figures and the manifest only")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Before anything imports Qt: the report never shows a window.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    manifest = run_report(args.dataset, args.output, pdf=not args.no_pdf)
    data = manifest.to_json()
    print(f"{data['status']}: {data['drawn']} drawn, {data['not_drawn']} not drawn"
          + (f" — {manifest.message}" if manifest.message else "")
          + f". Manifest: {Path(args.output) / 'manifest.json'}")
    return manifest.exit_code


if __name__ == "__main__":
    sys.exit(main())
