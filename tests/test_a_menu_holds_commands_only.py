"""A menu holds commands, one level deep.

Two things a menu must not hold. A **checkable action** is a setting, and a
setting belongs on an inspector page, where it is seen with its neighbours and
stays open while it is changed. A **submenu** is a menu opened from a menu: one
level is tolerated, each on the roster with its reason, and a submenu of a
submenu never.
"""

from __future__ import annotations

from tests import _surface_guards as scan

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
CHECKABLE_ACTIONS: dict[tuple[str, str], str] = {
}

#: ``(file, function)`` -> why the site is still there. An entry starting
#: ``pending Snn`` names the row of the surfaces plan that removes it; one
#: starting ``kept`` is permanent and says why.
SUBMENUS: dict[tuple[str, str], str] = {
    ('analyze/presets.py', 'AnalyzePresetsMixin._rebuild_presets_menu'):
        'kept: one level — Delete needs the name of the preset to delete, and listing the names to load and the names to delete in one list would make a load one slip from a deletion',
    ('datasets/job_queue/row.py', 'JobRow._add_chain_submenu'):
        'kept: one level — the jobs this job can follow, listed under the one command they answer',
    ('datasets/job_queue/row.py', 'JobRow._add_input_submenu'):
        'kept: one level — the jobs an input can come from, listed under the input they fill',
    ('datasets/job_queue/row.py', 'JobRow._add_next_step_submenu'):
        'kept: one level — the kinds of job that can read this output; the pairings that were a second level are entries of this list',
    ('datasets/job_queue/row.py', 'JobRow._add_retry'):
        'kept: one level — the two ways to retry a job whose output finished jobs used, which the plain Retry cannot choose between',
    ('review/features/batch_export/variable_select_panel.py', 'VariableSelectMixin._vsel_add_quick_setup_menu'):
        'kept: one level — one group per value of the variable chosen in it',
    ('widgets/path_field.py', 'PathField.add_browse_alternative'):
        'kept: one level — Browse… and the list of queued outputs that can fill the field are different jobs; the list is read as it opens',
}


def unrostered_checkable_actions(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.checkable_menu_actions(trees), CHECKABLE_ACTIONS if roster is None else roster)


def test_no_new_checkable_actions():
    found = unrostered_checkable_actions()
    assert not found, (
        'a checkable action is a setting in a menu: make it a control on an inspector page'
        ":\n  " + "\n  ".join(found))


def test_checkable_actions_describes_only_real_sites():
    gone = scan.stale(scan.checkable_menu_actions(scan.app_trees()), CHECKABLE_ACTIONS)
    assert not gone, (
        "these CHECKABLE_ACTIONS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))


def unrostered_submenus(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    return scan.unrostered(scan.submenus(trees), SUBMENUS if roster is None else roster)


def test_no_new_submenus():
    found = unrostered_submenus()
    assert not found, (
        'a submenu is a menu opened from a menu: flatten it, or roster it with a reason'
        ":\n  " + "\n  ".join(found))


def test_submenus_describes_only_real_sites():
    gone = scan.stale(scan.submenus(scan.app_trees()), SUBMENUS)
    assert not gone, (
        "these SUBMENUS entries no longer match a site — delete them, so the "
        "roster only shrinks:\n  " + "\n  ".join(gone))
