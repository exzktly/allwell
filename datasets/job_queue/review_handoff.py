"""Showing a recipe run's analysed result in Review, Qt-free.

A recipe can ask for its result to be opened in Review when its last Analyze
step finishes (``Recipe.open_result``). The queue then hands Review the
dataset root that step reported — the folder holding ``out/pipeline_info.json``,
which Analyze writes last — through the shell's hook, which decides whether to
move the window (``core.user_activity.review_hand_off``). What the hand-off
did is recorded in the run's journal and on the job's row, in the words below.
"""

from __future__ import annotations

from pathlib import Path

INFO = Path("out") / "pipeline_info.json"


def reviewable_root(path: str | Path | None) -> Path | None:
    """The dataset root at *path* (or the root above an ``out/`` folder) that
    Review can open, or ``None`` when it holds no analysed results."""
    text = str(path or "").strip()
    if not text:
        return None
    folder = Path(text)
    if (folder / INFO).is_file():
        return folder
    if folder.name == "out" and (folder / "pipeline_info.json").is_file():
        return folder.parent
    return None


#: What the run's journal and the job's row say for each hand-off outcome.
OUTCOME_MESSAGES = {
    "switched": "Opened {root} in Review.",
    "loaded": ("Loaded {root} in Review. You were using the app, so the window "
               "stayed where it was — switch to Review to see it."),
    "held": ("Review was in use, so it was left as it was. The result is ready "
             "to open: {root}"),
}
NOT_REVIEWABLE = ("Not opened in Review: {path} holds no analysed results "
                  "(no out/pipeline_info.json).")
