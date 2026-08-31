"""Every setting the form collects is restored, or is on a list saying why not.

"Load these settings" reads a previous run's record back onto the form.
`_collect_run_options` builds the settings; `_apply_pinfo_settings` and its two
siblings put them back. They are two vocabularies over one set of names, and
nothing held them together — L5 found two settings (`auto_threshold`,
`run_check`) that the applier read under their *negated* CLI spellings and so
silently dropped, and the commit that fixed them was titled "eighteen of twenty"
from a hand search rather than from a check.

A hand search is what §7.5 #4 warns about: it describes the search, not the
codebase. This is the check. A new form field is either restored or it appears
here with a reason, and there is no third outcome that passes quietly.

The roster is deliberately per *key*, not per file: a key exempted because it is
handled by a different applier has to name that applier, so an exemption cannot
outlive the thing it points at.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests._repo_scan import parse_source

pytest.importorskip("PySide6")

_ROOT = Path(__file__).resolve().parent.parent

#: Keys `_collect_run_options` builds that `analyze.pinfo` does not read,
#: each with the reason. Anything not here must be restored.
NOT_RESTORED_BY_PINFO = {
    # Restored, but under a different spelling or by a different applier.
    "auto_threshold": "read as its negated CLI spelling `no_auto_threshold`",
    "run_check": "read as its negated CLI spelling `no_run_check`",
    "stardist_prob_thresh": "restored by analyze.seg_params."
                            "_restore_stardist_thresholds, which also translates "
                            "the -1 sentinel to a blank box",
    "stardist_nms_thresh": "as stardist_prob_thresh",
    "remote_batch_size": "restored by analyze.job_edit._hydrate_opts_without_"
                         "sidecar_equivalents",
    "auto_threshold_method": "restored by analyze.job_edit; the sidecar re-infers "
                             "it from the recorded control wells",

    # Deliberately not restored.
    "raw": "the input folder is the one thing Load must not overwrite — "
           "restoring it sent the user's next run at the plate they had just "
           "navigated away from (audit D8)",
    "allow_low_disk": "an acknowledgement of a pre-flight refusal, not a "
                      "computation setting. Silently re-arming 'yes, I know, "
                      "run anyway' on a different machine is not a restore",

    # Not settings at all: hardcoded True at the collect site.
    "compress_input_well_folders": "always-on; the form has no control for it",
    "compress_output_well_folders": "always-on; the form has no control for it",
    "move_source_tifs": "always-on; the form has no control for it",
}


def _collected_keys() -> dict[str, str]:
    """Every keyword `_collect_run_options` builds, mapped to its expression."""
    tree = parse_source((_ROOT / "analyze/form.py"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "_collect_run_options"), None)
    assert fn is not None, "_collect_run_options is gone; this guard names a seam that no longer exists"
    out: dict[str, str] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "dict":
            for kw in n.keywords:
                if kw.arg:
                    out[kw.arg] = ast.unparse(kw.value)
    return out


#: An exemption that says "restored under another name" or "restored by another
#: module" is only as good as the name it points at. These are checked.
READ_UNDER_ANOTHER_NAME = {
    "auto_threshold": "no_auto_threshold",
    "run_check": "no_run_check",
}

RESTORED_BY_ANOTHER_APPLIER = {
    "stardist_prob_thresh": "analyze/seg_params.py",
    "stardist_nms_thresh": "analyze/seg_params.py",
    "remote_batch_size": "analyze/job_edit.py",
    "auto_threshold_method": "analyze/job_edit.py",
}


def _applier_source() -> str:
    return (_ROOT / "analyze/pinfo.py").read_text()


def test_the_scan_can_see_both_sides():
    """Fail closed: a guard reading nothing passes for ever."""
    keys = _collected_keys()
    assert len(keys) > 30, f"only {len(keys)} settings found; the scan is broken"
    assert "filename_schema" in keys and "segmentation_method" in keys
    assert "no_auto_threshold" in _applier_source()


def test_every_collected_setting_is_restored_or_excused():
    keys = _collected_keys()
    src = _applier_source()
    unread = [k for k in keys
              if f'"{k}"' not in src and f"'{k}'" not in src]
    unexcused = sorted(set(unread) - set(NOT_RESTORED_BY_PINFO))
    assert not unexcused, (
        "these settings are collected by the form and read back by nothing, so "
        '"Load these settings" silently leaves them at whatever the form '
        "happens to hold — which is what L5 was: "
        f"{unexcused}"
    )


def test_the_roster_has_no_stale_entries():
    """§7.5 #10: pair every allowlist with a liveness test.

    An entry that has started being restored, or that names a key the form no
    longer collects, is a note nobody re-reads that quietly widens the hole.
    """
    keys = _collected_keys()
    src = _applier_source()
    gone = sorted(k for k in NOT_RESTORED_BY_PINFO if k not in keys)
    assert not gone, f"these are no longer collected by the form: {gone}"
    now_read = sorted(k for k in NOT_RESTORED_BY_PINFO
                      if f'"{k}"' in src or f"'{k}'" in src)
    assert not now_read, (
        f"these are restored now, so their exemptions are lies: {now_read}")


@pytest.mark.parametrize("key", sorted(NOT_RESTORED_BY_PINFO))
def test_every_exemption_carries_a_reason(key):
    reason = NOT_RESTORED_BY_PINFO[key]
    assert len(reason) > 20, f"{key}'s exemption says nothing: {reason!r}"


@pytest.mark.parametrize("key", sorted(READ_UNDER_ANOTHER_NAME))
def test_a_setting_read_under_another_name_is_read_under_that_name(key):
    """The L5 defect exactly: the applier reads a *negated* CLI spelling.

    An exemption saying "read as `no_auto_threshold`" is worth nothing if
    nothing reads `no_auto_threshold` — that is how the setting went missing in
    the first place, and how it would go missing again.
    """
    alias = READ_UNDER_ANOTHER_NAME[key]
    src = _applier_source()
    assert f'"{alias}"' in src or f"'{alias}'" in src, (
        f"{key} is excused because the applier reads it as {alias!r}, and the "
        f"applier does not mention {alias!r} at all — so the setting is "
        "silently dropped by 'Load these settings'")


@pytest.mark.parametrize("key", sorted(RESTORED_BY_ANOTHER_APPLIER))
def test_a_setting_restored_elsewhere_is_restored_there(key):
    module = RESTORED_BY_ANOTHER_APPLIER[key]
    src = (_ROOT / module).read_text()
    assert f'"{key}"' in src or f"'{key}'" in src, (
        f"{key} is excused because {module} restores it, and {module} does not "
        f"mention it")


def test_the_two_sub_rosters_are_subsets_of_the_exemptions():
    """A name in one and not the other is an exemption nothing checks."""
    for roster, label in ((READ_UNDER_ANOTHER_NAME, "READ_UNDER_ANOTHER_NAME"),
                          (RESTORED_BY_ANOTHER_APPLIER,
                           "RESTORED_BY_ANOTHER_APPLIER")):
        stray = sorted(set(roster) - set(NOT_RESTORED_BY_PINFO))
        assert not stray, f"{label} names keys that are not exempt: {stray}"
