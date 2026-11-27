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
        'kept: it only reads the double click to stand aside for the rename; the rename itself '
        'is Display › Labels (heatmap/labels_view.py)',
    ('review/features/heatmap/layout_view.py', '_LayoutTable.mouseDoubleClickEvent'):
        'kept: a shortcut to the Layout page\'s Empty cell button, which empties the selected cell',
    ('review/features/heatmap/tab_view.py', '_on_canvas_press'):
        'kept: routes a double click on a tick label to the rename that Display › Labels also does',
    ('review/features/heatmap/tab_view.py', '_on_label_double_click'):
        'kept: a shortcut to Display › Labels; both end in labels_view.rename_label',
    ('review/features/home/triage/tab_view.py', 'build_triage_subtab'):
        'kept: a double click on a row is a shortcut to the page\'s Anchor button (anchor_btn → _on_anchor)',
    ('review/features/home/variables/controller.py', 'wire_variables'):
        'kept: a shortcut to the Select wells button under the group list (_var_groups_select)',
    ('review/features/image_browser/image_table/controller.py', 'image_table_generate'):
        'kept: a right-click on a cell is a shortcut to the action strip\'s Cell page, on that cell (grid_ops.py)',
    ('review/features/nanowell/_identity_zoom.py', 'on_press'):
        'kept: a double click on a panel is a shortcut to the band\'s Reset zoom button (identity_view.py)',
    ('review/features/nanowell/controller.py', 'on_click'):
        'kept: a double click on a nanowell is a shortcut to the band\'s Go to field, which zooms onto '
        'and rings the address typed in it (goto_view.py)',
    ('review/features/review_csv/tab_view.py', 'build_review_csv_tab'):
        'kept: a double click on a row is a shortcut to the band\'s Open in Segmentation button',
    ('widgets/plot_card.py', 'PlotCard.eventFilter'):
        'kept: the copy and save buttons a hover reveals are shortcuts to the band\'s Share menu '
        '(Copy figure, Save figure…)',
    ('widgets/removable_row.py', '_RemovableRow.enterEvent'):
        'kept: hover reveals the × at the desk; where there is no hover the row\'s declared form draws it '
        'always (setAlwaysShown, applied by the phone shell)',
    ('widgets/stage_path_view.py', 'StagePathView.mouseDoubleClickEvent'):
        'kept: a shortcut to the Fit whole path button under the path (datasets/ome_import/view.py)',
}


def test_no_gesture_is_still_pending():
    """Every site names the visible control that does the same thing."""
    pending = sorted(f"{f}::{fn}" for (f, fn), why in GESTURES.items() if not why.startswith("kept: "))
    assert not pending, "these gesture sites name no visible control:\n  " + "\n  ".join(pending)


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
