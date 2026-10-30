"""The Statistics view's controls on the Nanowells page's band: *Compare* (what one value is) and *Test*.

The view also uses the band's shared controls — Metric, Channel, Baseline, Subset, Fixed cohort, Timepoint and
*vs* (the control the comparisons are made against) — which ``tab_view.refresh_controls`` shows on it. Every control
writes through the controller, which redraws.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QComboBox

from review.features.nanowell import controller as nwc
from review.features.nanowell import statistics_compute as sc

_UNIT_TIP = ("What one value of a sample is.\n"
             "Wells: each well is a sample and its nanowells are the values — whether these wells' distributions\n"
             "differ. The nanowells of one well share its medium and handling, so this says nothing about the\n"
             "conditions behind the wells.\n"
             "Groups: each replicate set (Sample Definitions), or a well standing alone, is a sample and its wells'\n"
             "medians are the values — wells are the replicate unit, so this compares conditions. A group needs\n"
             "two wells with a value.")
_TEST_TIP = ("The test each comparison is made with.\n"
             "t-tests compare means; Mann-Whitney U compares ranks; Kolmogorov-Smirnov compares whole distributions\n"
             "(the figure draws their cumulative curves); Dunnett compares each sample with the control chosen\n"
             "under vs, corrected for that family. With three or more samples an omnibus (ANOVA, or Kruskal-Wallis\n"
             "for the rank tests) is stated first. Other tests' p-values are Benjamini-Hochberg adjusted (p_BH).")
STATS_CONTROL_TIP = ("Compare each sample with this one only — fewer comparisons than every pair, and the family a "
                     "plate design usually means. Dunnett needs one. None: every pair is compared.")


@dataclass
class StatisticsControls:
    unit: object
    test: object
    group: object

    @property
    def signalling(self) -> tuple:
        return (self.unit, self.test)


def build_statistics_controls(ctx, band) -> StatisticsControls:
    unit = QComboBox()
    for key in sc.UNITS:
        unit.addItem(sc.UNIT_LABELS[key], key)
    unit.setToolTip(_UNIT_TIP)
    unit.currentIndexChanged.connect(lambda _i: nwc.set_stats_unit(ctx, unit.currentData() or sc.NANOWELLS))
    ctx.widgets.register("_nw_stats_unit_cb", unit)
    test = QComboBox()
    for entry in sc.TESTS:
        test.addItem(entry["label"], entry["key"])
    test.setToolTip(_TEST_TIP)
    test.currentIndexChanged.connect(lambda _i: nwc.set_stats_test(ctx, test.currentData() or sc.TEST_KEYS[0]))
    ctx.widgets.register("_nw_stats_test_cb", test)
    group = band.add_scope("Compare", unit, test)
    return StatisticsControls(unit, test, group)


def refresh_statistics_controls(st, controls: StatisticsControls, on_view: bool) -> None:
    """Shown on the Statistics view only, on the state's choices. The caller blocks the signals."""
    controls.group.setVisible(on_view)
    for combo, value in ((controls.unit, st.stats_unit), (controls.test, st.stats_test)):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)


def fill_control_combo(ctx, st, combo, *, on_statistics: bool, tip: str) -> None:
    """The band's *vs* combo: none, then the groups (the Distribution view's comparison with a control) or, on the
    Statistics view, the samples of its unit; the state's control selected. The caller blocks the signals."""
    combo.clear()
    combo.addItem("— none", "")
    names = [] if st.data is None else control_choices(ctx, st) if on_statistics else list(nwc.page_groups(ctx))
    for name in names:
        combo.addItem(name, name)
    index = combo.findData(st.control)
    if index >= 0:
        combo.setCurrentIndex(index)
    combo.setToolTip(STATS_CONTROL_TIP if on_statistics else tip)


def control_choices(ctx, st) -> list[str]:
    """What the *vs* combo offers on the Statistics view: the samples of its unit — the wells drawn, or the groups."""
    if st.stats_unit == sc.NANOWELLS:
        return nwc.page_wells(ctx)
    return list(nwc.page_groups(ctx))
