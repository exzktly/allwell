"""The nanowell count's own segmentation settings sit in the Segmentation section, shown in nanowell mode.

Asked: "Why are cellpose options inside the nanowells options and not the segmentation area at the top of the
analyze form" — then "Do that". The count is its own Cellpose call (``pipeline.nanowell_run``) with its own model,
input and cell size; in nanowell mode the Segmentation section is pinned to label-free and used to show only a pixel
diameter the count never reads. Now that section's label-free page carries the count's three rows in nanowell mode,
and hides the diameter it ignores; the Nanowells section keeps the array's geometry. The options themselves are
unchanged, which the job round trip (``test_analyze_job_edit.py``) and ``test_nanowell_cellpose_model.py`` hold.
"""

from __future__ import annotations

from tests.test_nanowell_form import tab  # noqa: F401

_COUNT_WIDGETS = ("_nanowell_model_cb", "_nanowell_segment_on_cb", "_nanowell_cell_diameter_edit")


def _shown(tab, attr) -> bool:  # noqa: F811
    return getattr(tab, attr).isVisibleTo(tab)


def test_the_count_s_settings_are_in_the_segmentation_section_and_shown_only_in_nanowell_mode(tab):  # noqa: F811
    stack = tab._seg_param_stack
    box = tab._nanowell_count_box
    for attr in _COUNT_WIDGETS:
        assert stack.isAncestorOf(getattr(tab, attr)) and box.isAncestorOf(getattr(tab, attr)), attr
    assert not any(_shown(tab, attr) for attr in _COUNT_WIDGETS)       # nanowell mode off: not offered
    tab._nanowell_cb.setChecked(True)
    assert tab._seg_method_cb.currentData() == "cellpose_labelfree" and not tab._seg_method_cb.isEnabled()
    assert all(_shown(tab, attr) for attr in _COUNT_WIDGETS)
    assert not tab._cellpose_diameter_row.isVisibleTo(tab)               # the pixel size the count does not read
    assert not tab._labelfree_note.isVisibleTo(tab)
    tab._nanowell_cb.setChecked(False)
    assert not any(_shown(tab, attr) for attr in _COUNT_WIDGETS)


def test_out_of_nanowell_mode_label_free_keeps_its_own_diameter(tab):  # noqa: F811
    tab._seg_method_cb.setCurrentIndex(tab._seg_method_cb.findData("cellpose_labelfree"))
    assert tab._cellpose_diameter_row.isVisibleTo(tab) and tab._labelfree_note.isVisibleTo(tab)
    assert not tab._nanowell_count_box.isVisibleTo(tab)
