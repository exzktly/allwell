"""Home's Import command: groups from a JSON file, merged into the dataset's.

Its own module because two of Home's modules need it at import time — Sample Map's tab builds it beside its layer
switch, and ``controller.sync_home_sidebar`` shows and hides it — and neither may import the other's view for it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from review.core.selection_controller import import_selections_from_json
from ui import buttons

#: The widget registry key the button is registered under.
IMPORT_KEY = "_home_import_btn"


def build_import_button(ctx, parent: QWidget) -> QWidget:
    """Import: groups from a JSON file, merged in. Every definition on Home autosaves to the data folder on edit,
    so there is no Save to pair it with. Sample Map puts it beside its layer switch, and ``sync_home_sidebar``
    hides it on the Variables layer, since a file's groups are not what a page of variables would take."""
    button = ctx.widgets.register(IMPORT_KEY, buttons.quiet("Import\u2026", parent))
    button.setToolTip(
        "Import groups from a JSON file. When this dataset already has groups "
        "you choose whether to add the file's groups beside them or replace "
        "them.")
    button.clicked.connect(lambda _=False: import_selections_from_json(ctx))
    return button
