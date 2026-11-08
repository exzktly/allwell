"""Every figure the Properties panel opens on says which of its controls it can honour — and says it truthfully.

The panel offers a control only where the figure it is bound to has the style facet that control changes
(``review/domain/style_facets.py``). That decision is only as good as the declarations behind it, and each way a
declaration can go wrong fails quietly: a figure with no declaration is offered every control again, a control built
outside the vocabulary is offered everywhere, and a declaration that claims a renderer reads a field it never reads
takes nothing away while one that denies a field its renderer does read takes a working control away. So:

* **Every panel figure is declared, once.** The figures are read from ``open_export_style_panel``'s roster by its
  shape (a dict of ``(fig, canvas, name)`` tuples, as ``test_properties_panel_redraws`` reads it), and held against
  the registry's ``style_facets()``, which raises on a figure declared twice.
* **Each declaration is total and gives its reasons** (``FigureFacets.problems``).
* **The panel speaks only the vocabulary, and all of it.** Every section names a facet, every facet literal the panel
  names is in ``FACETS``, and every facet in ``FACETS`` gates at least one control.
* **A PlotState facet is declared where its field is read.** For the facets whose controls write a ``PlotState``
  field the renderer reads, the feature packages that name that field (or the helper that reads it) are exactly the
  packages that declare the facet. Package-level: the Scatter package draws both scatter figures, so it is checked
  as one — the per-cell scatter's absences there rest on their written reasons.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from review.domain.style_facets import FACETS
from tests._repo_scan import REPO_ROOT, parse_source

_PANEL_MODULE = REPO_ROOT / "review/features/export/style.py"
_SIDEBAR_MODULE = REPO_ROOT / "review/features/export/properties_view.py"
_FEATURES = REPO_ROOT / "review/features"

#: The facets whose controls write a field the renderer reads off ``PlotState``, with the names a feature package
#: uses to read it — the field itself, or the one helper every reader goes through.
_READ_BY = {
    "bar_show_mode": {"bar_show_mode"},
    "group_labels": {"group_label_size"},
    "threshold_line": {"show_threshold_line"},
    "value_labels": {"show_value_labels", "value_label_settings"},
    "time_axis": {"time_scale", "time_offset", "rescale_for", "apply_time_rescale"},
    "error_bars": {"use_sem", "bar_err_kind", "error_bar_settings"},
    # None and 95% CI are read only through ``error_bar_settings``; ``use_sem`` alone draws SEM or SD.
    "error_kinds": {"error_bar_settings"},
    "beeswarm": {"swarm_spacing", "swarm_spacing_of"},
    "bar_geometry": {"bar_width", "bar_group_spacing"},
    "colour_mode": {"bar_color_mode", "line_color_mode", "response_color_mode"},
}


def _panel_figures() -> set[str]:
    tree = parse_source(_PANEL_MODULE)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "open_export_style_panel":
            for node in ast.walk(fn):
                if (isinstance(node, ast.Dict) and node.keys
                        and all(isinstance(v, ast.Tuple) and len(v.elts) == 3 for v in node.values)):
                    return {v.elts[0].value for v in node.values if isinstance(v.elts[0], ast.Constant)}
    raise AssertionError("no (fig, canvas, name) roster in open_export_style_panel — this guard measures nothing")


def _declaring_modules() -> dict[str, dict]:
    """``{package: {figure: FigureFacets}}`` for every ``<feature>/_style_facets.py``."""
    out = {}
    for path in sorted(_FEATURES.glob("*/_style_facets.py")):
        module = importlib.import_module(f"review.features.{path.parent.name}._style_facets")
        out[path.parent.name] = module.style_facets()
    return out


def _registry_declarations() -> dict:
    from review.features.registry import build_feature_registry

    return build_feature_registry().style_facets()


def test_every_figure_the_panel_opens_on_is_declared_once():
    declared = _registry_declarations()
    panel = _panel_figures()
    assert len(panel) >= 10, panel
    assert set(declared) == panel, (
        f"undeclared: {sorted(panel - set(declared))}; declared but never opened: {sorted(set(declared) - panel)}")


def test_every_declaring_module_reaches_the_registry():
    by_module = {fig for figs in _declaring_modules().values() for fig in figs}
    assert by_module == set(_registry_declarations()), (
        "a <feature>/_style_facets.py is not wired to its FeatureDescriptor.style_facets, or one is wired twice")


@pytest.mark.parametrize("figure", sorted(_panel_figures()))
def test_each_declaration_is_total_and_gives_its_reasons(figure):
    facets = _registry_declarations()[figure]
    assert facets.problems() == [], f"{figure}: {facets.problems()}"


def test_the_nanowell_views_named_are_the_page_s_views():
    from review.features.nanowell.state import VIEWS

    facets = _registry_declarations()["_nw_fig"]
    assert set(facets.views) <= set(VIEWS)
    assert facets.default_view in VIEWS


def _facet_literals(node) -> list[str]:
    """The strings *node* can evaluate to: a literal, or either arm of a conditional (never its test)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _facet_literals(node.body) + _facet_literals(node.orelse)
    return []


def _panel_facets() -> tuple[set[str], list[int]]:
    """The facet literals the sidebar names, and the lines of any section built without one."""
    named: set[str] = set()
    unnamed: list[int] = []
    for node in ast.walk(parse_source(_SIDEBAR_MODULE)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "_supports" and node.args:
            named.update(_facet_literals(node.args[0]))
        if name in ("section", "add_row", "add_full", "add_stacked"):
            kw = [k for k in node.keywords if k.arg == "facet"]
            if kw:
                named.update(_facet_literals(kw[0].value))
            elif name == "section":
                unnamed.append(node.lineno)
    return named, unnamed


def test_every_section_names_a_facet_and_every_facet_gates_a_control():
    named, unnamed = _panel_facets()
    assert unnamed == [], f"Properties sections built without a facet at lines {unnamed}"
    assert named - set(FACETS) == set(), f"facets the vocabulary does not have: {sorted(named - set(FACETS))}"
    assert set(FACETS) - named == set(), f"facets that gate no control: {sorted(set(FACETS) - named)}"


def _names_in_package(package: str) -> set[str]:
    names: set[str] = set()
    for path in (_FEATURES / package).rglob("*.py"):
        if path.name == "_style_facets.py":
            continue
        for node in ast.walk(parse_source(path)):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name.rsplit(".", 1)[-1])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
    return names


@pytest.mark.parametrize("facet", sorted(_READ_BY))
def test_a_plotstate_facet_is_declared_exactly_where_its_field_is_read(facet):
    readers, declarers = set(), set()
    for package, figures in _declaring_modules().items():
        if _names_in_package(package) & _READ_BY[facet]:
            readers.add(package)
        if any(facet in f.has for f in figures.values()):
            declarers.add(package)
    assert declarers == readers, (
        f"{facet}: declared by {sorted(declarers)} but read by {sorted(readers)} — a control offered where nothing "
        f"reads it does nothing, and one withheld where something does is a working control taken away")


def test_the_reader_oracle_sees_the_packages():
    """The oracle above is only as good as its scan: it must find the bar package reading ``bar_show_mode``."""
    assert "bar_show_mode" in _names_in_package("barplot")
    assert Path(_FEATURES / "barplot").is_dir()
