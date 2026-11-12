"""The Analyze form's **Presets** menu: save the form's settings by name, load them back.

Beside *Use settings from a previous run…* at the top of the Data section. That
button reads a dataset's own record of how it was produced; a preset is the
user's own record of how a kind of plate is analysed, kept in the app's folder
and offered on every dataset. What a preset holds, what it leaves out and where
it is stored is ``analyze/domain/presets.py``; this module is the menu and the
three actions behind it.

The menu is rebuilt each time it opens (``aboutToShow``), so a preset saved or
deleted by another window — or by hand in the file — is what the next opening
lists, and a presets file that cannot be read is said so in the menu itself
rather than shown as an empty list the next save would overwrite.
"""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox, QPushButton, QWidget

from analyze.domain import presets as store
from core.safe import swallowed
from widgets.owned_action import owned_action

SAVE_ACTION = "Save current settings as…"
DELETE_MENU = "Delete preset"
NO_PRESETS = "No saved presets"


class AnalyzePresetsMixin:
    """Mixed into ``AnalyzeTab`` through ``AnalyzeTabFormMixin``; every method binds to the tab."""

    def _build_presets_button(self, parent: QWidget) -> QPushButton:
        """The *Presets* button and its menu, for the caller to place."""
        self._presets_btn = QPushButton("Presets", parent)
        self._presets_btn.setProperty("variant", "secondary")
        self._presets_btn.setToolTip(
            "Save this form's settings under a name, or fill the form from a saved preset. "
            "A preset never changes the input folder, the wells chosen for a run, or what to do "
            "with existing output.")
        self._presets_menu = QMenu(self._presets_btn)
        self._presets_menu.aboutToShow.connect(self._rebuild_presets_menu)
        self._presets_btn.setMenu(self._presets_menu)
        self._rebuild_presets_menu()
        return self._presets_btn

    def _rebuild_presets_menu(self) -> None:
        """List the saved presets to load, then Save and the Delete submenu."""
        menu = self._presets_menu
        menu.clear()                      # deletes the actions it owns (owned_action parents each to it)
        old = getattr(self, "_presets_delete_menu", None)
        if old is not None:               # a child widget, which clear() leaves: one per opening otherwise
            old.deleteLater()
        try:
            saved = store.load_presets()
            problem = ""
        except store.PresetFileError as exc:
            saved, problem = {}, str(exc)
        names = store.sorted_names(saved)
        if problem:
            note = menu.addAction("Presets file could not be read")
            note.setEnabled(False)
            note.setToolTip(problem)
        elif not names:
            menu.addAction(NO_PRESETS).setEnabled(False)
        for name in names:
            action = owned_action(menu, name)
            action.setToolTip(f"Fill the form with the settings saved as “{name}”.")
            action.triggered.connect(lambda _checked=False, n=name: self._load_preset(n))
        menu.addSeparator()
        save = owned_action(menu, SAVE_ACTION)
        save.setEnabled(not problem)     # never write over a file whose presets could not be read
        save.triggered.connect(self._save_preset_as)
        delete = self._presets_delete_menu = QMenu(DELETE_MENU, menu)
        for name in names:
            owned_action(delete, name).triggered.connect(
                lambda _checked=False, n=name: self._delete_preset(n))
        delete.setEnabled(bool(names))
        menu.addMenu(delete)

    def _say_preset_status(self, text: str) -> None:
        try:
            self._dashboard.set_status(text)
        except Exception:
            swallowed()

    def _save_preset_as(self) -> str | None:
        """Ask for a name and save the form's settings under it. Returns the name saved, or ``None``."""
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", QLineEdit.Normal, "")
        name = store.clean_name(name)
        if not ok or not name:
            return None
        try:
            existing = store.load_presets()
        except store.PresetFileError as exc:
            QMessageBox.warning(self, "Save preset", f"The presets file could not be read, so nothing "
                                                     f"was saved over it:\n{exc}")
            return None
        if name in existing:
            answer = QMessageBox.question(self, "Save preset",
                                          f"A preset called “{name}” already exists. Replace it?")
            if answer != QMessageBox.Yes:
                return None
        try:
            stored = store.save_preset(name, self._collect_run_options())
        except (OSError, ValueError, TypeError, store.PresetFileError) as exc:
            QMessageBox.warning(self, "Save preset", f"The preset could not be saved:\n{exc}")
            return None
        self._say_preset_status(f"Saved preset “{stored}”")
        return stored

    def _load_preset(self, name: str) -> bool:
        """Fill the form from the preset called *name*. Returns whether it was applied."""
        try:
            entry = store.load_presets().get(name)
        except store.PresetFileError as exc:
            QMessageBox.warning(self, "Load preset", str(exc))
            return False
        if entry is None:
            QMessageBox.warning(self, "Load preset", f"There is no preset called “{name}” any more.")
            return False
        opts = store.preset_options(entry["opts"])     # a hand-edited file's per-run keys are ignored too
        # The nanowell section's restore sets the registration source whether or not the record names one,
        # and a preset never does — it is a per-run key — so the field's own text is put back after.
        registration = getattr(self, "_nanowell_registration_edit", None)
        kept = registration.text() if registration is not None else None
        try:
            self.hydrate_options(opts, set_input_path=False)
        except Exception as exc:  # never crash the slot; say what happened instead
            QMessageBox.warning(self, "Load preset", f"Some fields could not be restored:\n{exc}")
            return False
        finally:
            if registration is not None:
                registration.setText(kept)
        self._say_preset_status(f"Loaded preset “{name}”")
        return True

    def _delete_preset(self, name: str) -> bool:
        """Delete the preset called *name*, after asking. Returns whether it was deleted."""
        answer = QMessageBox.question(self, "Delete preset", f"Delete the preset “{name}”?")
        if answer != QMessageBox.Yes:
            return False
        try:
            gone = store.delete_preset(name)
        except (OSError, store.PresetFileError) as exc:
            QMessageBox.warning(self, "Delete preset", f"The preset could not be deleted:\n{exc}")
            return False
        if gone:
            self._say_preset_status(f"Deleted preset “{name}”")
        return gone
