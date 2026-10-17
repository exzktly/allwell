"""A module belongs to the narrowest scope that contains every one of its users.

This is the rule the last three structural defects in this repository all broke,
and it is the rule that makes the others decidable. `theme.py` sat at the root
while only widgets read it. Five infrastructure modules sat inside the Review
pane while all three panes read them. What is left after those two sweeps is the
same shape once more: modules inside one pane that another pane imports.

Stated as a measurement rather than a taste: **if a module inside pane P is
imported from outside P, it is not P's module.** Either it is shared — in which
case it belongs in a root-level package, where the arrow runs one way from panes
into shared code — or the importer should not have reached for it. Both readings
are worth a decision; neither is served by leaving it where it is.

Nothing enforces this by accident. An import resolves identically wherever the
file sits, so a misplaced module costs nothing until someone has to work out who
owns what, and by then a dozen call sites have made the placement look
deliberate. That is why the drift is one-way and why this is a test.

The roster below is the exemption list, and an entry is a *decision with a
reason*, never an absence. A module left inside a pane on purpose and one nobody
got round to moving are indistinguishable in a directory listing six months
later; the reason is the only thing that tells them apart.
"""
from __future__ import annotations

import ast
from collections import defaultdict

from tests._repo_scan import REPO_ROOT, parse_source, python_sources

#: The three panes. A module under one of these is that pane's unless the
#: roster below says why it is not.
PANES = ("review", "analyze", "datasets")

#: Each pane's entry point — the one module `allwell.py` is entitled to import
#: in order to build the window. The shell has to reach *something* in each pane;
#: naming which module keeps that from becoming a licence to reach anything.
#:
#: Everything else the shell touches is a crossing like any other and needs a
#: reason in ALLOWED below, which is what makes the shell's remaining reaches
#: into pane internals visible as the debt they are rather than as plumbing.
PANE_ENTRY_POINTS = {
    "review/__init__.py": "ReviewApp, via the package's lazy __getattr__",
    "analyze/tab.py": "AnalyzeTab",
    "datasets/tab.py": "DatasetsTab",
}

#: Modules inside a pane that another part of the application imports anyway,
#: each with the measurement that justifies leaving it there.
#:
#: The test for an entry is not "is it used by two panes" — that is what puts it
#: on this list. The test is "what would moving it cost": a module that carries
#: its pane's internals with it cannot be lifted without lifting them too, and a
#: root package that reaches back down into a pane is worse than the misplacement
#: it was meant to fix.
ALLOWED: dict[str, str] = {
    "review/core/tab_controller.py":
        "The title bar asks `current_centre_tab` which Review leaf is showing, "
        "to pick the help topic for it — a question only Review can answer "
        "about itself. The module reaches deep into Review's internals, the "
        "fold-change scopes and the persistence layer among them, so it cannot "
        "travel; the roster of panes that used to sit beside it did, to "
        "`core/panes.py`.",
    "review/core/phone_drawer.py":
        "Phone layout's side drawer lends Review's rail and the other two "
        "panes' left panels to one overlay and gives them back; the title bar "
        "builds it with the rest of phone layout (`_build_phone_shell`) to "
        "light its ☰ button when the drawer opens or closes from inside. It "
        "moves Review's own widgets in and out of Review's splitters and is "
        "driven from `tab_controller.apply_sidebar_visibility`, so it cannot "
        "travel without them.",
    "review/core/reset_scopes_view.py":
        "The title bar's two reset actions (`prompt_reset_view`, "
        "`prompt_reset_analysis`) live here because the scopes they reset are "
        "Review's, and the prompt is about Review's state. Reached from the "
        "shell rather than from another pane.",
    "review/core/recipe_view.py":
        "The title bar's copy-analysis action (`prompt_copy_analysis`) lives "
        "here for the reason the reset actions beside it do: what it copies is "
        "Review's analysis state, read and written through Review's persistence "
        "layer, and the dialog is about that state. Reached from the shell "
        "rather than from another pane.",
    "review/core/plot_card_actions.py":
        "The title bar reads `PLOT_CARD_ATTRS` to decide which menu entries to "
        "enable. Plotting is Review's subject outright, and the module reaches "
        "the plot orchestrator to do it.",
    "review/core/ui_helpers.py":
        "`allwell.py` calls `commit_focused_notes` to make the Review pane "
        "flush an in-progress edit before a save. That is the shell asking a "
        "pane to do something, not shared infrastructure the pane happens to "
        "hold.",
    "review/features/home/controller.py":
        "The title bar asks `current_help_subject` which help topic the open "
        "Home page maps to — a question only Home can answer about itself.",
    "review/features/barplot/renderer.py":
        "Batch Export re-renders a bar plot off-screen through `redraw_bars`. "
        "The renderer is the one definition of what a bar plot looks like and "
        "belongs with the feature that owns it.",
    "review/runtime_app.py":
        "`teardown_review_pane` is the pane's own shutdown, called by the shell "
        "that built it.",
}


def _internal_import_count(rel: str) -> int:
    """How many of its own pane's modules this file imports.

    The number that decides whether a module can travel or only its symbols
    can, reported at the moment someone needs it rather than typed into the
    roster — two of the counts written there by hand had drifted within a day
    of being written, because the symbol moves this branch made changed them.
    """
    pane = _pane_of(rel)
    if pane is None:
        return 0
    tree = parse_source(REPO_ROOT / rel)
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        and node.module.split(".")[0] == pane
    )


def _pane_of(rel: str) -> str | None:
    top = rel.split("/")[0]
    return top if top in PANES else None


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every dotted module name this file imports, absolute forms only."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _module_names_for(rel: str) -> tuple[str, ...]:
    """The dotted names that reach the file at repo-relative path *rel*."""
    dotted = rel[:-3].replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return (dotted,)


def test_no_module_inside_a_pane_is_imported_from_outside_it():
    users: dict[str, set[str]] = defaultdict(set)

    # Map every dotted module name back to its repo-relative path, so an import
    # can be resolved to the file it reaches without importing anything.
    by_dotted: dict[str, str] = {}
    for path in python_sources():
        rel = str(path.relative_to(REPO_ROOT))
        if _pane_of(rel) is None:
            continue
        for dotted in _module_names_for(rel):
            by_dotted[dotted] = rel

    for path in python_sources():
        rel = str(path.relative_to(REPO_ROOT))
        importer_pane = _pane_of(rel)
        for dotted in _imported_modules(parse_source(path)):
            target = by_dotted.get(dotted)
            if target is None:
                continue
            target_pane = _pane_of(target)
            if target_pane is not None and target_pane != importer_pane:
                users[target].add(rel)

    # The shell may import each pane's declared entry point, and only that.
    for entry in PANE_ENTRY_POINTS:
        if users.get(entry) == {"allwell.py"}:
            users.pop(entry)

    offenders = sorted(set(users) - set(ALLOWED))
    lines = [
        f"{t}  ← imported by {', '.join(sorted(users[t]))}"
        f"  [it makes {_internal_import_count(t)} imports of its own pane]"
        for t in offenders
    ]
    assert not offenders, (
        "these modules sit inside one pane while something outside that pane "
        "imports them:\n  " + "\n  ".join(lines) + "\n\n"
        "A module belongs to the narrowest scope that holds all its users. Move "
        "each into the root-level package that fits what it is — `domain/` for "
        "Qt-free models and pure logic, `core/` for app infrastructure, `ui/` "
        "for shared UI, `widgets/` for a Qt widget with no All-Well knowledge — "
        "or, if it genuinely cannot travel, add it to ALLOWED above with the "
        "measurement that says why.")


def test_the_exemption_roster_names_nothing_that_has_gone():
    """An entry for a file that moved is a standing permission under a dead name."""
    stale = sorted(p for p in ALLOWED if not (REPO_ROOT / p).is_file())
    assert not stale, (
        "ALLOWED names files that are not there, so these entries permit "
        f"nothing and describe nothing: {', '.join(stale)}. Delete each one.")


def test_every_exemption_is_still_reached_from_outside_its_pane():
    """An exemption that no longer describes a real crossing is dead weight.

    Without this the roster only ever grows: an entry whose importer moved away
    stays as a silent permission, and the next module that lands at that path
    inherits it.
    """
    reached: set[str] = set()
    by_dotted = {}
    for path in python_sources():
        rel = str(path.relative_to(REPO_ROOT))
        if _pane_of(rel) is not None:
            by_dotted[_module_names_for(rel)[0]] = rel
    for path in python_sources():
        rel = str(path.relative_to(REPO_ROOT))
        for dotted in _imported_modules(parse_source(path)):
            target = by_dotted.get(dotted)
            if target and _pane_of(target) != _pane_of(rel):
                reached.add(target)

    for entry in PANE_ENTRY_POINTS:
        reached.discard(entry)

    unused = sorted(set(ALLOWED) - reached)
    assert not unused, (
        "these ALLOWED entries no longer describe a crossing — nothing outside "
        f"their pane imports them any more: {', '.join(unused)}. Delete each "
        "entry; the module is simply its pane's now.")


def test_every_declared_pane_entry_point_exists():
    """A roster entry naming a file that has gone is a permission for a dead name."""
    stale = sorted(p for p in PANE_ENTRY_POINTS if not (REPO_ROOT / p).is_file())
    assert not stale, (
        "PANE_ENTRY_POINTS names files that are not there: "
        f"{', '.join(stale)}. A pane's entry point moved and this roster did "
        "not follow, so the shell's import of it now reads as a violation "
        "while a departed path keeps a standing permission.")
