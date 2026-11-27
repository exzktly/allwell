"""Text takes one of the six text roles (``ui/text_roles.py``), not an object name or an inline font of its own.

Before the roles, each page answered "how does a heading look" itself: six ways of drawing a section heading,
three of labelling a field, and 217 labels named ``Muted``. A site is any of:

- ``setObjectName`` with a retired text name (:data:`RETIRED`) — each was a role spelled per page;
- ``setProperty`` of a retired text property (``muted``, ``caption``, ``secondary``, ``role="section"``);
- an inline font: a style sheet setting ``font-size`` or ``font-weight``, or a font setter (``setBold``,
  ``setPointSize``…) — text sized at its site instead of by its role.

The roster (``tests/_design_rosters.TEXT_ROLE``) holds today's sites per file and only shrinks; row D10 empties it.
"""

from __future__ import annotations

import ast

from tests._design_rosters import TEXT_ROLE as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster, strings_in
from tests._repo_scan import python_sources, rel

RETIRED = {"Muted", "MutedCaption", "Caption", "Title", "Heading", "SectionTitle", "Secondary", "InfoHint",
           "Notice", "Mono", "Warn", "Success", "Error", "Danger"}
RETIRED_PROPERTIES = {"muted", "caption", "secondary"}
FONT_SETTERS = {"setBold", "setPointSize", "setPointSizeF", "setPixelSize", "setWeight"}
#: Where text is styled by role on purpose: the roles themselves and the sheet that draws them.
HOMES = {"ui/text_roles.py", "ui/theme.py"}

#: ``(file, function)`` whose font calls set no label's text: a painter's font for glyphs it draws itself, a font
#: built only to measure, or phone layout restoring the fonts it scaled. Named per function, with the reason, so an
#: exemption cannot spread to a label built in the same file (CLAUDE.md §7.5 #10).
EXEMPT: dict[tuple[str, str], str] = {
    ("widgets/well_plate_selector.py", "_header_font"): "paints the plate's row and column numbers, fitted to a pitch",
    ("widgets/well_plate_selector.py", "_paint_well"): "paints the marker glyph inside a well, sized to the well",
    ("widgets/well_plate_selector.py", "_paint_visit_number"): "paints a well's visit number, sized to the well",
    ("widgets/stage_path_view.py", "_paint_numbers"): "paints the acquisition order numbers along the stage path",
    ("widgets/notice_badge.py", "__init__"): "the badge's pip glyph, sized to the pip it is drawn in",
    ("analyze/nanowell_preview.py", "render_montage"): "labels burnt into a rendered montage image",
    ("ui/phone_shell.py", "_rescale_font"): "phone layout restoring a font it scaled, to the half point",
    ("widgets/empty_state.py", "_pin_text_heights"): "a font built only to measure two lines of the title's height",
    ("analyze/log_pane.py", "_build_log"): "the log is a QPlainTextEdit, which the textRole rules do not reach",
    ("widgets/menu_marks.py", "mark_current"): "a menu entry's own font, the current one bold: a QAction, which the "
                                                "textRole rules do not reach",
}


def _exempt_lines(path: str, tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and (path, fn.name) in EXEMPT:
            lines.update(range(fn.lineno, fn.end_lineno + 1))
    return lines


def find(path: str, tree: ast.Module):
    exempt = _exempt_lines(path, tree)
    for call in ast.walk(tree):
        if getattr(call, "lineno", 0) in exempt:
            continue
        if not isinstance(call, ast.Call) or not call.args:
            continue
        name = call_name(call)
        if name == "setObjectName":
            yield from (s for s in strings_in(call.args[0]) if s.value in RETIRED)
        elif name == "setProperty" and isinstance(call.args[0], ast.Constant):
            key = call.args[0].value
            if key in RETIRED_PROPERTIES or (key == "role" and len(call.args) > 1
                                             and any(s.value == "section" for s in strings_in(call.args[1]))):
                yield call
        elif name == "setStyleSheet":
            yield from (s for s in strings_in(call.args[0])
                        if "font-size" in s.value or "font-weight" in s.value)
        elif name in FONT_SETTERS:
            yield call
    # A name handed through a variable or a parameter — ``tone = "Muted"; label.setObjectName(tone)``, or a
    # helper's ``quiet: str = "Muted"`` default — is set all the same. In a function that passes setObjectName
    # anything but a literal, a retired name written anywhere in it (its defaults included) is a site.
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.lineno in exempt:
            continue
        if any(isinstance(c, ast.Call) and call_name(c) == "setObjectName" and c.args
               and not isinstance(c.args[0], ast.Constant) for c in ast.walk(fn)):
            yield from (n for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value in RETIRED)
    # A helper whose parameter becomes the object name (``def _set_hint_style(self, name): …setObjectName(name)``),
    # called elsewhere in the module with a retired name.
    setters = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
            if any(isinstance(c, ast.Call) and call_name(c) == "setObjectName" and c.args
                   and isinstance(c.args[0], ast.Name) and c.args[0].id in params for c in ast.walk(fn)):
                setters.add(fn.name)
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and call_name(call) in setters and getattr(call, "lineno", 0) not in exempt):
            yield from (n for a in call.args for n in strings_in(a) if n.value in RETIRED)


def _seen():
    return count_sites(find, python_sources(), rel, skip=lambda r: r in HOMES)


def test_text_takes_a_role_not_a_name_or_font_of_its_own():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("text styled by a retired object name or an inline font — build it with ui.text_roles "
                     "(page_title, section_heading, eyebrow, body, field_label, help_text, number):\n  "
                     + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)


def test_every_exemption_still_names_a_function_that_sets_a_font():
    """An exemption whose function is gone, or no longer sets a font, is a stale permission."""
    from tests._repo_scan import REPO_ROOT, parse_source

    stale = []
    for (path, name), _why in EXEMPT.items():
        tree = parse_source(REPO_ROOT / path)
        fns = [fn for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               and fn.name == name]
        if not any(isinstance(c, ast.Call) and call_name(c) in FONT_SETTERS for fn in fns for c in ast.walk(fn)):
            stale.append(f"{path}::{name}")
    assert not stale, f"these exemptions no longer describe a font call: {stale}"
