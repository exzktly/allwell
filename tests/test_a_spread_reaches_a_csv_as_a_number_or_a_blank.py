"""An error column is a number or an empty cell — never the word ``nan``.

Every exporter in the app writes a spread beside the value it belongs to, and
each one has to decide what to print when the spread was never computed: a
group of one replicate, a well whose FOV labels are unusable, a fraction with
one sample. The rule the app settled on is written at
``export/service.spread_cell`` — blank for undefined, and the number (zero
included, because zero is a measurement) otherwise.

Two exporters had not joined it. The scatter aggregate wrote
``f"{x_err:.6f}"`` on the tab and ``f"{...:.8g}"`` in both of its Batch Export
jobs, so a single selected well — the commonest selection on that tab — put the
literal string ``nan`` in the file. A spreadsheet reads that cell as *text*, so
the column stops being numeric for every row beneath it and a chart built on it
silently drops the series, which is exactly why the line and bar writers blank
it. The guard below is what keeps a fourth writer from being added with its own
answer, since nothing about a bare f-string looks wrong at the call site.

The behavioural tests beside it pin the other half of the sweep: what an error
column is *over*. SD across wells and SD across fields are different numbers,
and a reader reconstructing one from the other takes two columns that do not
share a denominator.
"""
from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: A CSV row is built as a dict literal, so the rule is decidable from the key.
#: These are the column names that carry an interval rather than a value.
_SPREAD_KEY = ("err", "error", "spread", "_sd", "_sem")

#: The same idea with the band in front, which is how the line CSV spells its
#: own spread column (``sem_gfp_mean_intensity``) and how several locals read.
_SPREAD_PREFIX = ("err", "sem_", "sd_", "spread")

#: Keys that name *what* the interval is rather than holding one. They are
#: strings by construction ("SEM", "fov"), and blanking them would be wrong.
_METADATA_KEYS = frozenset({
    "spread_kind", "spread_unit", "err_kind", "spread_label",
    # NOT ``err_name``: that is the variable holding the Heat Map's column
    # name, and the cell it keys is the spread itself. Exempting it here — the
    # first draft did — put the one Name-keyed spread column in the app back
    # out of the guard's sight, which is the reason ``_key_text`` reads a Name
    # at all.
    # A payload key spelled exactly ``error`` is a failure *message*, not an
    # error *bar* — ``spatial/controller.py`` sets one from an exception, and
    # blanking it would delete the reason a panel is empty. Every real spread
    # column in this repo carries the band or the axis with it
    # (``x_err``, ``err_frac_SEM``, ``gfp_mean_intensity_error``), so the bare
    # word is safe to exempt and a suffixed one is still caught.
    "error", "errors", "error_message",
})


def _key_text(node) -> str:
    """The literal text of a dict key, including the fixed parts of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.Name):
        # ``err_name: f"…"`` — the Heat Map builds its column name first. The
        # variable's own name is the only thing there is to read, and it is
        # exactly as informative as a literal would have been.
        return node.id
    return ""


def _names_a_spread(text: str) -> bool:
    """Whether *text* — a column name or a variable name — holds an interval."""
    low = text.lower()
    if low in _METADATA_KEYS:
        return False
    if any(token in low for token in _SPREAD_KEY):
        return True
    # The repo spells its line-CSV spread columns and its locals with the band
    # in FRONT: ``sem_gfp_mean_intensity``, ``sd_gfp_mean_intensity``. A
    # substring test for ``_sem`` sees neither.
    return any(low.startswith(prefix) for prefix in _SPREAD_PREFIX)


def _formats_a_spread(value) -> bool:
    """Whether *value* is an f-string interpolating a spread-named expression.

    The other half of the rule, and the half that survives a key the guard
    cannot read: ``f"{spread:.6f}"`` says what it is formatting whatever the
    column is called. It is what makes the Line CSV's own spread cell — keyed
    ``f"{band_lbl.lower()}_{ch}_{metric}"``, whose constant parts are just
    ``"__"`` — visible to this guard at all.
    """
    if not isinstance(value, ast.JoinedStr):
        return False
    for part in ast.walk(value):
        if not isinstance(part, ast.FormattedValue):
            continue
        for inner in ast.walk(part.value):
            name = (inner.id if isinstance(inner, ast.Name)
                    else inner.attr if isinstance(inner, ast.Attribute) else "")
            if name and _names_a_spread(name):
                return True
    return False


def _row_cells(tree):
    """Every ``(name, value)`` pair a row-building statement writes.

    Two shapes, both live in this repo: a dict literal (``{"x_err": …}``) and a
    subscript assignment onto an existing row (``row[f"fold_change_{band}"] =
    …``), which is how the fold-change columns are appended and which a
    dict-only scan walked straight past.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    yield _key_text(key), value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    yield _key_text(target.slice), node.value


def test_no_exporter_formats_a_spread_without_a_blank_for_undefined():
    """A bare f-string cannot say "not computed", so it is the shape banned.

    An ``IfExp`` (``f"{v:.6f}" if is_number(v) else ""``) and a call
    (``spread_cell(v)``, ``_num(v)``, ``blank_if_undefined(v)``) both have a
    branch for the undefined case; an unconditional ``JoinedStr`` does not, and
    ``f"{float('nan'):.6f}"`` is the string ``nan``.

    A cell is caught by its **column name** or by the **expression it
    formats**, because neither alone sees every site: the Heat Map's column
    name is a variable, the Line CSV's is an f-string with no fixed text in it,
    and a batch writer's is a plain literal.

    What this cannot see, stated rather than left to be discovered: a row built
    as a **list** and handed to ``csv.writer`` — the Response CSVs
    (``response/controller.py``, ``batch_export/response_panel.py``) are that
    shape, and their cells are positional, so nothing in the syntax says which
    element is a spread. Both go through ``blank_if_undefined``, and
    ``tests/test_response_collect.py`` pins that; a third list-shaped exporter
    would need its own test rather than this guard.
    """
    offenders: list[str] = []
    for path in python_sources():
        tree = parse_source(path)
        if tree is None:
            continue
        for name, value in _row_cells(tree):
            if not isinstance(value, ast.JoinedStr):
                continue
            if (name and _names_a_spread(name)) or _formats_a_spread(value):
                offenders.append(f"{rel(path)}:{value.lineno} {name or '?'!r}")
    assert not offenders, (
        "a spread column is written with an unconditional f-string, so an "
        "undefined spread reaches the CSV as the literal 'nan':\n  "
        + "\n  ".join(offenders)
    )


# ── what the rule itself does ────────────────────────────────────────────────

def test_spread_cell_blanks_the_undefined_and_keeps_a_measured_zero():
    from well_viewer.features.export.service import spread_cell

    assert spread_cell(float("nan")) == ""
    assert spread_cell(None) == ""
    assert spread_cell(float("inf")) == ""
    # Zero is a measurement: the replicates agreed exactly.
    assert spread_cell(0.0) == "0.000000"
    assert spread_cell(1.5) == "1.500000"
    # The batch scatter writers keep their own precision.
    assert spread_cell(1.23456789, ".8g") == "1.2345679"


def test_spread_unit_names_the_replicate_the_app_is_using():
    """One spelling of the FOV rule — ``stats_compute.replicate_unit`` — so an
    exported column cannot disagree with the statistic the plot drew."""
    from well_viewer.features.export.service import spread_unit_for
    from tests.conftest import MockApp, make_agg

    ctx = getattr(MockApp(per_well_pts={"A01": [make_agg(0.0, 1.0)]}), "_ctx", None)
    ctx.state.plot.use_fov_replicates = False
    assert spread_unit_for(ctx) == "well"
    assert spread_unit_for(ctx, 3) == "well"
    ctx.state.plot.use_fov_replicates = True
    assert spread_unit_for(ctx, 3) == "fov"
    # A row covering no loaded well has nothing for a replicate to be.
    assert spread_unit_for(ctx, 0) == ""


# ── the Bar tab's Events unit, which the Line tab's fix put out of step ──────

def test_the_bar_row_says_fov_for_a_lone_well_under_aggregate_fovs():
    """``collect_bar_items`` puts the per-FOV mean in ``n_above`` on both
    branches, so a solo well's row states one — and used to state it under an
    empty unit with its spread blanked, while ``err_n_above_<band>`` two
    columns away carried that same spread. The Line tab says ``fov`` here."""
    from well_viewer.features.export.service import (
        _replicate_unit_for, bar_metric_row,
    )

    class _Item:
        def __init__(self, n_above, total):
            self.n_above, self.n_above_total = n_above, total

    solo = _Item(3.5, None)
    assert _replicate_unit_for(solo, per_fov=True) == "fov"
    row = bar_metric_row(
        mean=1.0, spread=0.1, frac=0.4, frac_spread=0.05, has=True,
        n_above=3.5, n_above_spread=0.5, n_above_total=None,
        replicate_unit=_replicate_unit_for(solo, per_fov=True),
        ch="gfp", metric="mean_intensity", tp_str="0",
        threshold=0.0, band_lbl="SEM")
    assert row["n_above_replicate_mean"] == "3.500000"
    assert row["err_n_above_replicate_SEM"] == "0.500000"
    assert row["n_above_replicate_unit"] == "fov"


def test_the_bar_row_still_leaves_a_lone_well_empty_without_the_toggle():
    """The documented case must not move: one well is not a replicate
    aggregation, and a 0.000000 there would read as replicates that agreed."""
    from well_viewer.features.export.service import (
        _replicate_unit_for, bar_metric_row,
    )

    class _Item:
        def __init__(self, n_above, total):
            self.n_above, self.n_above_total = n_above, total

    solo = _Item(7, None)
    assert _replicate_unit_for(solo, per_fov=False) == ""
    row = bar_metric_row(
        mean=1.0, spread=0.1, frac=0.4, frac_spread=0.05, has=True,
        n_above=7, n_above_spread=0.0, n_above_total=None,
        replicate_unit=_replicate_unit_for(solo, per_fov=False),
        ch="gfp", metric="mean_intensity", tp_str="0",
        threshold=0.0, band_lbl="SEM")
    assert row["err_n_above_replicate_SEM"] == ""
    assert row["n_above_replicate_unit"] == ""


# ── the scatter aggregate's whiskers, on the tab and in the batch figure ─────

def test_a_spread_that_was_not_computed_draws_no_whisker():
    """``capsize`` draws a cap at every point even where the bar has zero
    length, so the batch figure wore a cross at the centre of markers the tab
    left bare. Both figures ask the same function now."""
    from well_viewer.features.scatter.controller import whiskers_or_none

    assert whiskers_or_none([float("nan")]) is None
    assert whiskers_or_none([0.0]) is None
    assert whiskers_or_none([]) is None
    assert whiskers_or_none(None) is None
    assert whiskers_or_none([0.0, 1.5]) == [0.0, 1.5]


# ── the scatter aggregate's own file, written for real ──────────────────────

def _agg_export(tmp_path, monkeypatch, *, x_err, y_err, per_fov=False,
                use_sem=True):
    """Run ``export_scatter_agg_data`` over stubbed collection and read it back.

    The collector and the two stat combos are the tab's; what is under test is
    the writer, so those are replaced and everything from the row dicts to the
    header comes from the real function. Nothing here was covered by a test
    before — the whole exporter had none, which is how a ``nan`` cell survived.
    """
    import csv
    import io
    import types

    from well_viewer.features.export import service as EX

    out = tmp_path / "scatter_agg.csv"
    scatter_data = {
        "A01_tp0.0": {
            "x": [10.0], "y": [20.0], "x_err": [x_err], "y_err": [y_err],
            "timepoint": 0.0, "label": "A01 (t=0h)", "color": "#000",
        },
    }
    monkeypatch.setattr(EX, "_scatter_sync_agg_stat_combos", lambda _c: None)
    monkeypatch.setattr(EX, "_scatter_collect_agg_data",
                        lambda *_a, **_k: scatter_data)
    monkeypatch.setattr(EX, "ask_save_csv", lambda *_a, **_k: str(out))
    monkeypatch.setattr(EX, "figure_names_map", lambda _c: {})

    combo_x = types.SimpleNamespace(currentText=lambda: "Mean Fluorescence gfp")
    combo_y = types.SimpleNamespace(currentText=lambda: "Fraction On gfp")
    ctx = types.SimpleNamespace(
        window=None,
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths={"A01": "A01"}),
            plot=types.SimpleNamespace(use_sem=use_sem,
                                       use_fov_replicates=per_fov),
        ),
        runtime=types.SimpleNamespace(scatter=types.SimpleNamespace(
            agg_tp_checks={"0.0": types.SimpleNamespace(isChecked=lambda: True)},
        )),
        widgets=types.SimpleNamespace(get_or_none={
            "_scatter_agg_stat_x_cb": combo_x,
            "_scatter_agg_stat_y_cb": combo_y,
        }.get),
        notify=types.SimpleNamespace(set_status=lambda _m: None),
    )
    EX.export_scatter_agg_data(ctx)
    assert out.exists(), "the aggregate exporter wrote no file"
    return list(csv.DictReader(io.StringIO(out.read_text())))


def test_the_scatter_aggregate_blanks_an_undefined_error(tmp_path, monkeypatch):
    """One well is one replicate, so ``_reduce_reps`` returns NaN — and NaN
    reached this file as the word ``nan`` on the tab and in both batch jobs."""
    rows = _agg_export(tmp_path, monkeypatch,
                       x_err=float("nan"), y_err=float("nan"))
    assert len(rows) == 1
    row = rows[0]
    err_cols = [c for c in row if c.endswith("_error")]
    assert len(err_cols) == 2, sorted(row)
    assert all(row[c] == "" for c in err_cols), row


def test_the_scatter_aggregate_keeps_a_measured_error(tmp_path, monkeypatch):
    rows = _agg_export(tmp_path, monkeypatch, x_err=4.0, y_err=0.0)
    row = rows[0]
    x_col = next(c for c in row if c.endswith("_error") and "fraction" not in c.lower())
    assert row[x_col] == "4.000000"
    # A measured zero survives: the replicates agreed.
    y_col = next(c for c in row if c.endswith("_error") and c != x_col)
    assert row[y_col] == "0.000000"


def test_the_scatter_aggregate_says_which_interval_and_over_what(tmp_path,
                                                                 monkeypatch):
    """Neither the figure nor the file said whether ``x_err`` was an SD or a
    SEM, or whether it was taken over wells or over fields."""
    rows = _agg_export(tmp_path, monkeypatch, x_err=4.0, y_err=1.0)
    assert rows[0]["spread_kind"] == "SEM"
    assert rows[0]["spread_unit"] == "well"

    sd_fov = _agg_export(tmp_path, monkeypatch, x_err=4.0, y_err=1.0,
                         per_fov=True, use_sem=False)
    assert sd_fov[0]["spread_kind"] == "SD"
    assert sd_fov[0]["spread_unit"] == "fov"


# ── what a row with no data may say ─────────────────────────────────────────

def test_a_bar_that_measured_nothing_names_no_unit_and_no_spread():
    """A member with no point at this timepoint still reaches the writer.

    ``collect_bar_items``'s no-data branch fills the item with placeholders —
    ``n_above=0.0``, ``n_above_spread=0.0``, ``has_mean=False`` — so a unit
    keyed on the *value* answered "fov" for a zero nobody measured, and the
    spread cell, which keys on the unit, then printed ``0.000000`` on a row
    whose every other cell is blank. That is the fabricated whisker this file
    exists to prevent, and it arrived by way of its own fix.
    """
    from well_viewer.features.barplot.collect import BarItem
    from well_viewer.features.export.service import (
        _replicate_unit_for, bar_metric_row,
    )

    absent = BarItem(
        key="B01", display="B01", color="#000", tp="0",
        mean=float("nan"), spread=0.0, has_mean=False,
        frac=float("nan"), frac_spread=0.0, has_frac=False,
        n_above=0.0, n_above_spread=0.0, n_above_total=None)
    assert _replicate_unit_for(absent, per_fov=True) == ""
    row = bar_metric_row(
        mean=absent.mean, spread=absent.spread, frac=absent.frac,
        frac_spread=absent.frac_spread, has=absent.has_mean,
        n_above=float(absent.n_above), n_above_spread=float(absent.n_above_spread),
        n_above_total=absent.n_above_total,
        replicate_unit=_replicate_unit_for(absent, per_fov=True),
        ch="gfp", metric="mean_intensity", tp_str="0",
        threshold=0.0, band_lbl="SEM")
    assert row["err_n_above_replicate_SEM"] == ""
    assert row["err_n_above_SEM"] == ""
    assert row["n_above_replicate_unit"] == ""
    assert row["n_above_replicate_mean"] == ""
    assert row["n_above_threshold"] == ""


def test_the_two_events_error_columns_agree_about_a_measured_zero():
    """They hold the same statistic — the collectors put the drawn Events
    spread in ``n_above_spread`` on both branches — so they cannot disagree
    about whether zero is a measurement. ``err_n_above_<band>`` keyed on
    ``spread > 0``, a mode flag standing in for a value test, and blanked the
    zero its neighbour wrote."""
    from well_viewer.features.export.service import bar_metric_row

    row = bar_metric_row(
        mean=1.0, spread=0.1, frac=0.5, frac_spread=0.0, has=True,
        n_above=6.0, n_above_spread=0.0, n_above_total=12,
        replicate_unit="fov", ch="gfp", metric="mean_intensity",
        tp_str="0", threshold=0.0, band_lbl="SEM")
    assert row["err_n_above_SEM"] == "0.000000"
    assert row["err_n_above_replicate_SEM"] == "0.000000"


# ── the fold-change columns ─────────────────────────────────────────────────

def test_the_bar_fold_change_spread_is_blank_when_it_is_undefined():
    """``fc_has`` is the MEAN's flag. ``scale_bar_value`` returns a finite
    value with a NaN spread whenever the denominator's own spread was
    undefined, so gating the spread on it wrote the literal ``nan`` — in the
    same row where ``err_<band>_<ch>_<metric>`` was correctly blank, and
    against a Line CSV that blanks the identical cell."""
    from well_viewer.features.export.service import attach_bar_fold_change_columns

    row: dict = {}
    attach_bar_fold_change_columns(
        row, fc_mean=1.0, fc_spread=float("nan"), fc_has=True, band_lbl="SEM",
        vs_control=True, vs_t0=False, control_label="A01")
    assert row["fold_change_mean"] == "1.000000"
    assert row["fold_change_sem"] == ""

    # An infinity is not a measurement either — a control that vanishes can
    # produce one, and ``inf`` in a cell is as unusable as ``nan``.
    infinite: dict = {}
    attach_bar_fold_change_columns(
        infinite, fc_mean=float("inf"), fc_spread=float("inf"), fc_has=True,
        band_lbl="SEM", vs_control=True, vs_t0=False, control_label="A01")
    assert infinite["fold_change_mean"] == ""
    assert infinite["fold_change_sem"] == ""

    # A measured zero still exports.
    agreed: dict = {}
    attach_bar_fold_change_columns(
        agreed, fc_mean=1.25, fc_spread=0.0, fc_has=True, band_lbl="SEM",
        vs_control=True, vs_t0=False, control_label="A01")
    assert agreed["fold_change_sem"] == "0.000000"


def test_a_blanked_fold_change_point_reports_no_spread_either():
    """``fold_change.blank_point`` blanks the mean and leaves the spread at
    0.0, so guarding the two independently wrote ``0.000000`` beside an empty
    ``fold_change_mean`` on exactly the rows whose outcome says the control
    never resolved. Both line writers, on-tab and batch."""
    from well_viewer.features.batch_export.base_panel import _attach_fc_row

    row: dict = {}
    blanked = (0.0, float("nan"), 0.0, float("nan"), 0.0, 0)
    _attach_fc_row(row, blanked, "SEM", True, False, "A01", "control_unresolved")
    assert row["fold_change_mean"] == ""
    assert row["fold_change_sem"] == ""
    assert row["fold_change_outcome"] == "control_unresolved"
