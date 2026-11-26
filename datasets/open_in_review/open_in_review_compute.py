"""Which folder Review can open for a finished step, Qt-free.

Review opens a dataset root: the folder holding ``out/pipeline_info.json``,
which Analyze writes last, after every well. A step earlier in a chain reports
either that root (Analyze's ``dataset_root``, a Subset or Concatenate of
analysed data) or, handed a path by hand, possibly the ``out/`` folder itself.
Anything else — a conversion's output, which has ``in/`` and nothing analysed
— has nothing for Review to show, and the step says so rather than loading an
empty plate.
"""

from __future__ import annotations

from pathlib import Path

INFO = Path("out") / "pipeline_info.json"


def reviewable_root(path: str | Path) -> Path | None:
    """The dataset root under or at *path* that Review can open, or ``None``."""
    text = str(path or "").strip()
    if not text:
        return None
    folder = Path(text)
    if (folder / INFO).is_file():
        return folder
    if folder.name == "out" and (folder / "pipeline_info.json").is_file():
        return folder.parent
    return None


def not_reviewable_reason(path: str | Path) -> str:
    """Why *path* cannot be opened in Review, for the job's message."""
    text = str(path or "").strip()
    if not text:
        return "No dataset folder to open."
    folder = Path(text)
    if not folder.exists():
        return f"{folder} does not exist."
    return (f"{folder} has no analysed results (no out/pipeline_info.json), so "
            "there is nothing to show in Review — put this step after Analyze.")


#: What the job reports for each outcome of the shell's hand-off.
OUTCOME_MESSAGES = {
    "switched": "Opened {root} in Review.",
    "loaded": ("Loaded {root} in Review. You were using the app, so the window "
               "stayed where it was — switch to Review to see it."),
    "held": ("Review was in use, so it was left as it was. The result is ready: "
             "{root} — open it from the Open in Review page when you are done."),
}
