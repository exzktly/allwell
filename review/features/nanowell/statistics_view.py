"""The Statistics view's controls on the Nanowells page's band: *Compare* (what one value is) and *Test*.

The view also uses the band's shared controls — Metric, Channel, Baseline, Subset, Fixed cohort, Timepoint and
*vs* (the control the comparisons are made against) — which ``tab_view.refresh_controls`` shows on it. Every control
writes through the controller, which redraws.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QComboBox, QPushButton

from review.features.nanowell import _rare_controller as rc
from review.features.nanowell import controller as nwc
from review.features.nanowell import statistics_compute as sc

_UNIT_TIP = ("What one value of a sample is.\n"
             "Wells: each well is a sample and its nanowells are the values — whether these wells' distributions\n"
             "differ. The nanowells of one well share its medium and handling, so this says nothing about the\n"
             "conditions behind the wells.\n"
             "Groups: each replicate set (Sample Definitions), or a well standing alone, is a sample and its wells'\n"
             "medians (means, for a size class) are the values — wells are the replicate unit, so this compares\n"
             "conditions. A group needs two wells with a value.")
_TEST_TIP = ("The test each comparison is made with.\n"
             "t-tests compare means; Mann-Whitney U compares ranks; Kolmogorov-Smirnov compares whole distributions\n"
             "(the figure draws their cumulative curves); Dunnett compares each sample with the control chosen\n"
             "under vs, corrected for that family. With three or more samples an omnibus (ANOVA, or Kruskal-Wallis\n"
             "for the rank tests) is stated first. Other tests' p-values are Benjamini-Hochberg adjusted (p_BH).\n"
             "Rare responders: each group against the control group under vs, wells as the units: does it hold a\n"
             "handful of nanowells past anything the control wells reach, on the Metric? The exceedance test decides;\n"
             "Higher Criticism and an upper-tail shape test are reported beside it. The value is the count fold change.")
_FLAGGED_TIP = ("Show the nanowells past the control cutoff — the ones the verdict is counted from — in the "
                "pop-out, as rows of crops over time, to rule out segmentation errors, doublets and debris.")
#: Why **Review flagged crops** is greyed out, said on the button itself.
FLAGGED_NONE_TIP = "No nanowell was past the control cutoff, so there is nothing to review."
FLAGGED_NOT_RUN_TIP = "Choose the control group under Control: the nanowells the test flags can then be reviewed here."
FLAGGED_RUNNING_TIP = "Available once the test has run: the nanowells it flags can then be reviewed here."
STATS_CONTROL_TIP = ("Compare each sample with this one only — fewer comparisons than every pair, and the family a "
                     "plate design usually means. Dunnett needs one. None: every pair is compared.")


#: The Rare responders test's direction choices, as the band offers them: ``(label, NanowellState.rare_direction)``.
DIRECTIONS = (("Responders higher", "higher"), ("Responders lower", "lower"))
_DIRECTION_TIP = ("Which tail of the Metric a rare responder is in. Higher: a few nanowells far above anything the\n"
                  "control wells reach (growth, a rise in signal). Lower: far below (death, a loss of signal).")


@dataclass
class StatisticsControls:
    unit: object
    test: object
    group: object
    flagged: object = None
    direction: object = None

    @property
    def signalling(self) -> tuple:
        return tuple(w for w in (self.unit, self.test, self.direction) if w is not None)


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
    flagged = QPushButton("Review flagged crops")
    flagged.setProperty("variant", "secondary")
    flagged.setToolTip(_FLAGGED_TIP)
    flagged.clicked.connect(lambda _=False: rc.open_flagged_popout(ctx))
    ctx.widgets.register("_nw_stats_flagged_btn", flagged)
    direction = QComboBox()
    for label, key in DIRECTIONS:
        direction.addItem(label, key)
    direction.setToolTip(_DIRECTION_TIP)
    direction.currentIndexChanged.connect(lambda _i: nwc.set_rare_direction(ctx, direction.currentData() or "higher"))
    ctx.widgets.register("_nw_rare_direction_cb", direction)
    group = band.add_scope("Compare", unit, test, direction, flagged)
    return StatisticsControls(unit, test, group, flagged, direction)


def refresh_statistics_controls(st, controls: StatisticsControls, on_view: bool) -> None:
    """Shown on the Statistics view only, on the state's choices. The caller blocks the signals."""
    controls.group.setVisible(on_view)
    rare = sc.test_spec(st.stats_test)["kind"] == "rare"
    # Hidden, not greyed: the rare-responder tests always take wells as the units, so the choice never applies to
    # them, and a greyed control the user cannot use reads as one they must first unlock.
    controls.unit.setVisible(not rare)
    if controls.direction is not None:
        controls.direction.setVisible(rare)
        index = controls.direction.findData(st.rare_direction)
        if index >= 0:
            controls.direction.setCurrentIndex(index)
    if controls.flagged is not None:
        controls.flagged.setVisible(rare)
        ready = bool(st.rare_flagged)
        controls.flagged.setEnabled(ready)
        controls.flagged.setToolTip(_FLAGGED_TIP if ready else FLAGGED_NONE_TIP if st.rare_footer is not None
                                    else FLAGGED_RUNNING_TIP if st.control else FLAGGED_NOT_RUN_TIP)
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
    if st.stats_unit == sc.NANOWELLS and sc.test_spec(st.stats_test)["kind"] != "rare":
        return nwc.page_wells(ctx)
    return list(nwc.page_groups(ctx))
