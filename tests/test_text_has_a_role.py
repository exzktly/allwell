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


def find(_path: str, tree: ast.Module):
    for call in ast.walk(tree):
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
