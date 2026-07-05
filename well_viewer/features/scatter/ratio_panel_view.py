"""Ratio metric definition panel (embedded in the Sample Definitions tab).

Lets the user define / edit / delete ratio metrics. Each ratio is a virtual
channel computed at read time from two ``{channel}_{metric}`` columns. The
panel reads channel/metric options from the loaded CSVs via
``detect_fluor_channels`` / ``detect_smfish_channels``.

Apply commits the new ratio list to ``app._app_state.channel.ratio_metrics`` (via
``app._set_ratio_metrics``), persists it to the data directory, and triggers
a redraw of all plots.
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from well_viewer.domain.ratio_models import RatioMetric


_METRIC_CHOICES: List[str] = ["mean_intensity", "total_intensity", "max_intensity", "smfish_count"]


class RatioMetricsPanel(QWidget):
    """Inline panel for editing the per-dataset list of ratio metrics."""

    def __init__(self, app, parent: QWidget) -> None:
        super().__init__(parent)
        self._app = app

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        intro = QLabel(
            "Define ratio metrics computed at read time as "
            "<i>numerator / (denominator + ε)</i>. Ratios appear as virtual "
            "channels in every plot tab.<br>"
            "<b>ε (epsilon)</b> is a small constant added to the denominator "
            "before dividing — it prevents division by zero when the "
            "denominator channel reads 0. Use 0 to drop those cells (NaN); "
            "use a small positive number (e.g. 1) to keep them.",
            self,
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        intro.setAlignment(Qt.AlignLeft)
        layout.addWidget(intro)

        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels([
            "Name",
            "Numerator chan",
            "Numerator metric",
            "Denominator chan",
            "Denominator metric",
            "ε (epsilon)",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        # Left-align header labels to match the left-read text columns below.
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.verticalHeader().setVisible(False)
        # The cell widgets (QLineEdit / QComboBox / QDoubleSpinBox) carry the
        # global QSS padding, which overflows the default ~30px row and clipped
        # the text descenders ("gfp" → "qfp"). Give rows headroom to fit them.
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setMinimumHeight(140)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_btn = QPushButton("+ Add ratio", self)
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("− Remove selected", self)
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch(1)

        apply_btn = QPushButton("Apply", self)
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)

        layout.addLayout(btn_row)

        self.refresh_from_app()

    # ── Public refresh ───────────────────────────────────────────────────────

    def refresh_from_app(self) -> None:
        """Reload table rows from ``app._app_state.channel.ratio_metrics``.

        Called after a fresh dataset load so the panel reflects whatever was
        persisted in the data directory.
        """
        self._table.setRowCount(0)
        for r in list(self._app._app_state.channel.ratio_metrics or []):
            self._append_row(r)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _channel_choices(self) -> List[str]:
        chans = list(self._app._app_state.channel.fluor_channels or [])
        if not chans:
            chans = ["gfp"]
        return chans

    def _default_channel(self) -> str:
        return self._channel_choices()[0]

    def _on_add(self) -> None:
        self._append_row(RatioMetric(
            name="ratio_new",
            numerator_channel=self._default_channel(),
            numerator_metric="mean_intensity",
            denominator_channel=self._default_channel(),
            denominator_metric="mean_intensity",
            epsilon=0.0,
        ))

    def _append_row(self, ratio: RatioMetric) -> None:
        table = self._table
        row = table.rowCount()
        table.insertRow(row)

        name_edit = QLineEdit(ratio.name, table)
        table.setCellWidget(row, 0, name_edit)

        num_chan_cb = self._channel_combo(ratio.numerator_channel)
        table.setCellWidget(row, 1, num_chan_cb)

        num_metric_cb = self._metric_combo(ratio.numerator_metric)
        table.setCellWidget(row, 2, num_metric_cb)

        den_chan_cb = self._channel_combo(ratio.denominator_channel)
        table.setCellWidget(row, 3, den_chan_cb)

        den_metric_cb = self._metric_combo(ratio.denominator_metric)
        table.setCellWidget(row, 4, den_metric_cb)

        eps_spin = QDoubleSpinBox(table)
        eps_spin.setRange(0.0, 1e9)
        eps_spin.setDecimals(6)
        eps_spin.setSingleStep(0.01)
        eps_spin.setValue(float(ratio.epsilon))
        eps_spin.setToolTip(
            "ε is added to the denominator before division to avoid /0.\n"
            "0 = denominators of 0 produce NaN (cell dropped).\n"
            "Small positive (e.g. 1) = keep those cells, biasing the ratio "
            "toward 0 when the denominator is small."
        )
        table.setCellWidget(row, 5, eps_spin)

    def _channel_combo(self, current: str) -> QComboBox:
        cb = QComboBox()
        for ch in self._channel_choices():
            cb.addItem(ch)
        idx = cb.findText(current)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        return cb

    def _metric_combo(self, current: str) -> QComboBox:
        cb = QComboBox()
        for m in _METRIC_CHOICES:
            cb.addItem(m)
        idx = cb.findText(current)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        return cb

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            current = self._table.currentRow()
            if current >= 0:
                rows = [current]
        for r in rows:
            self._table.removeRow(r)

    def _collect_ratios(self) -> List[RatioMetric]:
        out: List[RatioMetric] = []
        seen_names: set = set()
        for row in range(self._table.rowCount()):
            name_w = self._table.cellWidget(row, 0)
            num_chan_w = self._table.cellWidget(row, 1)
            num_metric_w = self._table.cellWidget(row, 2)
            den_chan_w = self._table.cellWidget(row, 3)
            den_metric_w = self._table.cellWidget(row, 4)
            eps_w = self._table.cellWidget(row, 5)
            name = (name_w.text() if name_w else "").strip()
            if not name:
                continue
            if name in seen_names:
                raise ValueError(f"Duplicate ratio name: {name!r}")
            seen_names.add(name)
            out.append(RatioMetric(
                name=name,
                numerator_channel=num_chan_w.currentText().strip().lower(),
                numerator_metric=num_metric_w.currentText().strip().lower(),
                denominator_channel=den_chan_w.currentText().strip().lower(),
                denominator_metric=den_metric_w.currentText().strip().lower(),
                epsilon=float(eps_w.value()),
            ))
        return out

    def _on_apply(self) -> None:
        try:
            ratios = self._collect_ratios()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid ratios", str(exc))
            return
        if hasattr(self._app, "_set_ratio_metrics"):
            self._app._set_ratio_metrics(ratios)
        else:
            self._app._app_state.channel.ratio_metrics = ratios
        self._app._persistence.schedule_save("ratios")


def build_ratios_inline_panel(app, parent: QWidget) -> RatioMetricsPanel:
    """Construct the inline ratio editor and return it for app-level wiring."""
    panel = RatioMetricsPanel(app, parent)
    app._ratio_panel = app._ctx.widgets.register("_ratio_panel", panel)
    return panel
