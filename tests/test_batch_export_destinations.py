"""A batch figure reaches the gallery and the deck describing its own group.

Batch Export can now send each figure it renders to two destinations besides
the file it always wrote: the Saved Plots gallery, and a PowerPoint deck of
editable shapes. Both are fed from one capture point — ``_save_figure``, the
single place any panel writes a figure — and the whole risk of the feature is
concentrated there:

* a call site that forgets to name its group files a record whose parameters
  describe **whatever the plotting tab happened to be showing**, under this
  group's name, so Restore draws a different figure from the thumbnail beside
  it. That is not a missing record, it is a wrong one, and nothing on screen
  would say so — hence the AST guard;
* the parameters must be captured *inside* the group scope. The line mode
  already renders inside it and the bar mode never enters it at all, so a
  capture that trusted an ambient scope would be right on one panel and
  silently wrong on the next;
* nothing may reach either destination for a run that did not commit, since
  the file writes are staged and rolled back as a unit and a gallery holding
  the first K figures of a rolled-back run is half a run presented as a run.

The behaviour tests drive the real functions with stubs rather than building an
application: the defects above are in the *sequence* of reads and writes, not
in any widget. One slow-lane test builds the Response panel through the real
constructor — see ``test_batch_export_ctx_characterization.py``.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tests._repo_scan import REPO_ROOT, parse_source, rel  # noqa: E402
from review.features.batch_export.base_panel import (  # noqa: E402
    BatchExportPanel,
)

_BATCH_DIR = REPO_ROOT / "review" / "features" / "batch_export"

#: The two figure-writing seams every batch panel goes through. Both must be
#: told which export group the figure belongs to, because both feed the
#: destinations.
_FIGURE_WRITERS = ("_save_figure", "_export_scatter_figure")


def _figure():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(3, 2), dpi=72, facecolor="#ffffff")
    fig.add_subplot(1, 1, 1).plot([0, 1, 2], [0, 1, 4])
    return fig


# ── the guard: no call site may forget its group ────────────────────────────

def _writer_calls():
    """Every ``self._save_figure(...)`` / ``self._export_scatter_figure(...)``
    call in the batch-export package, as ``(file, line, keywords)``."""
    found = []
    for path in sorted(_BATCH_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _FIGURE_WRITERS:
                continue
            found.append((rel(path), node.lineno,
                          {kw.arg for kw in node.keywords if kw.arg}))
    return found


#: Directory parameters a job's writer is handed. A helper taking the staging
#: directory under another name is the same site wearing a different label —
#: ``_write_cell_rows_for(self, grp, write_dir, …)`` already is one — so the
#: guard watches the names rather than the one spelling that happened to be
#: used first.
_DIR_NAMES = frozenset({"write_dir", "staging"})

#: Functions entitled to build a staging path themselves, each for a written
#: reason. An exemption is a row with a reason, never an absence — a site left
#: out on purpose and one nobody remembered look identical six months later.
_MAY_JOIN = {
    # It is the function the rule is about.
    "_out_path": "builds the path, and answers the question first",
    # It makes the staging directory rather than writing into it.
    "new_staging_dir": "creates the directory the rule is about",
    # The run's single deck, not a per-group file, and already gated on the
    # snapshotted ``to_deck``.
    "write_deck": "one deck for the run, gated on the destination snapshot",
    # A read, not a write: it looks for an earlier run's deck.
    "note_stale_deck": "reads an existing file, writes nothing",
}


def _joins_a_staging_dir(node) -> bool:
    """Whether *node* builds a path under one of :data:`_DIR_NAMES`.

    Every spelling, not just ``write_dir / name``. A reviewer reverted the
    whole nine-site consolidation by rewriting each call as
    ``Path(write_dir).joinpath(name)`` and the guard stayed green, which is
    the failure this function exists to end: a rule that holds one spelling
    has not held the rule, it has held the spelling.
    """
    def _is_dir(expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in _DIR_NAMES
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
                and expr.func.id == "Path" and expr.args:
            return _is_dir(expr.args[0])
        if isinstance(expr, ast.Attribute):
            return _is_dir(expr.value)
        return False

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_dir(node.left)
    if isinstance(node, ast.Call):
        func = node.func
        # ``write_dir.joinpath(x)`` / ``Path(write_dir).joinpath(x)``
        if isinstance(func, ast.Attribute) and func.attr == "joinpath":
            return _is_dir(func.value)
        # ``os.path.join(write_dir, x)``
        if isinstance(func, ast.Attribute) and func.attr == "join" \
                and node.args:
            return _is_dir(node.args[0])
    if isinstance(node, ast.JoinedStr):
        # An f-string interpolating the directory is a path join too.
        return any(_is_dir(part.value) for part in node.values
                   if isinstance(part, ast.FormattedValue))
    return False


#: What this cannot see, said out loud rather than left to be discovered: a
#: **local alias** (``wd = write_dir`` then ``wd / name``) needs dataflow the
#: syntax tree does not carry. The ``_out_path`` call floor below is the other
#: half of the rule, and it is what a wholesale reversion trips — converting
#: the sites away removes the calls it counts.


def _staging_path_joins():
    """Every staging-directory path build in the package, as ``(file, line)``.

    Building a job's output path is exactly the decision ``_out_path`` owns:
    it is where "does this run write files at all" is answered, and a site
    that joins the staging directory itself has answered "yes" without asking.
    Ten such sites existed across four modules before this was one function
    (seven of them feeding a ``with open(...)``, the rest a ``savefig``), and
    converting nine of ten would have been the worse outcome — one folder
    holding the files of a run that asked for none.
    """
    found = []
    for path in sorted(_BATCH_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = parse_source(path)
        owner = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    owner[id(child)] = node.name
        for node in ast.walk(tree):
            if not _joins_a_staging_dir(node):
                continue
            if owner.get(id(node)) in _MAY_JOIN:
                continue
            found.append((rel(path), node.lineno))
    return found


def _out_path_calls():
    """Every ``self._out_path(...)`` call in the package."""
    found = []
    for path in sorted(_BATCH_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(parse_source(path)):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "_out_path":
                found.append((rel(path), node.lineno))
    return found


def test_every_job_output_path_is_built_by_the_one_function():
    sites = _staging_path_joins()
    assert sites == [], (
        "these build a staging path without asking whether this run writes "
        "files; route them through self._out_path(write_dir, name): "
        f"{sites}")
    # The positive control its sibling below already has: "the package is
    # swept" and "the matcher matches nothing" are otherwise the same green,
    # and a guard that found nothing has measured nothing.
    calls = _out_path_calls()
    assert len(calls) >= 9, (
        f"expected the converted sites to still call _out_path: {calls}")


def test_every_batch_figure_write_names_its_group_and_title():
    calls = _writer_calls()
    # The guard measures nothing if it found nothing. Four panels write
    # figures; a scan that came back empty has found a moved seam, not a clean
    # package.
    assert len(calls) >= 4, calls
    missing = [(where, line, sorted(kw))
               for where, line, kw in calls
               if not {"group", "title"} <= kw]
    assert not missing, (
        "Every batch figure write must name the export group it belongs to and "
        "the title its gallery record / deck slide carries. Without ``group=`` "
        "the record's parameters describe the live plotting tab rather than "
        "this group:\n  "
        + "\n  ".join(f"{w}:{ln} passes {kw}" for w, ln, kw in missing))


def test_every_batch_panel_declares_where_its_figures_came_from():
    """Each panel subclass names its source tab and its mode key.

    Inherited silently, a subclass would file its figures under the *line*
    tab's name and write its deck over the line mode's file — a wrong answer
    rather than a missing one, and one that only shows up as a gallery record
    whose Restore lands on the wrong tab.
    """
    from review.features.batch_export.bar_panel import BarBatchExportPanel
    from review.features.batch_export.base_panel import BatchExportPanel
    from review.features.batch_export.nanowell_panel import (
        NanowellBatchExportPanel,
    )
    from review.features.batch_export.response_panel import (
        ResponseBatchExportPanel,
    )
    from review.features.batch_export.scatter_panel import (
        ScatterBatchExportPanel,
    )

    for cls in (BarBatchExportPanel, ResponseBatchExportPanel,
                ScatterBatchExportPanel, NanowellBatchExportPanel):
        assert "_SOURCE_TAB" in cls.__dict__, cls.__name__
        assert "_mode_key" in cls.__dict__, cls.__name__
    tabs = {BatchExportPanel._SOURCE_TAB, BarBatchExportPanel._SOURCE_TAB,
            ResponseBatchExportPanel._SOURCE_TAB,
            ScatterBatchExportPanel._SOURCE_TAB,
            NanowellBatchExportPanel._SOURCE_TAB}
    assert tabs == {"Line Graphs", "Bar Plots", "Response Curves",
                    "Scatter Plot", "Nanowells"}, tabs


def test_the_mode_bar_the_dispatch_and_the_panels_agree():
    """One key per mode, spelled the same in all three places it appears."""
    from review.features.batch_export import tab_view

    bar_keys = [key for _label, key, _feature in tab_view._MODES]
    assert len(set(bar_keys)) == len(bar_keys), bar_keys
    source = parse_source(_BATCH_DIR / "tab_view.py")
    dispatch = []
    for node in ast.walk(source):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_mode_dispatch"
                for t in node.targets):
            dispatch = [k.value for k in node.value.keys]
    assert sorted(dispatch) == sorted(bar_keys), (dispatch, bar_keys)
    assert tab_view._DEFAULT_MODE in bar_keys


def test_response_mode_is_offered_only_where_the_feature_is():
    """A mode whose feature was profiled out is absent, not disabled.

    Left in the bar it would build a panel whose Run button reaches a collector
    that is not in the build — a page that looks complete and raises on use.
    """
    from review.features.batch_export import tab_view

    present = SimpleNamespace(features=SimpleNamespace(get=lambda name: object()))
    absent = SimpleNamespace(features=SimpleNamespace(get=lambda name: None))
    offered = {k for _l, k in tab_view._offered_modes(present)}
    assert {"response", "nanowell"} <= offered
    offered_without = {k for _l, k in tab_view._offered_modes(absent)}
    assert "response" not in offered_without
    assert "nanowell" not in offered_without
    # The four that need no other feature stay, so the guard is measuring the
    # gate and not an empty list.
    assert offered_without == {"line", "bar", "scatter_cells", "scatter_agg"}


# ── the capture point ───────────────────────────────────────────────────────

def test_the_capture_callback_runs_after_the_file_and_before_the_close():
    """``on_saved`` sees the written file and a figure that is still open.

    Called first, a capture that raised would cost the user the file they
    actually asked for. Called after the close, there is no figure left to
    render a second time — and both mistakes leave a green test if the callback
    is only asserted to have *run*.
    """
    from review.features.batch_export.service import save_export_figure

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "plot.png"
        seen = {}

        def _capture(fig):
            seen["file_exists"] = out.exists() and out.stat().st_size > 0
            seen["still_open"] = plt.fignum_exists(fig.number)

        fig = plt.figure()
        fig.add_subplot(111).plot([0, 1], [0, 1])
        save_export_figure(fig, out, "png", export_prefs={},
                           plot_bg="#ffffff", on_saved=_capture)
        assert seen == {"file_exists": True, "still_open": True}
        assert not plt.fignum_exists(fig.number)


# ── the parameters describe the group, not the live tab ─────────────────────

class _ParamScopeStub:
    """The two scope helpers and the capture, off the real class."""

    _app_group_scope = BatchExportPanel._app_group_scope
    _app_val_col_scope = BatchExportPanel._app_val_col_scope
    _control_selections = BatchExportPanel._control_selections
    _fold_change_control_refs = BatchExportPanel._fold_change_control_refs
    _capture_group_params = BatchExportPanel._capture_group_params
    _mode_key = BatchExportPanel._mode_key
    _SOURCE_TAB = "Line Graphs"

    def __init__(self, live_selections):
        self._fc_control = None
        self._fc_control_map = {}
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(
                channel=SimpleNamespace(active_val_col="gfp_mean_intensity"),
                selection=SimpleNamespace(
                    selections=list(live_selections),
                    current_selection_id=None,
                    selected_wells=set()),
            ),
        )


def _group(name, member_name, wells):
    from review.domain.batch_models import BarGroup, ReplicateSet

    return BarGroup(name, [ReplicateSet(member_name, list(wells))])


def test_the_captured_parameters_describe_the_group_not_the_live_tab(monkeypatch):
    """Captured inside the group scope, so each record is its own group's.

    Outside it, ``capture_params`` snapshots the application's live selections —
    whatever the plotting tabs are showing while the run is in flight — and
    every group in the run would file identical parameters under a different
    name. That is not a thin record, it is a wrong one: Restore draws a figure
    that is not the thumbnail beside it.
    """
    import review.features.saved_plots.collect as sp_collect

    seen = {}

    def _capture(ctx, tab):
        seen["names"] = [s.get("name") for s in ctx.state.selection.selections]
        seen["column"] = ctx.state.channel.active_val_col
        seen["tab"] = tab
        return {"selections": list(ctx.state.selection.selections)}, {}

    monkeypatch.setattr(sp_collect, "capture_params", _capture)

    live = [{"name": "WHAT THE TAB IS SHOWING", "wells": ["A01"], "id": "live"}]
    panel = _ParamScopeStub(live)
    params, _thresholds = panel._capture_group_params(
        _group("Drug A", "rep 1", ["B02", "B03"]), "mcherry_total_intensity")

    assert seen["names"] == ["rep 1"], seen["names"]
    assert seen["column"] == "mcherry_total_intensity"
    assert seen["tab"] == "Line Graphs"
    # And the live state is put back, so an export never leaves the batch's own
    # column or groups on the application afterwards.
    assert panel._ctx.state.selection.selections == live
    assert panel._ctx.state.channel.active_val_col == "gfp_mean_intensity"
    # Stamped, so the gallery can say where the record came from.
    assert params["batch_export"] == {"mode": "line", "group": "Drug A"}


def test_a_group_scope_nested_inside_itself_describes_the_same_group():
    """The line mode already renders inside the scope, so the capture re-enters
    it. Benign only if the inner pass rebuilds the same list — asserted rather
    than assumed, because the inner ``_control_selections`` reads the *scoped*
    selections rather than the live ones."""
    panel = _ParamScopeStub(
        [{"name": "elsewhere", "wells": ["A01"], "id": "live"}])
    grp = _group("Drug A", "rep 1", ["B02", "B03"])
    sel = panel._ctx.state.selection
    with panel._app_group_scope(grp):
        once = [(s.get("name"), tuple(s.get("wells") or ())) for s in sel.selections]
        with panel._app_group_scope(grp):
            twice = [(s.get("name"), tuple(s.get("wells") or ()))
                     for s in sel.selections]
        assert [(s.get("name"), tuple(s.get("wells") or ()))
                for s in sel.selections] == once
    assert twice == once == [("rep 1", ("B02", "B03"))]


def test_a_figure_with_no_group_files_no_parameters_rather_than_the_wrong_ones():
    panel = _ParamScopeStub([{"name": "live", "wells": ["A01"], "id": "live"}])
    assert panel._capture_group_params(None, "gfp_mean_intensity") == ({}, {})


# ── the spool ───────────────────────────────────────────────────────────────

def _spool(tmp_path, **kwargs):
    from review.features.batch_export._destinations import DestinationSpool

    defaults = dict(to_gallery=True, to_deck=False, tab="Line Graphs",
                    mode_key="line", dataset_name="plate")
    defaults.update(kwargs)
    spool = DestinationSpool(**defaults)
    spool.begin(tmp_path)
    return spool


def test_a_spool_nobody_asked_for_stages_nothing(tmp_path):
    spool = _spool(tmp_path, to_gallery=False, to_deck=False)
    assert not spool.wanted
    spool.capture(_figure(), title="Drug A")
    assert spool.summary() == "" and spool.shortfall() == ""
    # Nothing was created under the output directory, so an unticked run costs
    # the user no I/O at all.
    assert list(tmp_path.iterdir()) == []


def test_a_capture_stages_a_raster_and_a_vector(tmp_path):
    spool = _spool(tmp_path)
    spool.capture(_figure(), title="Drug A", params={"active_channel": "gfp"})
    assert len(spool._captures) == 1
    cap = spool._captures[0]
    assert cap.title == "Drug A" and cap.tab == "Line Graphs"
    assert cap.png_path.exists() and cap.png_path.stat().st_size > 0
    # The vector twin is what makes the deck editable, so it is asserted as a
    # real SVG rather than merely as a path that is not None.
    assert cap.svg_path is not None and cap.svg_path.read_bytes().startswith(b"<?xml")
    assert spool.raster_only == 0
    assert cap.params == {"active_channel": "gfp"}


def test_a_figure_with_no_vector_is_kept_as_a_picture_and_counted(tmp_path,
                                                                  monkeypatch):
    """A vector render that fails costs editability, never the record.

    And it is counted: a deck where some figures are shapes and some are
    pictures is otherwise indistinguishable from one where all of them are.
    """
    import widgets.fig_clipboard as fig_clipboard

    monkeypatch.setattr(fig_clipboard, "render_figure_svg",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    spool = _spool(tmp_path, to_deck=True)
    spool.capture(_figure(), title="Drug A")
    cap = spool._captures[0]
    assert cap.png_path.exists() and cap.svg_path is None
    assert spool.raster_only == 1
    # Not yet: no deck has been written, so there is no artifact for the note
    # to be about. A run that errors discards its staging before the deck is
    # ever built, and a status line counting pictures in a deck nobody wrote
    # describes a file that is not there.
    assert "picture rather than editable shapes" not in spool.shortfall()
    staging = tmp_path / "staging"
    staging.mkdir()
    spool.write_deck(staging)
    assert spool.deck_written
    assert "picture rather than editable shapes" in spool.shortfall()


def test_a_capture_that_cannot_be_written_is_counted_not_dropped(tmp_path,
                                                                 monkeypatch):
    spool = _spool(tmp_path)

    class _Broken:
        def get_facecolor(self):
            return "#ffffff"

        def savefig(self, *a, **k):
            raise OSError("disk full")

    spool.capture(_Broken(), title="Drug A")
    assert spool._captures == []
    assert spool.capture_failures == 1
    assert "could not be captured" in spool.shortfall()


def test_discard_removes_everything_staged(tmp_path):
    spool = _spool(tmp_path)
    spool.capture(_figure(), title="Drug A")
    staged = spool._dir
    assert staged is not None and staged.exists()
    spool.discard()
    assert not staged.exists()
    assert spool._captures == []


def test_the_deck_is_written_into_the_export_s_own_staging_dir(tmp_path):
    """So it commits with the figures rather than beside a rolled-back run."""
    spool = _spool(tmp_path, to_gallery=False, to_deck=True)
    spool.capture(_figure(), title="Drug A")
    spool.capture(_figure(), title="Drug B")
    staging = tmp_path / "staging"
    staging.mkdir()
    spool.write_deck(staging)
    deck = staging / "batch_export_line.pptx"
    assert deck.exists() and deck.stat().st_size > 0
    assert spool.deck_error == ""
    # A .pptx is a zip of XML parts; one slide per figure plus the cover.
    import zipfile
    with zipfile.ZipFile(deck) as zf:
        slides = [n for n in zf.namelist()
                  if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
    assert len(slides) == 3, sorted(slides)
    assert spool.summary() == "batch_export_line.pptx"


def test_each_mode_writes_its_own_deck(tmp_path):
    """Two modes exported into one folder leave two decks, not one.

    A shared name would leave the user with whichever mode they ran second,
    silently overwritten over the first.
    """
    from review.features.batch_export._destinations import DestinationSpool

    from review.features.batch_export import tab_view

    keys = [key for _label, key, _feature in tab_view._MODES]
    names = {
        DestinationSpool(to_gallery=False, to_deck=True, tab="t",
                         mode_key=key).deck_name
        for key in keys
    }
    assert len(names) == len(keys) == 6, names


# ── the gallery ─────────────────────────────────────────────────────────────

class _FakeStore:
    """Just enough of ``SavedPlotsStore`` to watch what the spool files."""

    def __init__(self, images_dir):
        self.records = []
        self._images_dir = images_dir
        self.persist_calls = 0
        self.added = []

    def ensure_loaded(self, ctx):
        pass

    def images_dir(self, ctx):
        return self._images_dir

    def add(self, ctx, rec, png, svg=b"", frames=b"", *, persist=True):
        self.added.append((rec, png, svg, persist))
        self.records.append(rec)
        return True

    def persist(self, ctx):
        self.persist_calls += 1


def test_the_gallery_is_written_once_for_a_whole_batch(tmp_path, monkeypatch):
    """One ``persistence.json`` rewrite for the run, not one per figure.

    That file also holds the view state and Batch Export's own group
    definitions; a record-at-a-time write is a full read/write cycle of it per
    exported group.
    """
    import review.features.saved_plots.controller as sp_controller
    import review.features.saved_plots.state as sp_state
    from review.features.batch_export import _destinations

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(sp_state, "get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "refresh_saved_plots_gallery",
                        lambda ctx: None)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    spool = _spool(tmp_path)
    for name in ("Drug A", "Drug B", "Drug C"):
        spool.capture(_figure(), title=name, params={"active_channel": "gfp"})
    spool.file_in_gallery(object())

    assert spool.filed == 3
    assert [rec.title for rec, *_ in store.added] == ["Drug A", "Drug B", "Drug C"]
    assert all(persist is False for *_rest, persist in store.added)
    assert store.persist_calls == 1
    # Every record got real bytes: a record whose PNG never arrived is a blank
    # thumbnail in the gallery, which is the failure this whole path is for.
    assert all(png and svg for _rec, png, svg, _p in store.added)
    assert all(rec.tab == "Line Graphs" for rec, *_ in store.added)
    assert len({rec.id for rec, *_ in store.added}) == 3


def test_a_record_the_gallery_refused_is_not_counted_as_filed(tmp_path,
                                                              monkeypatch):
    """The gallery's writer reports a failed image write by *returning*.

    ``SavedPlotsStore.add`` returns False on an ``OSError`` writing the PNG and
    appends nothing; ``save_figure_to_gallery`` turns that into a status string
    and returns it. A count taken from the call *returning* therefore reported
    ten records filed over an empty gallery — the §7 cardinal sin, in the one
    method whose docstring promises the opposite. The count comes from the
    store growing instead, which is the fact that matters.
    """
    import review.features.saved_plots.controller as sp_controller
    from review.features.batch_export import _destinations

    class _RefusingStore(_FakeStore):
        def add(self, ctx, rec, png, svg=b"", frames=b"", *, persist=True):
            return False        # what a read-only saved_plots/ really produces

    store = _RefusingStore(tmp_path / "saved_plots")
    monkeypatch.setattr(_destinations, "_get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    spool = _spool(tmp_path)
    for name in ("Drug A", "Drug B", "Drug C"):
        spool.capture(_figure(), title=name)
    spool.file_in_gallery(object())

    assert store.records == []
    assert spool.filed == 0
    assert spool.gallery_failures == 3
    assert "3 could not be written to Saved Plots" in spool.shortfall()
    assert "to Saved Plots" not in spool.summary(), spool.summary()


def test_a_figure_filed_without_its_settings_is_counted(tmp_path):
    """A record with no parameters restores nothing, and in the gallery it is
    indistinguishable from one that restores correctly — until pressed."""
    spool = _spool(tmp_path)
    spool.undescribed = 2
    assert "2 filed without their settings" in spool.shortfall()
    assert "Restore will not reproduce them" in spool.shortfall()


def test_an_earlier_run_s_deck_is_named_when_this_run_wrote_none(tmp_path):
    """Decks are named per mode, so a stale one carries a fresh one's name.

    A stale *figure* is named for its group, so a group this run did not export
    shows in the file name. A deck does not, which is why this is said and a
    stale PNG is not — and it is said even for a run that ticked neither
    destination, which is exactly the run it happens to.
    """
    from review.features.batch_export._destinations import DestinationSpool

    out = tmp_path / "out"
    out.mkdir()
    (out / "batch_export_line.pptx").write_bytes(b"from an earlier run")

    quiet = DestinationSpool(to_gallery=False, to_deck=False, tab="Line Graphs",
                             mode_key="line")
    assert not quiet.wanted
    quiet.note_stale_deck(out)
    assert quiet.stale_deck
    assert "batch_export_line.pptx is from an earlier run" in quiet.shortfall()

    # A run that *did* write one has replaced it, so there is nothing to say.
    fresh = DestinationSpool(to_gallery=False, to_deck=True, tab="Line Graphs",
                             mode_key="line")
    fresh.note_stale_deck(out)
    assert not fresh.stale_deck
    # And a folder with no earlier deck says nothing either, so the note is
    # measuring the file rather than the flag.
    empty = DestinationSpool(to_gallery=False, to_deck=False, tab="Line Graphs",
                             mode_key="bar")
    empty.note_stale_deck(out)
    assert not empty.stale_deck and empty.shortfall() == ""


def test_save_figure_to_gallery_files_the_params_it_is_handed(tmp_path,
                                                              monkeypatch):
    """It must not snapshot the live application over what it was given.

    Batch Export renders a figure the user never had on screen, so a function
    that captured the live view state here would file ten records carrying the
    plotting tab's parameters under ten different group names.
    """
    import review.features.saved_plots.controller as sp_controller

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "capture_params",
                        lambda ctx, tab: ({"NEVER": True}, {"NEVER": True}))
    monkeypatch.setattr(sp_controller, "refresh_saved_plots_gallery",
                        lambda ctx: None)

    sp_controller.save_figure_to_gallery(
        object(), tab="Bar Plots", title="Drug A", png_bytes=b"png",
        svg_bytes=b"svg", params={"active_channel": "mcherry"},
        thresholds={"fluor_gates": {}})
    rec = store.records[0]
    assert rec.params == {"active_channel": "mcherry"}
    assert rec.thresholds == {"fluor_gates": {}}
    assert rec.tab == "Bar Plots" and rec.title == "Drug A"


def test_a_record_id_is_never_reused(tmp_path, monkeypatch):
    """Two records filed in the same microsecond must not share a file name.

    The id names the record's PNG on disk, so a collision does not clash
    cosmetically — the second record's image overwrites the first's and the
    gallery shows one figure twice.
    """
    import review.features.saved_plots.controller as sp_controller

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "refresh_saved_plots_gallery",
                        lambda ctx: None)

    class _FrozenClock:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 1, 1, 12, 0, 0, 123456)

    monkeypatch.setattr(sp_controller, "datetime", _FrozenClock)
    for name in ("A", "B", "C"):
        sp_controller.save_figure_to_gallery(
            object(), tab="Line Graphs", title=name, png_bytes=b"png")
    ids = [r.id for r in store.records]
    assert len(set(ids)) == 3, ids


def test_the_summary_says_a_record_came_from_batch_export():
    """And that Restore re-applies the group, not the panel's layout options."""
    from review.features.saved_plots.collect import build_summary

    plain = build_summary("Bar Plots", {"active_channel": "gfp"}, {})
    assert "Batch Export" not in plain

    batched = build_summary(
        "Bar Plots",
        {"active_channel": "gfp",
         "batch_export": {"mode": "bar", "group": "Drug A"}}, {})
    assert "From: Batch Export (bar) — group “Drug A”" in batched
    assert "not Batch Export's own layout options" in batched


# ── the run's ordering ──────────────────────────────────────────────────────
#
# The three tests below drive the real ``_run_batch_jobs`` — the orchestration
# that decides *when* each destination is fed. Both defects it can have are
# ordering defects, invisible to a unit test of either end: a gallery filled as
# each figure renders holds the first K figures of a run whose output directory
# was rolled back, and a deck written after the commit lands in the folder
# without being under the manifest that describes it.


class _RunStub:
    """The runner and the spool factory, off the real class, with real widgets.

    Real Qt widgets rather than stubs because ``_run_batch_jobs`` drives a
    progress bar and pumps the event loop; a fake would answer a different
    question from the one being asked.
    """

    _run_batch_jobs = BatchExportPanel._run_batch_jobs
    _new_destination_spool = BatchExportPanel._new_destination_spool
    # The runner reads the AXES block once per run, so a stub driving it needs
    # the real reader — borrowed rather than stubbed, so a mode that stopped
    # snapshotting fails here rather than exporting whatever the fields held at
    # the moment each figure happened to be rendered.
    _snapshot_axes = BatchExportPanel._snapshot_axes
    # The runner asks whether anything lands on disk before it commits, so a
    # stub answering that itself would decide the very thing under test.
    _ticked_destinations = BatchExportPanel._ticked_destinations
    _destinations = BatchExportPanel._destinations
    _where_it_went = BatchExportPanel._where_it_went
    _set_destination_row_enabled = BatchExportPanel._set_destination_row_enabled
    _enabled_when_idle = BatchExportPanel._enabled_when_idle
    _sync_destination_widgets = BatchExportPanel._sync_destination_widgets
    _wants_image_files = BatchExportPanel._wants_image_files
    _wants_deck = BatchExportPanel._wants_deck
    _wants_gallery = BatchExportPanel._wants_gallery
    _wants_cell_rows = BatchExportPanel._wants_cell_rows
    _out_path = BatchExportPanel._out_path
    _wants_disk_output = BatchExportPanel._wants_disk_output
    _PANELS = BatchExportPanel._PANELS
    _SOURCE_TAB = "Line Graphs"
    _mode_key = BatchExportPanel._mode_key

    def __init__(self, data_dir, *, to_gallery=False, to_deck=False,
                 to_files=True):
        from PySide6.QtWidgets import QCheckBox, QLabel, QProgressBar, QPushButton

        self._to_files_cb = QCheckBox()
        self._to_files_cb.setChecked(to_files)

        self._prog_bar = QProgressBar()
        self._prog_lbl = QLabel()
        self._run_btn = QPushButton()
        self._to_gallery_cb = QCheckBox()
        self._to_gallery_cb.setChecked(to_gallery)
        self._to_deck_cb = QCheckBox()
        self._to_deck_cb.setChecked(to_deck)
        self.statuses = []
        # No AXES rows built, which is the neutral case: ``_snapshot_axes``
        # returns an empty spec and nothing about the run's axes changes.
        self._panel_rows = {}
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(dataset=SimpleNamespace(data_dir=data_dir)),
            notify=SimpleNamespace(set_status=self.statuses.append),
        )

    def _save_export_groups_now(self):
        pass


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _run(panel, out_dir, jobs, run_job_fn):
    panel._run_batch_jobs(
        jobs=jobs, progress_text_fn=lambda job, step, total: str(job),
        run_job_fn=run_job_fn, success_text="done",
        status_text="Batch export", out_dir=out_dir)


def test_a_committed_run_leaves_the_deck_under_the_manifest(tmp_path,
                                                            monkeypatch):
    from review.features.batch_export.service import MANIFEST_NAME

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_deck=True)

    def _job(name, write_dir):
        (write_dir / f"{name}.csv").write_text("x")
        panel._spool.capture(_figure(), title=name)
        return None

    _run(panel, out, ["Drug A", "Drug B"], _job)

    import json
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert "batch_export_line.pptx" in manifest["files"], manifest
    assert (out / "batch_export_line.pptx").exists()
    # Named in the status line, because a file the user did not ask for by name
    # is one they have to find.
    assert any("batch_export_line.pptx" in s for s in panel.statuses), panel.statuses
    # The spool's own staging is gone; only the run's outputs remain.
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["Drug A.csv", "Drug B.csv", MANIFEST_NAME, "batch_export_line.pptx"])


def test_a_failed_run_files_nothing_in_the_gallery(tmp_path, monkeypatch):
    """Not even the figures that succeeded before the failure.

    The output directory is rolled back as a unit; a gallery holding the first
    K figures of a run that produced no files is half a run presented as a run.
    """
    import review.features.saved_plots.controller as sp_controller
    from review.features.batch_export import _destinations

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(_destinations, "_get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_gallery=True, to_deck=True)

    def _job(name, write_dir):
        (write_dir / f"{name}.csv").write_text("x")
        panel._spool.capture(_figure(), title=name)
        return None if name != "Drug B" else "Drug B blew up"

    _run(panel, out, ["Drug A", "Drug B"], _job)

    assert store.records == []
    assert store.persist_calls == 0
    # And no output at all — including no deck, which is staged with the files.
    assert not out.exists() or list(out.iterdir()) == []


def test_a_committed_run_files_every_figure_in_the_gallery(tmp_path,
                                                           monkeypatch):
    import review.features.saved_plots.controller as sp_controller
    from review.features.batch_export import _destinations

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(_destinations, "_get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_gallery=True)

    def _job(name, write_dir):
        (write_dir / f"{name}.csv").write_text("x")
        panel._spool.capture(_figure(), title=name)
        return None

    _run(panel, out, ["Drug A", "Drug B"], _job)

    assert [r.title for r in store.records] == ["Drug A", "Drug B"]
    assert store.persist_calls == 1
    assert any("2 to Saved Plots" in s for s in panel.statuses), panel.statuses
    # The run's own state is cleared, so a figure written outside a run cannot
    # reach the spool the previous run used.
    assert panel._spool is None


def test_a_run_with_no_destinations_stages_nothing_extra(tmp_path):
    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path)

    def _job(name, write_dir):
        (write_dir / f"{name}.csv").write_text("x")
        return None

    _run(panel, out, ["Drug A"], _job)
    from review.features.batch_export.service import MANIFEST_NAME
    assert sorted(p.name for p in out.iterdir()) == ["Drug A.csv", MANIFEST_NAME]
    # The runner appends where the output went; this run wrote image files,
    # so it names the folder it really filled.
    assert panel.statuses == [f"Batch export \u2192 {out}"]


# ── a gallery-only run touches no directory of the user's ──────────────────

def test_a_gallery_only_run_leaves_no_output_folder_behind(tmp_path,
                                                           monkeypatch):
    """Committing would create the folder and a manifest describing no files.

    That manifest is an audit record of an export that produced nothing on
    disk, in a directory the user never asked to have made. The run still
    succeeded, so the gallery is still filled — asserted here too, because a
    "discard instead of commit" that also skipped the filing would satisfy
    the first half of this and quietly produce nothing at all.
    """
    from review.features.batch_export.service import MANIFEST_NAME

    import review.features.saved_plots.controller as sp_controller
    from review.features.batch_export import _destinations

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(_destinations, "_get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_gallery=True, to_files=False)

    def _job(name, write_dir):
        # The job writes nothing: every path it would have used came back
        # ``None`` from ``_out_path``.
        panel._spool.capture(_figure(), title=name)
        return None

    _run(panel, out, ["Drug A", "Drug B"], _job)

    assert not (out / MANIFEST_NAME).exists()
    assert not out.exists(), sorted(p.name for p in out.iterdir())
    assert [r.title for r in store.records] == ["Drug A", "Drug B"]


def test_a_deck_only_run_still_commits_because_a_deck_is_a_file(tmp_path,
                                                               monkeypatch):
    """The deck needs the folder; the images do not have to come with it."""
    import json

    from review.features.batch_export.service import MANIFEST_NAME

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_deck=True, to_files=False)

    def _job(name, write_dir):
        panel._spool.capture(_figure(), title=name)
        return None

    _run(panel, out, ["Drug A"], _job)

    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert manifest["files"] == ["batch_export_line.pptx"], manifest
    assert (out / "batch_export_line.pptx").exists()
    # And nothing else: no PNG, no CSV, from a run that asked for neither.
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [MANIFEST_NAME, "batch_export_line.pptx"])


# ── the destinations are fixed for a run, and reported honestly ────────────

def test_a_gallery_only_run_names_saved_plots_not_a_folder_it_never_wrote(
        tmp_path, monkeypatch):
    """The headline used to interpolate the output folder unconditionally.

    So a gallery-only run announced "2 group(s) → out/" and handed the status
    bar an absolute path to a directory that does not exist and received
    nothing — and where an earlier run had left a deck there, to a real
    directory whose only content belongs to another run.
    """
    import review.features.saved_plots.controller as sp_controller
    from review.features.batch_export import _destinations

    store = _FakeStore(tmp_path / "saved_plots")
    monkeypatch.setattr(_destinations, "_get_store", lambda ctx: store)
    monkeypatch.setattr(sp_controller, "get_store", lambda ctx: store)
    monkeypatch.setattr(_destinations, "_refresh_gallery", lambda ctx: None)

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_gallery=True, to_files=False)

    def _job(name, write_dir):
        panel._spool.capture(_figure(), title=name)
        return None

    _run(panel, out, ["Drug A", "Drug B"], _job)

    assert "→ Saved Plots" in panel._prog_lbl.text()
    assert str(out) not in panel._prog_lbl.text()
    assert all(str(out) not in s for s in panel.statuses), panel.statuses
    assert any("→ Saved Plots" in s for s in panel.statuses), panel.statuses


def test_a_run_that_writes_files_still_names_the_folder(tmp_path):
    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path)

    def _job(name, write_dir):
        (write_dir / f"{name}.csv").write_text("x")
        return None

    _run(panel, out, ["Drug A"], _job)
    assert f"→ {out.name}/" in panel._prog_lbl.text()
    assert any(str(out) in s for s in panel.statuses), panel.statuses


def test_unticking_image_files_mid_run_cannot_discard_what_was_written(
        tmp_path):
    """The runner pumps the event loop between jobs, so the row is live.

    Read per job, unticking Image files halfway gave the first groups their
    files, the rest none, and then — because the commit consulted the same
    live answer — discarded the ones already written and called the run a
    success. Files destroyed, reported green: the exact shape CLAUDE.md §7
    forbids.
    """
    from review.features.batch_export.service import MANIFEST_NAME

    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path)
    seen = []

    def _job(name, write_dir):
        path = panel._out_path(write_dir, f"{name}.csv")
        seen.append(path is not None)
        if path is not None:
            path.write_text("x")
        # The user clicks the box while job one is finishing.
        panel._to_files_cb.setChecked(False)
        return None

    _run(panel, out, ["Drug A", "Drug B"], _job)

    assert seen == [True, True], "the snapshot must outlast the click"
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["Drug A.csv", "Drug B.csv", MANIFEST_NAME])


def test_ticking_image_files_mid_run_cannot_move_staging_across_devices(
        tmp_path):
    """The mirror case, which raised rather than lost data.

    Staging is placed by the same answer the commit later consults: chosen as
    a system temp dir for a gallery-only run, the commit would then
    ``os.replace`` across filesystems — the one thing ``new_staging_dir``
    exists to prevent — and the exception escaped into the Run slot with no
    dialog.
    """
    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path, to_gallery=True, to_files=False)

    def _job(name, write_dir):
        panel._to_files_cb.setChecked(True)
        assert panel._out_path(write_dir, f"{name}.csv") is None
        return None

    _run(panel, out, ["Drug A"], _job)
    assert not out.exists()


def test_the_destination_row_is_locked_while_a_run_is_in_flight(tmp_path):
    """What makes the snapshot legible rather than merely correct."""
    _qapp()
    out = tmp_path / "out"
    panel = _RunStub(tmp_path)
    during = []

    def _job(name, write_dir):
        during.append((panel._to_files_cb.isEnabled(),
                       panel._to_deck_cb.isEnabled(),
                       panel._to_gallery_cb.isEnabled()))
        return None

    _run(panel, out, ["Drug A"], _job)
    assert during == [(False, False, False)]
    assert panel._to_files_cb.isEnabled()
    assert panel._to_gallery_cb.isEnabled()
