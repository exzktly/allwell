"""Phone layout stays in the layers that are entitled to know about it.

``_Docs/Markdowns/PHONE_FORM_TRACKER.md`` §2.2 names five layers and says which
may read the phone-layout flag. Three rules hold that table in place, and a
fourth is added by the rows that follow:

1. **Data and drawing never read the mode** (P0.2). A renderer, a collector, a
   ``*_compute.py`` or anything under ``domain/``, ``services/``, ``pipeline/``
   or ``util/`` receives its sizes as arguments. A bare ``if is_on():`` there is
   how a second mode spreads through code that should not know there is one.
2. **Reads of the mode outside the sanctioned layers only go down** (P0.4). The
   sanctioned layers are ``ui/``, ``core/``, the ``*_phone.py`` /
   ``*phone_view.py`` siblings and ``review/core/phone_panel_sheet.py``. A read
   anywhere else is on a roster keyed by *file and function* with a written
   reason, and an entry that no longer describes a real read fails the test, so
   the roster can only shrink.
3. **The shell recognises widgets by what they declare, not by their name**
   (P0.5). ``ui/phone_shell.py`` still finds some widgets by class name, object
   name or a ``draw_idle`` duck type; each of those is counted, the budget only
   goes down, and the migration ends at zero.

Every file is read through ``tests._repo_scan.parse_source`` and the roots come
from ``tests._repo_scan``. The scan functions take their file list as an
argument so ``tests/test_guards_bite.py`` can hand them a synthetic violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._repo_scan import REPO_ROOT, parse_source, python_sources, rel

# ── what each rule scans ────────────────────────────────────────────────────

#: Directories whose modules are data or drawing, whatever they are called.
DATA_AND_DRAWING_ROOTS = ("domain", "services", "pipeline", "util")

#: A file in any other layer is data or drawing if it is named like one.
DATA_AND_DRAWING_NAMES = ("renderer.py", "collect.py")
DATA_AND_DRAWING_SUFFIXES = ("_compute.py",)

#: Root-level modules that are the pipeline.
DATA_AND_DRAWING_ROOT_MODULES = ("process_microscopy.py",)

#: Importing any of these from a data or drawing module is the violation.
#: ``(module, names)``: ``names`` None means the module itself, a tuple means
#: those names imported *from* the module's parent.
_MODE_MODULES = ("core.phone_layout", "widgets.form_registry")
_MODE_PREFIXES = ("ui.phone_",)

#: The calls that read the mode. ``remove_listener`` and ``switching`` are
#: bookkeeping around a read, not reads.
MODE_READS = ("is_on", "add_listener", "is_switching")

#: Where the mode may be read freely: the chrome and the tokens.
SANCTIONED_DIRS = ("ui", "core")
SANCTIONED_SUFFIXES = ("_phone.py", "phone_view.py")
SANCTIONED_FILES = (
    "review/core/phone_panel_sheet.py",
    # The Image Table's phone form, owned by the Image Table: it listens to the
    # mode and refits its own grid. One of the five views that already own
    # their phone form (PHONE_FORM_TRACKER.md §1.1).
    "review/features/image_browser/image_table/_phone_fit.py",
)

#: The one module whose recognition-by-name P0.5 counts.
SHELL = "ui/phone_shell.py"

#: ``ui/phone_shell.py``'s recognition by name today, counted by site. Each row
#: of PHONE_FORM_TRACKER.md that removes a site lowers this in the same commit;
#: P4.4 takes it to zero. Measured: ``_KIND_BY_CLASS`` 1 key,
#: ``_KIND_BY_OBJECT_NAME`` empty, ``"InfoHint"`` compared in 2 places, and the
#: ``draw_idle`` duck type in 4 (``_phone_relevant``, ``_is_canvas``,
#: ``_PhoneEventFilter.eventFilter``, ``PhoneShell._sweep``).
RECOGNISED_BY_NAME_BUDGET = 7


# ── roster of reads outside the sanctioned layers ───────────────────────────

#: ``(file, enclosing function)`` -> why the read is entitled to be there.
#: A reason is written at the site as well (§7.5 #10). An entry with no
#: matching read fails ``test_the_roster_describes_only_real_reads``.
MODE_READ_ROSTER: dict[tuple[str, str], str] = {
    ("allwell.py", "AllWellApp._build_rail_drawer._panels"):
        "the window decides which panel it lends the drawer: Analyze's form is the page, "
        "not a panel, in phone layout. The window owns its own layout. P6.1 decides.",
    ("allwell.py", "AllWellApp._build_rail_drawer._closed_inside"):
        "what a close from inside the drawer means for the sidebar flag; the window owns "
        "both. P6.1 decides.",
    ("allwell.py", "AllWellApp._sync_mode_bar"):
        "the mode bar is the window's own widget and is laid out differently in phone "
        "layout. P6.1 decides.",
    ("allwell.py", "AllWellApp._confirm_close_in_phone_layout"):
        "closing the window mid-run stops a local run in phone layout; the window owns "
        "its close. P6.1 decides.",
    ("allwell.py", "AllWellApp._save_window_state"):
        "the window must not save a phone-shaped geometry as its desk geometry. P6.1 decides.",
    ("review/core/plot_style.py", "_refit_on_resize._on_resize"):
        "refits a figure's titles when the canvas resizes in phone layout. P6.2 moves it "
        "to the canvas's phone form.",
    ("review/core/plot_style.py", "_apply_layout_engine"):
        "chooses constrained layout for phone-sized plot text. P6.2 moves it to the "
        "canvas's phone form.",
    ("review/core/plot_style.py", "apply_export_style_to_current"):
        "stamps whether the figure was fitted for the phone. P6.2 moves it to the "
        "canvas's phone form.",
    ("review/core/split_view.py", "_set_toggle_visible_for_mode"):
        "two panes side by side cannot fit a one-column layout, so the split toggle goes. "
        "P6.3 decides.",
    ("review/core/app_shell_view.py", "position_rail_toggle"):
        "the floating rail toggle is hidden in phone layout, where the drawer replaces "
        "it; every path into this function ends here. P6.3 decides.",
    ("review/core/ui_helpers.py", "phone_wording"):
        "rewords an empty-state hint for a finger and for the Wells sheet. P6.3 decides.",
    ("review/core/ui_helpers.py", "follow_phone_layout"):
        "re-words every empty plot's hint when the mode flips; the listener that "
        "phone_wording needs. P6.3 decides.",
    ("review/features/image_browser/image_table/controller.py", "image_table_refresh_empty_state"):
        "the empty-state hint points at Controls in phone layout and at 'above' at the "
        "desk. P6.3 decides.",
}


# ── scanning ────────────────────────────────────────────────────────────────

def _is_data_or_drawing(path: Path) -> bool:
    relative = rel(path)
    parts = relative.split("/")
    if parts[0] in DATA_AND_DRAWING_ROOTS:
        return True
    if relative in DATA_AND_DRAWING_ROOT_MODULES:
        return True
    name = parts[-1]
    return name in DATA_AND_DRAWING_NAMES or name.endswith(DATA_AND_DRAWING_SUFFIXES)


def _imported_mode_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """Every import of the mode, at any nesting: ``(line, what was imported)``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODE_MODULES or alias.name.startswith(_MODE_PREFIXES):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module in _MODE_MODULES or module.startswith(_MODE_PREFIXES):
                found.append((node.lineno, module))
                continue
            for alias in node.names:
                joined = f"{module}.{alias.name}"
                if joined in _MODE_MODULES or joined.startswith(_MODE_PREFIXES):
                    found.append((node.lineno, joined))
    return found


def data_and_drawing_reads_of_the_mode(files: list[Path]) -> list[str]:
    """Sites in data or drawing modules that import the mode."""
    offenders = []
    for path in files:
        if not _is_data_or_drawing(path):
            continue
        for line, what in _imported_mode_modules(parse_source(path)):
            offenders.append(f"{rel(path)}:{line} imports {what}")
    return offenders


def _is_sanctioned(path: Path) -> bool:
    relative = rel(path)
    if relative.split("/")[0] in SANCTIONED_DIRS:
        return True
    if relative in SANCTIONED_FILES:
        return True
    return relative.endswith(SANCTIONED_SUFFIXES)


class _Scope(ast.NodeVisitor):
    """Walk a module recording each mode read with its enclosing function."""

    def __init__(self) -> None:
        self._stack: list[str] = []
        self.reads: list[tuple[int, str, str]] = []     # (line, function, call)

    def _enter(self, node) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in MODE_READS \
                and isinstance(fn.value, ast.Name) and fn.value.id == "phone_layout":
            self.reads.append((node.lineno, ".".join(self._stack) or "<module>", fn.attr))
        elif isinstance(fn, ast.Name) and fn.id in MODE_READS and fn.id in self._imported:
            self.reads.append((node.lineno, ".".join(self._stack) or "<module>", fn.id))
        self.generic_visit(node)

    #: Names imported straight from ``core.phone_layout`` (``from core.phone_layout
    #: import is_on``), set by :func:`mode_reads` before the walk.
    _imported: frozenset[str] = frozenset()


def mode_reads(files: list[Path]) -> list[tuple[str, str, int, str]]:
    """Every read of the mode outside the sanctioned layers:
    ``(file, function, line, call)``."""
    out = []
    for path in files:
        if _is_sanctioned(path):
            continue
        tree = parse_source(path)
        scope = _Scope()
        scope._imported = frozenset(
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "core.phone_layout"
            for alias in node.names)
        scope.visit(tree)
        out.extend((rel(path), fn, line, call) for line, fn, call in scope.reads)
    return out


def unrostered_reads(files: list[Path], roster=None) -> list[str]:
    roster = MODE_READ_ROSTER if roster is None else roster
    return [f"{f}:{line} {fn}() calls {call}"
            for f, fn, line, call in mode_reads(files) if (f, fn) not in roster]


def stale_roster_entries(files: list[Path], roster=None) -> list[str]:
    roster = MODE_READ_ROSTER if roster is None else roster
    live = {(f, fn) for f, fn, _line, _call in mode_reads(files)}
    return [f"{f} {fn}()" for (f, fn) in sorted(roster) if (f, fn) not in live]


def _is_string_equal_to(node: ast.AST, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def recognised_by_name(tree: ast.AST) -> list[tuple[int, str]]:
    """The places ``ui/phone_shell.py`` finds a widget by what it is called or
    by a duck type, rather than by what it declares: ``(line, what)``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(isinstance(t, ast.Name) and t.id in ("_KIND_BY_CLASS", "_KIND_BY_OBJECT_NAME")
                        for t in node.targets):
            name = next(t.id for t in node.targets if isinstance(t, ast.Name))
            found.extend((key.lineno, f"{name} entry {key.value!r}")
                         for key in node.value.keys if isinstance(key, ast.Constant))
        elif isinstance(node, ast.Compare) \
                and any(_is_string_equal_to(n, "InfoHint") for n in [node.left, *node.comparators]):
            found.append((node.lineno, 'compares an object name to "InfoHint"'))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "getattr" and len(node.args) >= 2 \
                and _is_string_equal_to(node.args[1], "draw_idle"):
            found.append((node.lineno, "duck-types a canvas by draw_idle"))
    return sorted(found)


#: ``widgets/form_registry.py`` may import none of these: it is in ``widgets/``,
#: which carries no app knowledge, and it knows kinds, not layouts.
FORM_REGISTRY = "widgets/form_registry.py"
_FORBIDDEN_IMPORT_ROOTS = ("core", "ui", "review", "analyze", "datasets", "allwell")


def form_registry_violations(path: Path) -> list[str]:
    """Imports of the application and any identifier or string naming a phone,
    in the registry module: ``["line what", ...]``."""
    out: list[str] = []
    for node in ast.walk(parse_source(path)):
        if isinstance(node, ast.Import):
            out.extend(f"{node.lineno} imports {a.name}" for a in node.names
                       if a.name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                out.append(f"{node.lineno} imports from {node.module}")
        words = []
        if isinstance(node, ast.Name):
            words = [node.id]
        elif isinstance(node, ast.Attribute):
            words = [node.attr]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            words = [node.name]
        elif isinstance(node, ast.arg):
            words = [node.arg]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = [node.value]
        out.extend(f"{getattr(node, 'lineno', 0)} names a phone: {w[:40]!r}"
                   for w in words if "phone" in w.lower())
    return out


# ── the tests ───────────────────────────────────────────────────────────────

def test_data_and_drawing_layers_never_read_the_mode():
    offenders = data_and_drawing_reads_of_the_mode(python_sources())
    assert not offenders, (
        "renderers, collectors, *_compute.py and domain/, services/, pipeline/, util/ "
        "receive their sizes as arguments and never import the phone-layout mode "
        "(PHONE_FORM_TRACKER.md §2.2):\n  " + "\n  ".join(offenders))


def test_every_read_of_the_mode_outside_the_sanctioned_layers_is_rostered():
    offenders = unrostered_reads(python_sources())
    assert not offenders, (
        "a read of phone_layout outside ui/, core/, *_phone.py and *phone_view.py needs a "
        "MODE_READ_ROSTER entry with a reason — or belongs in one of those layers instead "
        "(PHONE_FORM_TRACKER.md §2.2):\n  " + "\n  ".join(offenders))


def test_the_roster_describes_only_real_reads():
    stale = stale_roster_entries(python_sources())
    assert not stale, (
        "these MODE_READ_ROSTER entries no longer match a read — delete them, so the roster "
        "only shrinks:\n  " + "\n  ".join(stale))


def test_the_shell_recognises_no_more_widgets_by_name_than_the_budget():
    sites = recognised_by_name(parse_source(REPO_ROOT / SHELL))
    listing = "\n  ".join(f"{SHELL}:{line} {what}" for line, what in sites)
    assert len(sites) <= RECOGNISED_BY_NAME_BUDGET, (
        f"{len(sites)} sites recognise a widget by name, against a budget of "
        f"{RECOGNISED_BY_NAME_BUDGET}. A widget declares its kind where it is built "
        f"(PHONE_FORM_TRACKER.md §2.3); do not add a name to the shell:\n  " + listing)
    assert len(sites) == RECOGNISED_BY_NAME_BUDGET, (
        f"the shell now recognises {len(sites)} widgets by name and the budget says "
        f"{RECOGNISED_BY_NAME_BUDGET}: lower RECOGNISED_BY_NAME_BUDGET to {len(sites)} "
        f"in the same commit.\n  " + listing)


def test_the_form_registry_knows_kinds_and_not_phones():
    offenders = form_registry_violations(REPO_ROOT / FORM_REGISTRY)
    assert not offenders, (
        f"{FORM_REGISTRY} is a widgets/ module that records which widgets have an alternate form; "
        "it must import nothing from the application and name no layout "
        "(PHONE_FORM_TRACKER.md §2.3):\n  " + "\n  ".join(offenders))
