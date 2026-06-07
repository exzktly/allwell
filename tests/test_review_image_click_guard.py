"""A click in the Segmentation tab must never hard-crash the app.

Under PySide6 an exception escaping a Qt event handler (mousePress/Move/Release,
which the review-image label overrides) aborts the whole process. The handlers
are wrapped by _guard_event, which logs and returns None instead. These tests
feed deliberately-broken state so the handler body raises, and assert the guard
swallows it (returns None) rather than propagating."""
import logging

from well_viewer.features.review_image import controller


class _Bad:
    """Missing every attribute the handlers touch → forces an AttributeError
    inside the handler body."""


def test_click_handler_never_raises_on_bad_state():
    assert controller.on_review_image_click(_Bad(), object(), logging.getLogger("t")) is None


def test_press_handler_never_raises_on_bad_state():
    assert controller.on_review_image_press(_Bad(), object()) is None


def test_release_handler_never_raises_on_bad_state():
    assert controller.on_review_image_release(_Bad(), object()) is None


def test_move_handler_never_raises_on_bad_state():
    assert controller.on_review_image_move(_Bad(), object()) is None
