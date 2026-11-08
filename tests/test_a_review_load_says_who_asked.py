"""Every Review load writes, before it reads anything, which code asked for it.

A Windows session crashed twice just after a dataset load finished, during a remote run. The second time nobody was
at the window, and the boot log named every well the load read (``dataset load: reading measurements_A01.csv.gz``)
but nothing about what had opened the dataset. The line this pins is how the next such log answers that.

Driven through the real ``ReviewApp`` (``viewer_app``), because the line is only worth anything if the path the app
takes writes it.
"""

from __future__ import annotations

from review.core import load_controller


def _a_button_that_opens(app, path) -> None:
    """Stands in for whatever started the load: its name is what the line must carry."""
    app._load_path(path)


def test_a_load_names_the_code_that_started_it(viewer_app, mini_dataset, monkeypatch):
    lines: list[str] = []
    real = load_controller.boot_diag.boot
    monkeypatch.setattr(load_controller.boot_diag, "boot", lambda msg: (lines.append(msg), real(msg)))

    _a_button_that_opens(viewer_app, mini_dataset)

    opening = [ln for ln in lines if ln.startswith("dataset load: opening ")]
    assert len(opening) == 1, lines
    assert str(mini_dataset) in opening[0]
    assert "test_a_review_load_says_who_asked.py:" in opening[0] and " _a_button_that_opens" in opening[0], opening[0]
    # Before any well is read, so a load that never comes back still names who asked.
    first_read = next(i for i, ln in enumerate(lines) if ln.startswith("dataset load: reading "))
    assert lines.index(opening[0]) < first_read


def test_a_deferred_load_names_who_asked_when_it_was_asked(qapp, qtbot, mini_dataset, monkeypatch):
    """Every hand-off to Review goes through ``AllWellApp._load_in_review_later``, which loads from a timer, so the
    load's own line can only name the timer. The asking is written when it happens: here the end of an Analyze run
    (``_on_analyze_pipeline_complete``), the one hand-off the app makes with nobody pressing anything."""
    import startup.boot_diag as boot_diag
    from allwell import AllWellApp

    lines: list[str] = []
    real = boot_diag.boot
    monkeypatch.setattr(boot_diag, "boot", lambda msg: (lines.append(msg), real(msg)))
    win = AllWellApp(None)
    try:
        win._on_analyze_pipeline_complete(mini_dataset)
        qtbot.waitUntil(lambda: any(ln.startswith("dataset load: opening ") for ln in lines), timeout=20000)
    finally:
        win.close()
    asked = [ln for ln in lines if ln.startswith(f"dataset load: {mini_dataset} asked for")]
    assert len(asked) == 1 and "allwell.py:" in asked[0] and " _on_analyze_pipeline_complete" in asked[0], asked
    opening = next(ln for ln in lines if ln.startswith("dataset load: opening "))
    assert lines.index(asked[0]) < lines.index(opening)
