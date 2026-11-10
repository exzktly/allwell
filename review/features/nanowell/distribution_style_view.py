"""The Distribution view's **Show**, **Smoothing** and **Bins** controls on the Nanowells page's band.

**Show** (Auto / Beeswarm / Violin / Histogram) chooses how each well's nanowells are drawn; **Smoothing** sets the
violins' bandwidth as a multiple of the automatic one; **Bins** sets the histograms' bin count. All three write
``NanowellState`` through the controller (``set_dist_style`` / ``set_violin_smooth`` / ``set_hist_bins``);
``distribution_style_compute`` holds what each choice means. Shown on the Distribution view only. Smoothing is
greyed unless a violin can be drawn (Auto, Violin), Bins unless Show is Histogram; each greyed tooltip says why.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from review.features.nanowell import controller as nwc
from review.features.nanowell import distribution_style_compute as dsc
from ui import theme
from widgets.segmented_control import SegmentedControl
from widgets.spin_box import SpinBox
from widgets.styled_slider import StyledSlider

SHOW_TIP = (
    "How each well's nanowells are drawn.\n\n"
    "Auto: dots (a beeswarm) while a column holds four wells or fewer, violins past that, where a slot is too "
    "narrow for readable dots.\n"
    "Beeswarm: always dots, one per nanowell; a crowded slot draws what fits and writes +N under it for the rest.\n"
    "Violin: always a violin per well with a tick at its median (or mean, for a metric averaged per well). No dots "
    "are drawn, so single nanowells cannot be clicked.\n"
    "Histogram: always a histogram per well, mirrored about its slot, with the same tick. Every well's bars sum to 1 "
    "and all wells share one set of bins and one width scale, so widths compare across wells. No dots are drawn.")
BINS_TIP = (
    "How many bins the histograms split the figure's range into. An integer metric (a cell count) bins on whole "
    "numbers, one bin per value when the range fits.")
SMOOTH_TIP = (
    "How smooth the violins are: their kernel bandwidth as a multiple of the automatic one (Scott's rule). 1.00× is "
    "the automatic bandwidth; lower follows the data more closely, higher smooths more. Saved figures state any "
    "setting other than 1.00×.")
_GREYED = "\n\n(Greyed: Show is {style}, which draws no violins.)"
_BINS_GREYED = "\n\n(Greyed: Show is {style}, which draws no histograms.)"


@dataclass
class DistributionStyleControls:
    show: object
    slider: object
    readout: object
    group_show: object
    group_smooth: object
    bins: object
    group_bins: object

    @property
    def signalling(self) -> tuple:
        return (self.show, self.slider, self.bins)

    def refresh(self, st, on_distribution: bool) -> None:
        style = dsc.style_or_default(st.dist_style)
        self.show.setCurrentByData(style)
        smooth = dsc.clamp_smooth(st.violin_smooth)
        self.slider.setValue(smooth)
        self.readout.setText(dsc.smooth_text(smooth))
        name = dict(dsc.STYLES)[style]
        usable = dsc.smooths(style)
        self.slider.setEnabled(usable)
        self.readout.setEnabled(usable)
        self.slider.setToolTip(SMOOTH_TIP + ("" if usable else _GREYED.format(style=name)))
        self.bins.show_value(dsc.clamp_bins(st.hist_bins))
        binned = style == dsc.HISTOGRAM
        self.bins.setEnabled(binned)
        self.bins.setToolTip(BINS_TIP + ("" if binned else _BINS_GREYED.format(style=name)))
        for group in (self.group_show, self.group_smooth, self.group_bins):
            group.setVisible(on_distribution)


def build_distribution_style_controls(ctx, band) -> DistributionStyleControls:
    """**Show**, **Smoothing** and **Bins**, each a scope of *band*."""
    show = SegmentedControl()
    for key, label in dsc.STYLES:
        show.addSegment(text=label, data=key)
    show.setToolTip(SHOW_TIP)
    show.currentChanged.connect(lambda idx: nwc.set_dist_style(ctx, dsc.STYLES[idx][0]))

    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    slider = StyledSlider(Qt.Horizontal, holder)
    slider.setRange(dsc.SMOOTH_MIN, dsc.SMOOTH_MAX)
    slider.setValue(dsc.SMOOTH_DEFAULT)
    slider.setFixedWidth(theme.Metrics.num_field_w_lg)
    slider.setToolTip(SMOOTH_TIP)
    # A redraw of a large plate takes a moment: the figure follows the slider when it is let go, while the readout
    # follows it as it moves.
    slider.setTracking(False)
    readout = QLabel(dsc.smooth_text(dsc.SMOOTH_DEFAULT), holder)
    readout.setToolTip(SMOOTH_TIP)
    slider.sliderMoved.connect(lambda value: readout.setText(dsc.smooth_text(value)))
    slider.valueChanged.connect(lambda value: nwc.set_violin_smooth(ctx, value))
    row.addWidget(slider)
    row.addWidget(readout)

    bins = SpinBox()
    bins.setRange(dsc.BINS_MIN, dsc.BINS_MAX)
    bins.setValue(dsc.BINS_DEFAULT)
    bins.setToolTip(BINS_TIP)
    bins.valueChanged.connect(lambda value: nwc.set_hist_bins(ctx, value))

    reg = ctx.widgets
    reg.register("_nw_hist_bins_spin", bins)
    reg.register("_nw_dist_style_seg", show)
    reg.register("_nw_violin_smooth_slider", slider)
    reg.register("_nw_violin_smooth_label", readout)
    group_show = band.add_scope("Show", show)
    group_smooth = band.add_scope("Smoothing", holder)
    group_bins = band.add_scope("Bins", bins)
    return DistributionStyleControls(show, slider, readout, group_show, group_smooth, bins, group_bins)
