"""flow_group: a label + its control are bundled into ONE FlowLayout item, so a
wrap at narrow width can never split a label from the widget it names (the
Distribution "Layout:", Scatter "Y-axis: Channel:", smFISH "LUT Min:" orphans).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def test_group_structure(qapp):
    from PySide6.QtWidgets import QComboBox, QLabel
    from widgets.flow_layout import flow_group

    combo = QComboBox()
    grp = flow_group(None, "Mode:", combo)
    lay = grp.layout()
    assert lay.count() == 2
    assert isinstance(lay.itemAt(0).widget(), QLabel)
    assert lay.itemAt(0).widget().text() == "Mode:"
    assert lay.itemAt(1).widget() is combo
    # the passed control is reparented into the group container
    assert combo.parent() is grp


def test_multi_label_group(qapp):
    from PySide6.QtWidgets import QComboBox
    from widgets.flow_layout import flow_group

    combo = QComboBox()
    grp = flow_group(None, "Y-axis:", "Channel:", combo)
    lay = grp.layout()
    assert lay.count() == 3
    assert [lay.itemAt(i).widget().text() for i in range(2)] == ["Y-axis:", "Channel:"]
    assert lay.itemAt(2).widget() is combo


def test_group_is_single_flow_item_and_never_splits(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QLineEdit, QWidget
    from widgets.flow_layout import FlowLayout, flow_group

    host = QWidget()
    fl = FlowLayout(host, h_spacing=6, v_spacing=4)
    groups = []
    for i in range(4):
        edit = QLineEdit()
        edit.setFixedWidth(80)
        g = flow_group(host, f"field {i}:", edit)
        fl.addWidget(g)
        groups.append((g, edit))

    # One FlowLayout item per group — the label can't wrap away from its control.
    assert fl.count() == 4

    host.show()
    qapp.processEvents()
    # Narrow rect: the four wide groups must wrap onto multiple lines.
    fl.setGeometry(QRect(0, 0, 300, 400))
    qapp.processEvents()

    tops = sorted({g.geometry().top() for g, _ in groups})
    assert len(tops) >= 2, "groups did not wrap onto multiple lines when narrow"

    # Within each group the label and its control share a line (container-relative
    # tops are equal): they move together across the wrap, never split.
    for g, edit in groups:
        lbl = g.layout().itemAt(0).widget()
        assert abs(lbl.geometry().top() - edit.geometry().top()) <= 2
