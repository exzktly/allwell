"""Nothing is reachable by a gesture alone.

A right-click, a double click and a hover stay, at the desk, as *shortcuts* to
something a visible control also does. In phone layout there is no right
button and no hover, so the visible control is the only route — and at the
desk a feature found only by accident is a feature most users never find.

Every gesture site is on the roster below with the visible control that does
the same thing.
"""

from __future__ import annotations

from tests import _surface_guards as scan

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
GESTURES: dict[tuple[str, str], str] = {
    ('review/features/heatmap/controller.py', 'on_heatmap_label_drag_press'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/heatmap/layout_view.py', '_LayoutTable.mouseDoubleClickEvent'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/heatmap/tab_view.py', '_on_canvas_press'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/heatmap/tab_view.py', '_on_label_double_click'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/home/triage/tab_view.py', 'build_triage_subtab'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/home/variables/controller.py', 'wire_variables'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/image_browser/image_table/controller.py', 'image_table_generate'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/nanowell/_identity_zoom.py', 'on_press'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/nanowell/controller.py', 'on_click'):
        'pending S25: name the visible control that does the same thing',
    ('review/features/review_csv/tab_view.py', 'build_review_csv_tab'):
        'pending S25: name the visible control that does the same thing',
    ('widgets/plot_card.py', 'PlotCard.eventFilter'):
        'pending S25: name the visible control that does the same thing',
    ('widgets/removable_row.py', '_RemovableRow.enterEvent'):
        'pending S25: name the visible control that does the same thing',
    ('widgets/stage_path_view.py', 'StagePathView.mouseDoubleClickEvent'):
        'pending S25: name the visible control that does the same thing',
}


def unrostered_gestures(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.gesture_sites(trees), GESTURES if roster is None else roster)


def test_no_new_gestures():
    found = unrostered_gestures()
    assert not found, (
        'a gesture needs a visible control that does the same thing: name it in the roster'
        ":\n  " + "\n  ".join(found))


def test_gestures_describes_only_real_sites():
    gone = scan.stale(scan.gesture_sites(scan.app_trees()), GESTURES)
    assert not gone, (
        "these GESTURES entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))
