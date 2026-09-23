"""The per-channel role picker every packed-file converter page carries.

All three converters end their form with one row per channel read from the
file — a name and a role, seeded from the name — because the roles **pre-fill
Analyze's channel-token fields** when the dataset is opened there. The channel
names themselves always go into the output filenames regardless; the roles are
the convenience that saves retyping them.

**Measured before it was extracted, like the controller split** (CLAUDE.md
§7.5 #9). Hashing each view method's normalised AST across the two existing
converter pages put only five of thirty-one shared names byte-identical, and
the large ones — ``_build_params`` at 144 lines against 113, ``_build_ui`` at
106 against 78, ``refresh_readout`` at 72 against 32 — all diverge
substantially. So the *pages* are deliberately not shared: a common view base
would be a base full of flags, which is the shape
:class:`~datasets.core.tool_controller.DatasetToolController`'s own docstring
warns against and the reason it keeps its siblings' callbacks out.

This block is the part that really is one thing. It is a mixin rather than a
widget so a page can place the rows in its own layout, which is the one thing
the three pages do differently about it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from ui import theme
from util.channel_roles import (
    CHANNEL_ROLES,
    channel_tokens_from_roles,
    default_channel_roles,
)

#: Channel-name column in the per-channel role picker. Named rather than
#: written at the call site, both because ARCHITECTURE §8 asks for it and
#: because a literal passed through a helper's parameter is invisible to the
#: pixel guard that enforces it.
ROLE_LABEL_MIN_W = 120

ROLE_LABELS = {
    "nuclear": "Nuclear / segmentation",
    "fluor": "Fluorescent",
    "phase": "Phase / brightfield",
    "skip": "Skip",
}


class ChannelRolesMixin:
    """Per-channel role rows for a converter page.

    The page supplies ``self._channels_host`` (the container widget),
    ``self._channels_lay`` (its layout) and ``self._channels_caption``, and
    initialises the two pieces of state in :meth:`init_channel_roles`. What is
    here is the rebuild and the read-back, which every converter does
    identically.
    """

    def init_channel_roles(self) -> None:
        """Set up the state the rebuild keeps. Called from the page's ``__init__``."""
        self._channel_rows: list[tuple[str, QComboBox]] = []
        self._channel_names_shown: list[str] = []
        # Roles a queued job asked for, held until rows with those names exist.
        # The rows are built from reading the source file, which a job's
        # ``show_job`` has only just named, so they may not exist yet.
        self._pending_roles: dict[str, str] = {}

    def _rebuild_channels(self, names) -> None:
        """Rebuild the per-channel role rows when the channel set changes.

        Guarded on the names being *different*: the readout runs on every
        keystroke, and tearing down and rebuilding the rows each time would
        drop whatever roles the user had just picked.
        """
        names = list(names)
        if names == self._channel_names_shown:
            return
        self._channel_names_shown = names
        while self._channels_lay.count():
            item = self._channels_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._channel_rows = []
        defaults = default_channel_roles(names)
        for name in names:
            row = QWidget(self._channels_host)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(theme.Spacing.sm)
            label = QLabel(name, row)
            label.setMinimumWidth(ROLE_LABEL_MIN_W)
            row_layout.addWidget(label)
            combo = QComboBox(row)
            for role in CHANNEL_ROLES:
                combo.addItem(ROLE_LABELS[role], userData=role)
            combo.setCurrentIndex(
                CHANNEL_ROLES.index(defaults.get(name, "fluor")))
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            self._channels_lay.addWidget(row)
            self._channel_rows.append((name, combo))
        self._channels_caption.setVisible(bool(names))
        if self._pending_roles and set(self._pending_roles) & set(names):
            self._apply_roles(self._pending_roles)
            self._pending_roles = {}

    def channel_role_pairs(self) -> list[list[str]]:
        """The rows as ``[[name, role], …]`` — JSON-shaped, for a queued job.

        A queued converter job carries these so an Analyze job chained after
        it can take the roles as known (``services.job_kinds.produced_layout``)
        instead of guessing them from the filenames.
        """
        if not self._channel_rows and self._pending_roles:
            # A job just shown, whose source the page has not read yet. The
            # roles it is holding *are* this page's roles until the rows
            # exist; answering "none" made the Job Queue's edit session, which
            # collects straight after ``show_job``, report that saving would
            # clear them, on every converter job it opened.
            return [[name, role] for name, role in self._pending_roles.items()]
        return [[name, str(combo.currentData() or "fluor")]
                for name, combo in self._channel_rows]

    def set_channel_roles(self, pairs) -> None:
        """Select *pairs*' roles on the rows with those names.

        Applied now to any row that already exists, and held for the next
        rebuild otherwise — a queued job's page reads its source file after
        ``show_job`` names it, so on an edit the rows usually arrive later.
        """
        roles = {str(p[0]): str(p[1]) for p in (pairs or [])
                 if isinstance(p, (list, tuple)) and len(p) == 2}
        shown = {name for name, _combo in self._channel_rows}
        if not roles:
            # A job with no roles of its own. The rows may still hold the
            # previous job's choices — ``_rebuild_channels`` keeps rows whose
            # names are unchanged — and saving this job would then write them
            # into it. Back to the defaults the rows are built with.
            self._pending_roles = {}
            self._apply_roles(default_channel_roles(
                [name for name, _combo in self._channel_rows]))
            return
        if set(roles) & shown:
            self._apply_roles(roles)
            self._pending_roles = {}
        else:
            self._pending_roles = roles

    def _apply_roles(self, roles: dict[str, str]) -> None:
        for name, combo in self._channel_rows:
            role = roles.get(name)
            if role in CHANNEL_ROLES:
                combo.setCurrentIndex(CHANNEL_ROLES.index(role))

    def channel_tokens_dict(self) -> dict:
        """The channel role assignment as a ``pipeline_info``-shaped dict."""
        pairs = [(name, str(combo.currentData() or "fluor"))
                 for name, combo in self._channel_rows]
        return channel_tokens_from_roles(pairs).as_pipeline_info()
