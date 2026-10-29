"""Analyze's *Skip these wells*: named wells are left out of a run, and their output is left as it is.

A skip is carried as the ``--only_wells`` subset that runs every other well (``pipeline.well_skip.skip_wells_scope``,
applied by ``PipelineRunner._apply_skip_wells`` once the input folder is resolved), so the pipeline, the remote
upload, the journal and the gating carry treat a skipped well exactly as they treat a well outside a subset — which
``tests/test_only_wells_run.py`` and ``tests/test_only_wells_remote.py`` already hold.

These hold the translation and the seams either side of it: the rule itself, the argv the runner really builds
when ``start`` is driven over a real input folder, and the form field the user types into.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline.well_skip import skip_wells_scope

PLATE = ["A01", "B02", "C03", "D04"]


# ── the rule ─────────────────────────────────────────────────────────────────

def test_a_skip_runs_every_other_well_of_the_plate():
    scope = skip_wells_scope(None, ["B02"], PLATE)
    assert scope.only_wells == ["A01", "C03", "D04"]
    assert scope.skipped == ["B02"]
    assert not scope.refusal


def test_wells_are_matched_however_they_are_spelled():
    assert skip_wells_scope(None, ["b2", "c3"], PLATE).only_wells == ["A01", "D04"]


def test_with_only_these_wells_the_skip_comes_out_of_that_subset():
    assert skip_wells_scope(["A1", "B2", "C3"], ["B02"], PLATE).only_wells == ["A01", "C03"]


def test_a_skip_wins_over_force():
    scope = skip_wells_scope(None, ["B02"], PLATE, force_wells=["B2", "C03"])
    assert scope.only_wells == ["A01", "C03", "D04"]
    assert scope.unforced == ["B02"]


def test_a_named_well_the_plate_lacks_and_a_typo_are_reported_not_refused():
    scope = skip_wells_scope(None, ["B02", "H12", "Z9"], PLATE)
    assert scope.only_wells == ["A01", "C03", "D04"]
    assert scope.absent == ["H12", "Z9"]
    assert not scope.refusal


def test_skipping_every_well_refuses_the_run():
    scope = skip_wells_scope(None, PLATE, PLATE)
    assert scope.only_wells is None
    assert "would process nothing" in scope.refusal
    assert "every well in the input" in scope.refusal
    subset = skip_wells_scope(["A01"], ["A1"], PLATE)
    assert "Only these wells" in subset.refusal


def test_no_skip_changes_nothing():
    assert skip_wells_scope(["A01"], [], PLATE).only_wells is None
    assert skip_wells_scope(None, None, PLATE).only_wells is None


# ── the runner, driven through start over a real input folder ────────────────

def _plate(tmp_path: Path) -> tuple[Path, Path]:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    for well in PLATE:
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for ch in ("DAPI", "GFP"):
                zf.writestr(f"e_{ch}_{well}_1_T01.tif", b"x")
    return inp, out


def _opts(inp: Path, **extra) -> dict:
    return {"raw": inp, "filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI", "csv_prefix": "measurements", **extra}


class _Done:
    """A pipeline child that has already exited cleanly, having said nothing."""

    pid = 0
    returncode = 0
    stdout = iter(())

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _start(tmp_path, monkeypatch, **extra) -> tuple[list[str] | None, str]:
    """``PipelineRunner.start`` as Analyze calls it; the argv the pipeline was spawned with, and the run log."""
    import services.pipeline_runner as pr

    inp, out = _plate(tmp_path)
    spawned: list[list[str]] = []

    def spawn(args):
        spawned.append(list(args))
        return _Done()

    monkeypatch.setattr(pr, "spawn_pipeline", spawn)
    r = pr.PipelineRunner()
    r.start(Path("process_microscopy.py"), _opts(inp, **extra),
            resolve_dirs=lambda opts, log_q, proc_hook=None: (inp, out))
    r._thread.join(timeout=60)
    assert not r._thread.is_alive()
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    text = "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))
    return (spawned[0] if spawned else None), text


def _flag_values(argv: list[str], flag: str) -> list[str] | None:
    if flag not in argv:
        return None
    i, values = argv.index(flag) + 1, []
    while i < len(argv) and not argv[i].startswith("--"):
        values.append(argv[i])
        i += 1
    return values


def test_the_run_is_spawned_over_every_well_but_the_skipped_ones(tmp_path, monkeypatch):
    argv, text = _start(tmp_path, monkeypatch, skip_wells=["B2"])
    assert argv is not None, text
    assert _flag_values(argv, "--only_wells") == ["A01", "C03", "D04"], argv
    assert "Skipping 1 well(s): B02" in text, text


def test_a_skipped_well_is_dropped_from_force_these_wells(tmp_path, monkeypatch):
    argv, text = _start(tmp_path, monkeypatch, skip_wells=["B02"], force_wells=["B02", "C03"])
    assert _flag_values(argv, "--force_wells") == ["C03"], argv
    assert "named in both Force these wells and Skip these wells: skipped" in text, text


def test_a_run_whose_skip_leaves_nothing_is_refused_before_it_spawns(tmp_path, monkeypatch):
    argv, text = _start(tmp_path, monkeypatch, skip_wells=list(PLATE))
    assert argv is None, argv
    assert "would process nothing" in text, text


def test_without_a_skip_the_whole_plate_runs(tmp_path, monkeypatch):
    argv, _text = _start(tmp_path, monkeypatch)
    assert argv is not None and "--only_wells" not in argv, argv


# ── the field ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_field_is_on_the_run_scope_section_and_reaches_the_run_options(qapp):
    from PySide6.QtWidgets import QWidget

    from analyze.well_scope import AnalyzeRunScopeMixin, build_well_scope_rows, well_scope_options

    rows: list[str] = []

    class _Form(AnalyzeRunScopeMixin):
        def _row(self, sec, label, tooltip=None):
            from PySide6.QtWidgets import QHBoxLayout
            row = QWidget(sec)
            QHBoxLayout(row)
            rows.append(label)
            return row

    form, sec = _Form(), QWidget()
    build_well_scope_rows(form, sec)
    assert "Skip these wells" in rows
    form._skip_wells_edit.setText("B2, c03")
    assert well_scope_options(form)["skip_wells"] == ["B2", "c03"]
