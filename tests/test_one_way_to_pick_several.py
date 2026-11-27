"""One component picks several of a list: ``widgets/multi_select.py``.

A list of timepoints, fields of view, gates or channels with a tick beside each
was built eight separate ways, some applying as a box was ticked and some only
when a menu closed. Two things hold the one component in place: nothing
imports the old ``widgets.checkable_dropdown``, and no function builds a
check-list by hand — a checkable action or a ``QCheckBox`` made in a loop, in a
function that also builds the menu or popover it goes in.
"""

from __future__ import annotations

from tests import _surface_guards as scan

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
OLD_PICKER_IMPORTERS: dict[tuple[str, str], str] = {
    ('analyze/qc_section.py', '<module>'):
        'pending S12: takes widgets/multi_select instead',
    ('analyze/section_summaries.py', '<module>'):
        'pending S12: takes widgets/multi_select instead',
    ('datasets/core/timepoint_picker.py', '<module>'):
        'pending S12: takes widgets/multi_select instead',
    ('review/features/preview/frame_scope_view.py', '<module>'):
        'pending S12: takes widgets/multi_select instead',
}

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
CHECK_LIST_BUILDS: dict[tuple[str, str], str] = {
    ('datasets/job_queue/row.py', 'JobRow._add_sources'):
        "pending S26: the job row's menu",
    ('review/features/barplot/controller.py', 'open_bar_timepoint_selector'):
        'pending S12: one of the multi-select builds',
    ('review/features/home/cell_gating/fov_view.py', 'open_fov_menu'):
        'pending S12: one of the multi-select builds',
    ('review/features/home/cell_gating/tab_view.py', '_open_gate_filter_menu'):
        'pending S12: one of the multi-select builds',
    ('review/features/image_browser/cell_browser/criteria_view.py', '_open_gate_select'):
        'pending S12: one of the multi-select builds',
    ('review/features/image_browser/cell_browser/criteria_view.py', '_open_multiselect'):
        'pending S12: one of the multi-select builds',
    ('review/features/image_browser/image_table/builder_view.py', '_add_baseline_choices'):
        "pending S18: the channel gear's options become a page",
    ('review/features/image_browser/image_table/builder_view.py', '_fill_channel_menu'):
        "pending S18: the channel gear's options become a page",
    ('review/features/nanowell/qc_view.py', '_walk_button'):
        'pending S19: becomes a row and a multi-select page',
    ('review/features/nanowell/tab_view.py', '_layers_button'):
        'pending S19: becomes a row and a multi-select page',
    ('review/features/response/controller.py', 'open_series_selector'):
        'pending S12: one of the multi-select builds',
    ('review/features/response/controller.py', 'open_timepoint_selector'):
        'pending S12: one of the multi-select builds',
    ('review/features/scatter/tp_selector_view.py', 'open_timepoint_selector'):
        'pending S12: one of the multi-select builds',
    ('ui/phone_shell.py', 'PhoneShell._text_size_row'):
        "pending S23: the phone's text-size choice becomes three plain commands",
    ('widgets/checkable_dropdown.py', 'CheckableDropdown._open_menu'):
        'pending S12: one of the multi-select builds',
}


def unrostered_old_picker_importers(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.old_picker_importers(trees), OLD_PICKER_IMPORTERS if roster is None else roster)


def test_no_new_old_picker_importers():
    found = unrostered_old_picker_importers()
    assert not found, (
        'widgets.checkable_dropdown is replaced by widgets.multi_select'
        ":\n  " + "\n  ".join(found))


def test_old_picker_importers_describes_only_real_sites():
    gone = scan.stale(scan.old_picker_importers(scan.app_trees()), OLD_PICKER_IMPORTERS)
    assert not gone, (
        "these OLD_PICKER_IMPORTERS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))


def unrostered_check_list_builds(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.check_list_builds(trees), CHECK_LIST_BUILDS if roster is None else roster)


def test_no_new_check_list_builds():
    found = unrostered_check_list_builds()
    assert not found, (
        'a check-list built by hand: use widgets.multi_select.MultiSelect'
        ":\n  " + "\n  ".join(found))


def test_check_list_builds_describes_only_real_sites():
    gone = scan.stale(scan.check_list_builds(scan.app_trees()), CHECK_LIST_BUILDS)
    assert not gone, (
        "these CHECK_LIST_BUILDS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))
