"""The AXES form itself: what it refuses, and what it says a run will do.

Two things shipped in the Batch Export panel that the rest of the batch tests
cannot see, because they live in the *form* rather than in the export:

**What the block refuses.** Three fields per row and no validator meant a
figure could be asked for a range it cannot have. ``min`` at or above ``max``
is a typo under columns headed ``min`` and ``max``, and matplotlib answers the
equal case by expanding the range to an interval of its own and drawing a frame
whose numbers nobody typed; a log axis bounded at zero has no position for that
bound, so matplotlib drops it and autoscales. Both produce a *file*, per group,
that looks like every other file in the folder — the shape of failure
``CLAUDE.md`` §7 exists to keep out.

**What the block promises.** "Each export group produces:" was a static string,
and by the time the panel could also write a per-cell CSV, a slide deck, a
gallery record and three of its four panels it was wrong in four ways at once.
It is derived now, which is only worth anything if it is derived from the same
reads the run makes — so these drive the real methods rather than asserting the
sentence exists.
"""
from __future__ import annotations

import os

import pytest

from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from review.features.batch_export._panels import (  # noqa: E402
    AxesSpec, Panel, PanelAxis,
)
from review.features.batch_export.bar_panel import (  # noqa: E402
    BarBatchExportPanel,
)
from review.features.batch_export.base_panel import (  # noqa: E402
    BatchExportPanel,
)
from review.features.batch_export.response_panel import (  # noqa: E402
    ResponseBatchExportPanel,
)
from review.features.batch_export.scatter_panel import (  # noqa: E402
    ScatterBatchExportPanel,
)

_MODES = (BatchExportPanel, BarBatchExportPanel, ScatterBatchExportPanel,
          ResponseBatchExportPanel)


class _FormStub:
    """A panel reduced to the AXES answers, with the real methods on it.

    The methods are *borrowed off the real class* rather than copied. A copy
    would go on passing after the real one changed, which is the failure this
    whole file is about.
    """

    _axes_complaints = BatchExportPanel._axes_complaints
    _x_row_label = BatchExportPanel._x_row_label
    _figure_summary_lines = BatchExportPanel._figure_summary_lines
    _summary_figure_lead = BatchExportPanel._summary_figure_lead
    _summary_figure_note = BatchExportPanel._summary_figure_note
    _summary_csv_line = BatchExportPanel._summary_csv_line
    _summary_footnotes = BatchExportPanel._summary_footnotes
    _wants_cell_rows = BatchExportPanel._wants_cell_rows
    _run_level_summary = BatchExportPanel._run_level_summary
    _ticked_destinations = BatchExportPanel._ticked_destinations
    _destinations = BatchExportPanel._destinations
    _wants_image_files = BatchExportPanel._wants_image_files
    _wants_deck = BatchExportPanel._wants_deck
    _wants_gallery = BatchExportPanel._wants_gallery
    _wants_disk_output = BatchExportPanel._wants_disk_output
    _out_path = BatchExportPanel._out_path
    _refresh_run_summary = BatchExportPanel._refresh_run_summary

    _PANELS = BatchExportPanel._PANELS
    _X_ROW_LABEL = BatchExportPanel._X_ROW_LABEL
    _Y_ROW_LABEL = BatchExportPanel._Y_ROW_LABEL
    _SUMMARY_FIGURE_LEAD = BatchExportPanel._SUMMARY_FIGURE_LEAD
    _SUMMARY_FIGURE_NOTE = BatchExportPanel._SUMMARY_FIGURE_NOTE
    _SUMMARY_CSV_LINE = BatchExportPanel._SUMMARY_CSV_LINE

    def __init__(self, spec, *, panels=None, cells=False, gallery=False,
                 deck=False, sidebar=False, files=True):
        self._spec = spec
        self._to_files_cb = _Box(files)
        if panels is not None:
            self._PANELS = panels
        self._cell_rows_cb = _Box(cells)
        # ``None`` where the build has no Saved Plots: that is the shape the
        # real ``_build_write_to_row`` produces there, and a stub that always
        # made two checkboxes could never take it.
        self._to_gallery_cb = None if gallery is None else _Box(gallery)
        self._to_deck_cb = None if deck is None else _Box(deck)
        self._use_sidebar_groups = sidebar
        self._summary_lbl = _Label()

    def _snapshot_axes(self):
        return self._spec


class _Box:
    def __init__(self, on):
        self._on = on

    def isChecked(self):
        return self._on


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


def _spec(panels=BatchExportPanel._PANELS, **kw):
    kw.setdefault("panels", tuple((p.key, PanelAxis()) for p in panels))
    return AxesSpec(**kw)


# ── what the block refuses ──────────────────────────────────────────────────

def test_a_reversed_range_is_refused_by_the_row_that_holds_it():
    spec = _spec(x_min=48.0, x_max=0.0)
    complaints = _FormStub(spec)._axes_complaints()
    assert len(complaints) == 1
    assert complaints[0] == "X (time, h): min (48) is not below max (0)."


def test_an_equal_pair_is_refused_too():
    """The case matplotlib answers by inventing a range of its own.

    Asserted separately from the reversed case because ``>`` rather than
    ``>=`` passes the test above and leaves this one live.
    """
    complaints = _FormStub(_spec(x_min=10.0, x_max=10.0))._axes_complaints()
    assert complaints == ["X (time, h): min (10) is not below max (10)."]


def test_a_well_formed_range_draws_no_complaint():
    assert _FormStub(_spec(x_min=0.0, x_max=48.0))._axes_complaints() == []
    # And one bound alone is a range, not half a mistake.
    assert _FormStub(_spec(x_max=48.0))._axes_complaints() == []
    assert _FormStub(_spec())._axes_complaints() == []


def test_a_log_axis_bounded_at_zero_is_refused():
    spec = _spec(x_min=0.0, x_max=48.0, x_log=True)
    complaints = _FormStub(spec)._axes_complaints()
    assert len(complaints) == 1
    assert complaints[0].startswith("X (time, h): a log scale has no position "
                                    "for 0")


def test_a_log_axis_bounded_above_zero_is_fine():
    assert _FormStub(_spec(x_min=1.0, x_max=48.0,
                           x_log=True))._axes_complaints() == []
    # And the same bound on a linear axis is not a complaint either: it is
    # the *scale* that makes zero unrepresentable, not the number.
    assert _FormStub(_spec(x_min=0.0, x_max=48.0,
                           x_log=False))._axes_complaints() == []


def test_a_panel_row_is_named_by_its_own_panel():
    panels = tuple((p.key, PanelAxis(y_min=5.0, y_max=1.0))
                   for p in BatchExportPanel._PANELS if p.key == "frac")
    spec = AxesSpec(panels=panels)
    complaints = _FormStub(spec)._axes_complaints()
    assert complaints == [
        "Fraction above cutoff: min (5) is not below max (1)."]


def test_a_single_panel_mode_names_its_row_y_rather_than_the_panel():
    """"Plot: min…" would name a thing the form does not say out loud.

    The single-panel modes label the row from ``_Y_ROW_LABEL``, and a
    complaint that used ``panel.label`` instead would name a word ("Plot")
    that appears nowhere on the screen.
    """
    stub = _FormStub(AxesSpec(panels=(("plot", PanelAxis(y_min=2.0,
                                                         y_max=1.0)),)),
                     panels=(Panel("plot", "Plot"),))
    assert stub._axes_complaints() == ["Y: min (2) is not below max (1)."]


def test_an_unticked_panel_s_fields_are_not_refused():
    """It is not drawn, so its numbers describe nothing.

    Refusing over them would block a run on a value that has no effect on any
    file it writes.
    """
    panels = (("mean", PanelAxis(included=True)),
              ("frac", PanelAxis(included=False, y_min=5.0, y_max=1.0)))
    assert _FormStub(AxesSpec(panels=panels))._axes_complaints() == []


def test_every_bad_row_is_reported_in_one_pass():
    panels = (("mean", PanelAxis(y_min=9.0, y_max=1.0)),
              ("frac", PanelAxis(y_min=0.0, y_max=1.0, y_log=True)))
    complaints = _FormStub(AxesSpec(x_min=5.0, x_max=1.0,
                                    panels=panels))._axes_complaints()
    assert len(complaints) == 3
    assert complaints[0].startswith("X (time, h):")
    assert complaints[1].startswith("Value:")
    assert complaints[2].startswith("Fraction above cutoff:")


# ── what the block promises ─────────────────────────────────────────────────

def test_the_summary_names_the_panels_a_partial_run_will_draw():
    # A *middle* panel, deliberately. Dropping the last one leaves the kept
    # keys in ``_PANELS`` order either way, so a summary that listed the
    # declared panels rather than the drawn ones would produce the same
    # string — the fixture has to make the right and wrong answers differ
    # before any assertion over it can measure anything (CLAUDE.md §7.8).
    panels = tuple((p.key, PanelAxis(included=(p.key != "frac")))
                   for p in BatchExportPanel._PANELS)
    stub = _FormStub(AxesSpec(panels=panels))
    stub._refresh_run_summary()
    assert ("One figure — 3 of 4 panels (Value, N above cutoff, CDF)"
            in stub._summary_lbl.text), stub._summary_lbl.text
    # And not the "all N panels" wording, which is the answer the
    # undropped run gives and would satisfy a looser assertion.
    assert "all 4 panels" not in stub._summary_lbl.text
    assert "Fraction above cutoff" not in stub._summary_lbl.text


def test_a_run_that_keeps_every_panel_says_so_rather_than_listing_them():
    stub = _FormStub(_spec())
    stub._refresh_run_summary()
    assert "One figure — all 4 panels" in stub._summary_lbl.text


def test_the_per_cell_csv_bullet_appears_only_when_it_is_asked_for():
    off = _FormStub(_spec())
    off._refresh_run_summary()
    assert "raw per-cell CSV" not in off._summary_lbl.text
    on = _FormStub(_spec(), cells=True)
    on._refresh_run_summary()
    assert "One raw per-cell CSV" in on._summary_lbl.text


def test_the_two_run_level_outputs_are_named_only_when_they_are_ticked():
    none = _FormStub(_spec())
    none._refresh_run_summary()
    assert "The run also" not in none._summary_lbl.text

    gallery = _FormStub(_spec(), gallery=True)
    gallery._refresh_run_summary()
    assert ("The run also files a Saved Plots record per figure."
            in gallery._summary_lbl.text)
    assert "PowerPoint" not in gallery._summary_lbl.text

    deck = _FormStub(_spec(), deck=True)
    deck._refresh_run_summary()
    assert ("The run also writes one PowerPoint deck into this folder, a "
            "slide per group." in deck._summary_lbl.text)
    assert "Saved Plots" not in deck._summary_lbl.text


def test_the_deck_and_the_gallery_are_told_apart_by_their_verbs():
    """"Writes" is the output folder; "files" is the application.

    That is the whole distinction the two rows exist to draw — the deck is a
    file beside the figures, subject to the same folder, the same staging and
    the same stale-file warning, and the gallery record is not. A summary that
    ran them together under one verb would undo the row split it describes.
    """
    both = _FormStub(_spec(), gallery=True, deck=True)
    both._refresh_run_summary()
    text = both._summary_lbl.text
    assert ("The run also writes one PowerPoint deck into this folder, a "
            "slide per group, and files a Saved Plots record per figure."
            in text)
    # And neither is a bullet under "Each export group produces:", because
    # forty groups produce one deck between them.
    bullets = [ln for ln in text.splitlines() if ln.startswith("  \u2022")]
    assert not any("PowerPoint" in ln or "Saved Plots" in ln
                   for ln in bullets), bullets


def test_the_summary_says_where_groups_come_from():
    inline = _FormStub(_spec())
    inline._refresh_run_summary()
    assert "Groups are defined in the left panel." in inline._summary_lbl.text
    sidebar = _FormStub(_spec(), sidebar=True)
    sidebar._refresh_run_summary()
    assert ("Groups come from Home → Sample Map → Replicates."
            in sidebar._summary_lbl.text)


def test_the_bar_mode_s_figure_count_follows_its_span_toggle():
    """The one mode whose *number of files per group* is a control.

    With the toggle off a group of four timepoints comes out as four files,
    and the old static text said "One bar figure per timepoint" either way.
    """
    stub = _FormStub(_spec(BarBatchExportPanel._PANELS),
                     panels=BarBatchExportPanel._PANELS)
    stub._summary_figure_lead = BarBatchExportPanel._summary_figure_lead.__get__(
        stub)
    stub._multi_tp_cb = _Box(False)
    assert stub._summary_figure_lead() == "One figure per selected timepoint"
    stub._multi_tp_cb = _Box(True)
    assert (stub._summary_figure_lead()
            == "One figure spanning every selected timepoint")


def test_the_scatter_mode_s_figure_count_follows_its_split_toggle():
    stub = _FormStub(AxesSpec(panels=(("plot", PanelAxis()),)),
                     panels=ScatterBatchExportPanel._PANELS)
    lead = ScatterBatchExportPanel._summary_figure_lead.__get__(stub)
    stub._scatter_split_tp_cb = _Box(True)
    assert lead() == "One scatter figure per selected timepoint"
    stub._scatter_split_tp_cb = _Box(False)
    assert lead() == "One scatter figure spanning every selected timepoint"


# ── what each mode declares ─────────────────────────────────────────────────

def test_only_the_categorical_mode_hides_the_x_row():
    """A bar figure's x is one position per member, not a number.

    ``xlim(0, 48)`` over three bars crushes them into the left of every frame
    in the run, so the row is not offered rather than offered and ignored.
    """
    assert BarBatchExportPanel._X_IS_NUMERIC is False
    for cls in (BatchExportPanel, ScatterBatchExportPanel,
                ResponseBatchExportPanel):
        assert cls._X_IS_NUMERIC is True, cls.__name__


def test_only_the_scatter_modes_decline_the_scale_column():
    """They own a ``log10(v + 1)`` of their own, which keeps cells at zero.

    Two controls for one axis is worse than either, and the one to keep is the
    one that is not lossy.
    """
    assert ScatterBatchExportPanel._AXES_SCALE_OFFERED is False
    for cls in (BatchExportPanel, BarBatchExportPanel,
                ResponseBatchExportPanel):
        assert cls._AXES_SCALE_OFFERED is True, cls.__name__


def test_the_modes_whose_axes_are_a_choice_place_the_block_themselves():
    """Scatter and Response pick what their axes *are* before bounding them.

    Above those rows the block asked for a range on an axis the user had not
    chosen, and Scatter carried two rows labelled "X:" meaning different
    things.
    """
    for cls in (ScatterBatchExportPanel, ResponseBatchExportPanel):
        assert cls._AXES_FOLLOW_HEADER is False, cls.__name__
        source = cls._build_output_panel.__code__.co_names
        assert "_build_output_tail" in source, cls.__name__
    for cls in (BatchExportPanel, BarBatchExportPanel):
        assert cls._AXES_FOLLOW_HEADER is True, cls.__name__


def test_every_mode_names_its_own_x_axis():
    """"X:" over a row of numbers says nothing about what unit to type."""
    labels = {cls: cls._X_ROW_LABEL for cls in _MODES}
    assert labels[BatchExportPanel] == "X (time, h):"
    assert labels[ResponseBatchExportPanel] == "X (dose):"
    assert labels[ScatterBatchExportPanel] == "X range:"
    # And no two modes with different x axes share a label by inheritance.
    assert len({labels[BatchExportPanel], labels[ResponseBatchExportPanel],
                labels[ScatterBatchExportPanel]}) == 3


@pytest.mark.parametrize("cls", _MODES)
def test_every_mode_can_answer_the_summary_from_its_own_declarations(cls):
    """A mode that inherits the line stack's words describes another figure.

    Cheap to get wrong silently: the base's note names "one line per group
    member", which is not what a bar or a dose-response figure draws.
    """
    assert cls._SUMMARY_CSV_LINE
    if cls is not BatchExportPanel:
        # Declared on the mode itself, not merely truthy: inherited, all three
        # promise "per-member time-series data" for a bar, a scatter and a
        # dose-response CSV, and `assert cls._SUMMARY_CSV_LINE` is happy with
        # every one of them.
        assert "_SUMMARY_CSV_LINE" in cls.__dict__, cls.__name__
        assert (cls._SUMMARY_FIGURE_NOTE
                != BatchExportPanel._SUMMARY_FIGURE_NOTE), cls.__name__


# ── the refusal reaches the user, and the column fits its labels ────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _RefusalStub(_FormStub):
    """Enough of a panel for the pre-run guard, which needs a dialog parent."""

    _axes_block_refuses_the_run = BatchExportPanel._axes_block_refuses_the_run
    _run_is_refused = BatchExportPanel._run_is_refused


def test_a_run_asking_for_an_impossible_range_is_refused_before_any_job(
        monkeypatch):
    """Asserted on the guard's answer *and* on the dialog it raised.

    The guard returning True is what stops the run; the dialog is what tells
    the user which row to fix. An implementation that refused silently would
    satisfy the first half alone, and a user would press Run and watch nothing
    happen.
    """
    from review.features.batch_export import base_panel as mod

    seen = []
    monkeypatch.setattr(mod.QMessageBox, "warning",
                        lambda *a, **k: seen.append(a))
    stub = _RefusalStub(_spec(x_min=48.0, x_max=0.0))
    assert stub._axes_block_refuses_the_run() is True
    assert len(seen) == 1
    assert seen[0][1] == "Axis limits"
    assert "X (time, h): min (48) is not below max (0)." in seen[0][2]


def test_a_run_with_a_sound_axes_block_is_not_refused(monkeypatch):
    from review.features.batch_export import base_panel as mod

    seen = []
    monkeypatch.setattr(mod.QMessageBox, "warning",
                        lambda *a, **k: seen.append(a))
    stub = _RefusalStub(_spec(x_min=0.0, x_max=48.0))
    assert stub._axes_block_refuses_the_run() is False
    assert seen == []


def test_an_empty_selection_is_still_refused_by_its_own_message(monkeypatch):
    """The two refusals say different things, and both still fire.

    Folding the limit check in beside the panel check is exactly where one of
    them gets lost.
    """
    from review.features.batch_export import base_panel as mod

    seen = []
    monkeypatch.setattr(mod.QMessageBox, "warning",
                        lambda *a, **k: seen.append(a))
    panels = tuple((p.key, PanelAxis(included=False))
                   for p in BatchExportPanel._PANELS)
    stub = _RefusalStub(AxesSpec(panels=panels))
    assert stub._axes_block_refuses_the_run() is True
    assert seen[0][1] == "No panels"


def test_the_axes_label_column_is_wide_enough_for_the_names_in_it(qapp):
    """Measured, because at a guessed 108 px the line stack clipped two rows.

    Qt clips a fixed-width label silently, so "Fraction above cutoff" rendered
    as "Fraction abov" and read as a name rather than as a truncation.
    """
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    try:
        for cls in _MODES:
            host._PANELS = cls._PANELS
            width = BatchExportPanel._axes_label_w(host)
            metrics = host.fontMetrics()
            widest = max(metrics.horizontalAdvance(p.label)
                         for p in cls._PANELS)
            if len(cls._PANELS) > 1:
                # A stacked mode's label sits beside a checkbox indicator.
                assert width > widest, cls.__name__
            assert width >= widest, cls.__name__
            # And an upper bound, because ">= widest" alone is satisfied by
            # any number: a column nine thousand pixels wide clips nothing
            # and pushes every field off the screen instead.
            assert width <= BatchExportPanel._axes_label_w_ceiling(host), \
                cls.__name__
    finally:
        host.deleteLater()


# ── image files are one destination, not the base every run pays for ───────

def test_a_run_with_no_destination_is_refused(monkeypatch):
    """Nothing ticked produces nothing, so it is stopped before the first job.

    Its own message, separate from the AXES refusals: "every panel is
    unticked" and "no destination is ticked" send the user to two different
    rows.
    """
    from review.features.batch_export import base_panel as mod

    seen = []
    monkeypatch.setattr(mod.QMessageBox, "warning",
                        lambda *a, **k: seen.append(a))
    stub = _RefusalStub(_spec(), files=False)
    assert stub._run_is_refused() is True
    assert seen[0][1] == "No destination"


def test_one_destination_is_enough_whichever_it_is(monkeypatch):
    """None of the three is privileged, so any one of them clears the check."""
    from review.features.batch_export import base_panel as mod

    seen = []
    monkeypatch.setattr(mod.QMessageBox, "warning",
                        lambda *a, **k: seen.append(a))
    for kw in ({"files": True}, {"files": False, "deck": True},
               {"files": False, "gallery": True}):
        stub = _RefusalStub(_spec(), **kw)
        assert stub._run_is_refused() is False, kw
    assert seen == []


def test_no_output_path_is_built_for_a_run_that_writes_no_files(tmp_path):
    """``None`` rather than a path somewhere harmless.

    A run told to write no files must not create one — not even a temporary
    one it deletes afterwards — so every caller gets ``None`` and skips its
    write.
    """
    off = _FormStub(_spec(), files=False)
    assert off._out_path(tmp_path, "batch_Drug_A.csv") is None
    on = _FormStub(_spec(), files=True)
    assert on._out_path(tmp_path, "batch_Drug_A.csv") == \
        tmp_path / "batch_Drug_A.csv"


def test_the_per_cell_csv_follows_the_folder_it_would_sit_in():
    """It is a file in that folder, so it cannot outlive the folder's absence.

    Asserted on ``_wants_cell_rows`` rather than on the checkbox's enabled
    state: greying the control is the cue, but the run has to agree with the
    cue, and a run that wrote the file anyway would put it in a directory the
    user asked to be left alone.
    """
    on = _FormStub(_spec(), cells=True, files=True)
    assert on._wants_cell_rows() is True
    off = _FormStub(_spec(), cells=True, files=False)
    assert off._wants_cell_rows() is False


def test_only_a_gallery_only_run_skips_the_output_folder_entirely():
    """The deck is a file, so it still needs somewhere to go."""
    assert _FormStub(_spec(), files=False, gallery=True)._wants_disk_output() \
        is False
    assert _FormStub(_spec(), files=False, deck=True)._wants_disk_output() \
        is True
    assert _FormStub(_spec(), files=True)._wants_disk_output() is True


def test_a_run_that_saves_no_numbers_says_so_before_it_is_started():
    """The one shortfall a user chooses — and still a shortfall.

    An absence is what nobody notices afterwards, so it is stated in the block
    above the Run button rather than left to be discovered in the folder.
    """
    stub = _FormStub(_spec(), files=False, gallery=True)
    stub._refresh_run_summary()
    text = stub._summary_lbl.text
    assert ("No image files, and no CSVs: the numbers behind each figure are "
            "not saved anywhere." in text)
    # And the per-group bullets are gone, rather than describing files that
    # will not exist.
    assert "Each export group produces:" not in text
    assert "One CSV with per-member time-series data" not in text
    # A gallery-only run is the whole of what it does, so not "also".
    assert "The run files a Saved Plots record per figure." in text


def test_the_bullets_come_back_with_the_image_files():
    stub = _FormStub(_spec(), files=True, gallery=True)
    stub._refresh_run_summary()
    text = stub._summary_lbl.text
    assert "Each export group produces:" in text
    assert "not saved anywhere" not in text
    assert "The run also files a Saved Plots record per figure." in text


# ── the folder is created only by a run that writes into it ────────────────

class _OutDirStub(_FormStub):
    """``_resolve_out_dir`` off the real class, with a real line edit."""

    _resolve_out_dir = BatchExportPanel._resolve_out_dir

    def __init__(self, text, data_dir, **kw):
        from PySide6.QtWidgets import QLineEdit

        super().__init__(_spec(), **kw)
        self._out_dir_edit = QLineEdit(str(text))
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(dataset=SimpleNamespace(data_dir=data_dir)))


def test_a_gallery_only_run_does_not_create_the_folder_it_will_not_use(
        qapp, tmp_path):
    """The creation happened before the runner ever asked the question.

    ``_resolve_out_dir`` is called by every mode's ``_run_batch`` *before*
    ``_run_batch_jobs``, and it used to ``mkdir(parents=True)``
    unconditionally — so a gallery-only run made two levels of empty
    directory named for an export that never touched them. The runner's own
    care not to create it was measured one step past this call, which is
    exactly the seam CLAUDE.md §7.6 warns about.
    """
    target = tmp_path / "exports" / "run7"
    stub = _OutDirStub(target, tmp_path, files=False, gallery=True)
    assert stub._resolve_out_dir() == target
    assert not target.exists()
    assert not target.parent.exists()


def test_a_run_that_writes_files_still_creates_its_folder(qapp, tmp_path):
    target = tmp_path / "exports" / "run7"
    stub = _OutDirStub(target, tmp_path, files=True)
    assert stub._resolve_out_dir() == target
    assert target.is_dir()


def test_a_deck_only_run_creates_the_folder_because_the_deck_goes_in_it(
        qapp, tmp_path):
    target = tmp_path / "exports" / "deck"
    stub = _OutDirStub(target, tmp_path, files=False, deck=True)
    assert stub._resolve_out_dir() == target
    assert target.is_dir()


# ── the greying is wired, not merely written ───────────────────────────────

class _SyncStub(_FormStub):
    """``_sync_destination_widgets`` with the real widgets it greys.

    Real ``QCheckBox`` / ``QComboBox`` / ``QLineEdit`` rather than fakes,
    because ``setEnabled`` is the thing under test and a fake that records the
    call would pass against a method that called it on the wrong widget.
    """

    _sync_destination_widgets = BatchExportPanel._sync_destination_widgets
    _set_destination_row_enabled = BatchExportPanel._set_destination_row_enabled
    _enabled_when_idle = BatchExportPanel._enabled_when_idle

    def __init__(self, **kw):
        from PySide6.QtWidgets import (
            QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton,
        )

        super().__init__(_spec(), **kw)
        self._to_files_cb = QCheckBox()
        self._to_files_cb.setChecked(kw.get("files", True))
        self._to_deck_cb = QCheckBox()
        self._to_deck_cb.setChecked(kw.get("deck", False))
        self._to_gallery_cb = QCheckBox()
        self._to_gallery_cb.setChecked(kw.get("gallery", False))
        self._cell_rows_cb = QCheckBox()
        self._fmt_cb = QComboBox()
        self._fmt_hint = QLabel()
        self._out_dir_edit = QLineEdit()
        self._out_dir_browse_btn = QPushButton()
        self._summary_lbl = _Label()


def test_format_is_greyed_when_no_image_files_are_written(qapp):
    """Four answers to a question this run does not ask."""
    stub = _SyncStub(files=False, gallery=True)
    stub._sync_destination_widgets()
    assert not stub._fmt_cb.isEnabled()
    assert not stub._fmt_hint.isEnabled()
    stub._to_files_cb.setChecked(True)
    stub._sync_destination_widgets()
    assert stub._fmt_cb.isEnabled(), "and it comes back"


def test_the_folder_is_greyed_only_when_nothing_lands_in_it(qapp):
    """The deck is a file, so it keeps the folder alive."""
    gallery_only = _SyncStub(files=False, gallery=True)
    gallery_only._sync_destination_widgets()
    assert not gallery_only._out_dir_edit.isEnabled()
    assert not gallery_only._out_dir_browse_btn.isEnabled()

    with_deck = _SyncStub(files=False, deck=True)
    with_deck._sync_destination_widgets()
    assert with_deck._out_dir_edit.isEnabled()
    assert with_deck._out_dir_browse_btn.isEnabled()


def test_the_per_cell_checkbox_is_greyed_with_the_folder_it_writes_into(qapp):
    stub = _SyncStub(files=False, gallery=True)
    stub._sync_destination_widgets()
    assert not stub._cell_rows_cb.isEnabled()
    stub._to_files_cb.setChecked(True)
    stub._sync_destination_widgets()
    assert stub._cell_rows_cb.isEnabled()


def test_re_enabling_after_a_run_does_not_hand_back_a_greyed_control(qapp):
    """The row is locked for a run and unlocked after it.

    Unlocking must not undo the greying that ``_sync_destination_widgets``
    applied for a reason that still holds — the per-cell checkbox belongs to
    a folder this run is still not writing.
    """
    stub = _SyncStub(files=False, gallery=True)
    stub._sync_destination_widgets()
    stub._set_destination_row_enabled(False)
    assert not stub._to_files_cb.isEnabled()
    stub._set_destination_row_enabled(True)
    assert stub._to_files_cb.isEnabled()
    assert stub._to_gallery_cb.isEnabled()
    assert not stub._cell_rows_cb.isEnabled(), \
        "still no folder for it to write into"
