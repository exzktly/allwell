"""Qt builder for the Automation page: the Queue, Recipes and Runs tabs.

Three things about unattended work, kept apart because they are different
kinds of thing and kept together because each leads to the others:

* **Queue** — the jobs: what is running now and what runs next.
* **Recipes** — the plans: chains saved to apply to each new acquisition.
* **Runs** — the record: each time a plan was applied, and what it did.

Applying a recipe adds jobs to the Queue; a job queued from a recipe opens its
run in Runs; a recipe lists its runs. The tabs are the only navigation between
them, so this widget offers :meth:`show_tab` to whoever needs to move the user
along that path.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

QUEUE, RECIPES, RUNS = "Queue", "Recipes", "Runs"
TABS = (QUEUE, RECIPES, RUNS)


class AutomationPage(QWidget):
    """The Automation feature body: one tab per page."""

    def __init__(self, queue: QWidget, recipes: QWidget, runs: QWidget,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("AutomationTabs")
        self.tabs.tabBar().setObjectName("AutomationSubTabs")
        self.tabs.setDocumentMode(True)
        for name, page in zip(TABS, (queue, recipes, runs)):
            self.tabs.addTab(page, name)
        layout.addWidget(self.tabs)

    def show_tab(self, name: str) -> None:
        self.tabs.setCurrentIndex(TABS.index(name))

    def current_tab(self) -> str:
        return TABS[self.tabs.currentIndex()]
