"""A status line belongs to the tab that wrote it (NANOWELL_OBSERVATIONS_TRACKER #19).

Opening Segmentation after generating an Image Table left "Image Table: generated 0 of 12 cells…" in the status
bar, and every Image Browser tab showed the Line plot's "36 of 36 nuclei included": the line describes a tab that is
not on screen. A tab change now clears a line another tab wrote; the new tab writes its own.
"""

from __future__ import annotations

from types import SimpleNamespace

from review.core import notifications, tab_controller


def _service(monkeypatch, tab: list[str]):
    shown: list[str] = []
    ctx = SimpleNamespace(window=SimpleNamespace(_set_status=shown.append))
    monkeypatch.setattr(tab_controller, "current_centre_tab", lambda _ctx: tab[0])
    return notifications.NotificationService(ctx), shown


def test_another_tab_s_line_is_cleared_and_the_tab_s_own_is_kept(monkeypatch):
    tab = ["Image Table"]
    svc, shown = _service(monkeypatch, tab)
    svc.set_status("Image Table: generated 8 of 12 cell(s).")
    assert svc.status_owner == "Image Table"
    assert not svc.clear_unless_owned_by("Image Table") and shown == ["Image Table: generated 8 of 12 cell(s)."]
    assert svc.clear_unless_owned_by("Segmentation")
    assert shown[-1] == "" and svc.status_owner is None
    assert not svc.clear_unless_owned_by("Bar Plots")          # nothing left to clear


def test_the_tab_change_handler_clears_it(monkeypatch):
    tab = ["Line Graphs"]
    svc, shown = _service(monkeypatch, tab)
    svc.set_status("36 of 36 nuclei included")
    tab[0] = "Segmentation"
    calls = []
    monkeypatch.setattr(tab_controller, "current_centre_tab", lambda _ctx: tab[0])
    ctx = SimpleNamespace(notify=svc, runtime=SimpleNamespace(tab=SimpleNamespace(last_tab_name="Line Graphs")),
                          state=SimpleNamespace(selection=SimpleNamespace(selected_wells=set())))
    try:
        tab_controller.on_tab_change(ctx)
    except AttributeError:
        calls.append("stopped after the clear")       # the rest of the handler needs a real window
    assert shown[-1] == "" and svc.status_owner is None


def test_a_line_written_past_the_service_belongs_to_no_tab_and_stays(monkeypatch):
    """Review finding: the loader writes "Loaded N well(s)" straight to the label; the next tab change cleared it
    because the previous tab's ownership was still recorded."""
    tab = ["Image Table"]
    shown: list[str] = []
    label = SimpleNamespace(value="", text=lambda: label.value)

    def write(message):
        shown.append(message)
        label.value = message

    ctx = SimpleNamespace(window=SimpleNamespace(_set_status=write, _status_lbl=label))
    monkeypatch.setattr(tab_controller, "current_centre_tab", lambda _ctx: tab[0])
    svc = notifications.NotificationService(ctx)
    svc.set_status("Image Table: generated 8 of 12 cell(s).")
    write("Loaded 2 well(s)")                       # the loader, past the service
    assert not svc.clear_unless_owned_by("Segmentation")
    assert label.value == "Loaded 2 well(s)" and svc.status_owner is None
