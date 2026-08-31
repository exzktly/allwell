"""Each way of declaring what a well means says what it is, and what it does.

Home's definition tabs are siblings that all point at wells, and until the role
strip existed none of them said which one changed a number. This drives the
three tabs through their **real builders** and reads what is on them, because
the failure this guards against is not a wrong sentence — it is a strip that is
never built, or built for two tabs of three, and both of those pass any test
that asserts the roster's contents on their own.

The second half is the substitution nobody could see:
``replicate_display_label`` replaces an auto-generated group name with its
member well labels, so a label typed on the Well Labels tab silently becomes a
group's identity on every figure. That is the one place two of the four
declarations are genuinely wired together, and the note on the Groups tab is
where it is said. Asserted **at the widget**, per §7.4 #1 — the first version of
a readout like this checked that a sentence had been composed and passed
against a controller that had stopped painting it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from well_viewer.features.home import collect as hc


class _Registry:
    def __init__(self):
        self._d = {}

    def register(self, name, widget):
        self._d[name] = widget
        return widget

    def get_or_none(self, name):
        return self._d.get(name)

    def has(self, name):
        return name in self._d


def _wells(rows="AB", cols=(1, 2, 3, 4)):
    return {f"{r}{c:02d}": f"/data/{r}{c:02d}.csv" for r in rows for c in cols}


def _ctx(wells=None, selections=(), labels=None):
    from well_viewer.core.selection_state import SelectionState

    selection = SelectionState()
    selection.selections = [dict(s) for s in selections]
    return SimpleNamespace(
        widgets=_Registry(),
        window=SimpleNamespace(),
        runtime=SimpleNamespace(groups=SimpleNamespace(inline_edit_idx=-1)),
        state=SimpleNamespace(
            selection=selection,
            dataset=SimpleNamespace(well_paths=wells if wells is not None else _wells(),
                                    well_labels=dict(labels or {})),
        ),
        persistence=SimpleNamespace(schedule_save=lambda *a, **k: None),
    )


def _texts(root: QWidget) -> list[str]:
    return [w.text() for w in root.findChildren(QLabel)]


# ── the strip is on every tab that declares a role ──────────────────────────

_BUILDERS = {
    "Groups": ("well_viewer.features.home.groups.tab_view",
               "build_replicate_groups_centre"),
    "Variables": ("well_viewer.features.home.variables.tab_view",
                  "build_variables_subtab"),
    "Well Labels": ("well_viewer.features.home.labels.tab_view",
                    "build_label_grid"),
}


def _build(tab_name, qtbot, ctx=None):
    import importlib

    module, func = _BUILDERS[tab_name]
    ctx = ctx if ctx is not None else _ctx()
    frame = QWidget()
    qtbot.addWidget(frame)
    getattr(importlib.import_module(module), func)(ctx, frame)
    return ctx, frame


@pytest.mark.parametrize("tab_name", sorted(_BUILDERS))
def test_every_definition_tab_states_its_role_and_its_sibling(tab_name, qtbot):
    """The real builder, not the strip helper — a strip nothing calls is a
    strip that ships invisible, which is what §7.6 is about."""
    role = hc.role_for_tab(tab_name)
    assert role is not None, f"{tab_name} declares no role in DECLARATION_ROLES"
    _ctx_, frame = _build(tab_name, qtbot)
    texts = _texts(frame)
    assert role.role in texts, (
        f"the {tab_name} tab never says what it is. Rendered: {texts[:6]}")
    assert role.cross_link in texts, (
        f"the {tab_name} tab never points at the sibling answering the other "
        f"question. Rendered: {texts[:6]}")


def test_the_roster_and_the_builders_name_the_same_tabs():
    """A role for a tab nobody builds is copy nobody reads, and the reverse is
    a tab with no statement of what it is for."""
    declared = {r.tab for r in hc.DECLARATION_ROLES if r.tab}
    assert declared == set(_BUILDERS)


@pytest.mark.parametrize("tab_name", ["Cell Gating", "Ratios", "Pipeline Info"])
def test_a_tab_outside_the_taxonomy_gets_no_strip(tab_name):
    """A gate is a threshold and a ratio is a derived channel. Neither says what
    a well *is*, so neither grows a blank band claiming to."""
    assert hc.role_for_tab(tab_name) is None


def test_every_role_says_whether_it_pools():
    """The effect column is the distinction the four were confused on, so an
    empty one is a role that has not been thought through."""
    for role in hc.DECLARATION_ROLES:
        assert role.effect.strip(), f"{role.key} states no effect"
        assert role.question.strip().endswith("?"), (
            f"{role.key}'s question is not one: {role.question!r}")


# ── Overview reports all four, with what each does ──────────────────────────

def test_your_work_reports_variables_with_their_count():
    from well_viewer.domain.sample_variables import VariableSet, new_variable

    ctx = _ctx()
    vset = VariableSet()
    # ``VariableSet`` is immutable; ``new_variable`` returns the new set. A
    # fixture that called it for its side effect would build an empty set and
    # then assert the count it happened to produce.
    for name in ("Cell line", "Dose"):
        vset, _id = new_variable(vset, name)
    ctx.state.selection.variables = vset
    ctx.state.channel = SimpleNamespace(ratio_metrics=[])
    ctx.state.dataset.pipeline_info = {}
    rows = {r["key"]: r for r in hc.your_work(ctx)}
    assert "variables" in rows, (
        "Overview reports groups, labels and ratios and stays silent about the "
        "annotation the plots are pooling by")
    assert rows["variables"]["count"] == 2
    assert rows["variables"]["effect"] == "Pools, colours, splits"


def test_every_declaration_row_carries_its_effect():
    """Not "some row has an effect" — each of the four, by key, with the exact
    string. A row whose effect went missing is a tally with no meaning beside
    three that have one."""
    ctx = _ctx()
    ctx.state.channel = SimpleNamespace(ratio_metrics=[])
    ctx.state.dataset.pipeline_info = {}
    rows = {r["key"]: r for r in hc.your_work(ctx)}
    for role in hc.DECLARATION_ROLES:
        assert rows[role.key]["effect"] == role.effect, role.key
    # And the rows that are not declarations carry none, so the column means
    # one thing: a smFISH threshold is a setting, not a way of naming wells.
    assert not rows["ratios"].get("effect")
    assert not rows["notes"].get("effect")


def test_the_work_row_paints_its_effect_beside_the_count(qtbot):
    """At the widget the painter produces, not at the dict it is handed.

    ``your_work`` carrying an ``effect`` key and the row *drawing* it are two
    different facts, and the second is the one a user can see. This calls the
    real row builder, which is what Overview calls for every line of the block.
    """
    from well_viewer.features.home.controller import _row_widget

    parent = QWidget()
    qtbot.addWidget(parent)
    row = _row_widget(parent, {"label": "Variables", "count": 2,
                               "effect": "Pools, colours, splits"}, kind="work")
    assert "Pools, colours, splits" in _texts(row)
    assert "2" in _texts(row)
    bare = _row_widget(parent, {"label": "Notes", "count": 0}, kind="work")
    assert _texts(bare) == ["Notes", "None yet"], (
        "a row with no effect must draw no chip — an empty one would put a "
        "blank column beside three filled ones")


# ── the label → group-name substitution is said out loud ────────────────────

def _group(name, wells, **extra):
    return {"id": name, "name": name, "wells": list(wells), "hidden": False,
            "color": "#5B9BF8", "replicates": None, "source": "user", **extra}


def test_the_clause_names_the_exact_substitution():
    """The value, not the shape of it. "A sentence was produced" is satisfied
    by a sentence naming the wrong group."""
    from well_viewer.domain.selections_model import relabelled_groups_clause

    ctx = _ctx(selections=[_group("1", ["A01", "A02"])],
               labels={"A01": "shCtrl", "A02": "shA"})
    assert relabelled_groups_clause(ctx) == (
        "1 group has a name that looks auto-generated, so figures label them "
        "with their wells instead: “1” → “shCtrl,shA”. "
        "Rename a group to choose its own label."
    )


def test_a_group_the_user_named_is_not_reported():
    """The clause exists to explain a substitution. A group named ``Control``
    keeps its name on the axis, so saying anything about it is noise."""
    from well_viewer.domain.selections_model import relabelled_groups_clause

    ctx = _ctx(selections=[_group("Control", ["A01", "A02"])],
               labels={"A01": "shCtrl"})
    assert relabelled_groups_clause(ctx) == ""


def test_a_hidden_group_is_not_reported():
    """Nothing draws it, so nothing relabels it."""
    from well_viewer.domain.selections_model import relabelled_groups_clause

    ctx = _ctx(selections=[_group("1", ["A01", "A02"], hidden=True)],
               labels={"A01": "shCtrl"})
    assert relabelled_groups_clause(ctx) == ""


def test_the_clause_counts_the_ones_it_does_not_name():
    from well_viewer.domain.selections_model import relabelled_groups_clause

    # Five groups over wells the dataset actually holds — a group whose only
    # well is not loaded is not drawn, so a fixture reaching past A04 would be
    # counting four and asserting five.
    ctx = _ctx(wells=_wells(rows="AB", cols=(1, 2, 3)),
               selections=[_group("1", ["A01"]), _group("2", ["A02"]),
                           _group("3", ["A03"]), _group("4", ["B01"]),
                           _group("5", ["B02"])])
    clause = relabelled_groups_clause(ctx)
    assert "5 groups have names that look auto-generated" in clause, clause
    assert ", and 2 more" in clause, clause


def test_the_groups_tab_shows_the_clause_on_its_own_label(qtbot):
    """At the widget. A test that read the clause function would pass against a
    tab that had stopped painting it — which is the defect this whole note is
    about, one layer up."""
    from well_viewer.features.home.groups.selections_view import rep_panel_refresh

    ctx = _ctx(selections=[_group("1", ["A01", "A02"])],
               labels={"A01": "shCtrl", "A02": "shA"})
    _c, frame = _build("Groups", qtbot, ctx=ctx)
    note = ctx.widgets.get_or_none("_rep_relabel_note")
    assert note is not None, "the Groups tab has no relabel note at all"
    rep_panel_refresh(ctx)
    assert "“shCtrl,shA”" in note.text()
    assert note.isVisibleTo(frame)


def test_the_note_stays_off_when_nothing_is_relabelled(qtbot):
    from well_viewer.features.home.groups.selections_view import rep_panel_refresh

    ctx = _ctx(selections=[_group("Control", ["A01", "A02"])])
    _c, frame = _build("Groups", qtbot, ctx=ctx)
    rep_panel_refresh(ctx)
    note = ctx.widgets.get_or_none("_rep_relabel_note")
    assert note.text() == ""
    assert not note.isVisibleTo(frame)
