"""``widgets/form_registry``: kinds declared where a widget is built.

PHONE_FORM_TRACKER.md P1.1. Real widgets throughout. Each test names the wrong
implementation it rules out.
"""

from __future__ import annotations

import gc

import pytest
from PySide6.QtWidgets import QWidget

from widgets import form_registry as fr


@pytest.fixture(autouse=True)
def _clean_registry():
    """A test sees only its own declarations and listeners."""
    before = list(fr._listeners)
    fr._declared.clear()
    yield
    fr._listeners[:] = before
    fr._declared.clear()


def _pump(qapp, n=5):
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(n):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_declaring_stores_the_kind_on_the_widget(qapp):
    """A registry that kept the kind only in a Python dict would lose it for a
    second wrapper of the same widget."""
    w = QWidget()
    assert fr.kind_of(w) is None
    fr.declare(w, "stepper")
    assert fr.kind_of(w) == "stepper"
    assert w.property("formKind") == "stepper"


def test_a_listener_is_told_once_with_the_widget_and_its_kind(qapp):
    """Not called per listener-add, not called twice for one declaration."""
    seen = []
    fr.add_listener(lambda w, k: seen.append((w, k)))
    w = QWidget()
    fr.declare(w, "toolbar")
    fr.declare(w, "toolbar")                      # the same kind again: nothing new to say
    assert seen == [(w, "toolbar")]


def test_declaring_another_kind_tells_the_listeners_again(qapp):
    seen = []
    fr.add_listener(lambda w, k: seen.append(k))
    w = QWidget()
    fr.declare(w, "toolbar")
    fr.declare(w, "controls")
    assert seen == ["toolbar", "controls"]
    assert fr.kind_of(w) == "controls"


def test_a_listener_added_later_is_not_replayed(qapp):
    """The shell asks :func:`declared` for what exists; a replay would adapt a
    widget twice."""
    w = QWidget()
    fr.declare(w, "stepper")
    seen = []
    fr.add_listener(lambda w, k: seen.append(k))
    assert seen == []
    assert fr.declared("stepper") == [w]


def test_a_listener_that_raises_does_not_break_the_constructor_or_the_others(qapp):
    seen = []

    def bad(_w, _k):
        raise RuntimeError("boom")

    fr.add_listener(bad)
    fr.add_listener(lambda w, k: seen.append(k))
    w = QWidget()
    fr.declare(w, "stepper")                      # must not raise
    assert seen == ["stepper"]
    assert fr.kind_of(w) == "stepper"


def test_declared_lists_by_kind_in_declaration_order(qapp):
    a, b, c = QWidget(), QWidget(), QWidget()
    fr.declare(a, "stepper")
    fr.declare(b, "toolbar")
    fr.declare(c, "stepper")
    assert fr.declared("stepper") == [a, c]
    assert fr.declared("toolbar") == [b]
    assert fr.declared() == [a, b, c]
    assert fr.declared("canvas") == []


def test_a_deleted_widget_leaves_declared_even_while_its_wrapper_lives(qapp):
    """The wrapper can outlive the C++ widget; a list that only checked the
    wrapper would hand the shell a widget that raises on first use."""
    parent = QWidget()
    child = QWidget(parent)
    fr.declare(child, "stepper")
    assert fr.declared("stepper") == [child]
    child.deleteLater()
    _pump(qapp)
    assert fr.declared("stepper") == []
    # The unfiltered list is the one that needs the validity check: a kind
    # filter drops a dead widget by itself, because its kind reads as None.
    assert fr.declared() == []
    assert fr.kind_of(child) is None              # a dead widget has no kind, and no exception


def test_a_collected_widget_leaves_declared(qapp):
    """The registry must not keep a page alive."""
    w = QWidget()
    fr.declare(w, "stepper")
    del w
    gc.collect()
    assert fr.declared() == []


def test_register_records_an_apply_callable_under_kind_custom(qapp):
    calls = []
    w = QWidget()
    fr.register(w, calls.append)
    assert fr.kind_of(w) == "custom"
    fr.custom_apply(w)(True)
    fr.custom_apply(w)(False)
    assert calls == [True, False]
    assert fr.custom_apply(QWidget()) is None


def test_an_empty_kind_is_refused(qapp):
    with pytest.raises(ValueError):
        fr.declare(QWidget(), "")


def test_a_removed_listener_is_not_called(qapp):
    seen = []

    def listener(w, k):
        seen.append(k)

    fr.add_listener(listener)
    fr.remove_listener(listener)
    fr.declare(QWidget(), "stepper")
    assert seen == []


# ── the shell listens (P1.2) ────────────────────────────────────────────────

class _TouchWidget(QWidget):
    """A widget the way ``Stepper`` is: a touch form it can be put in, declared
    at the end of its constructor."""

    def __init__(self, parent=None, *, declare=True):
        super().__init__(parent)
        self._touch = False
        if declare:
            fr.declare(self, "stepper")

    def setTouchMode(self, on):
        self._touch = bool(on)

    def isTouchMode(self):
        return self._touch


@pytest.fixture
def shell(qapp):
    """A real ``PhoneShell`` with the phone flag on and **no sweep running**, so
    the only thing that can adapt a widget is what the declaration set up."""
    from core import phone_layout
    from ui.phone_shell import PhoneHandles, PhoneShell

    host = QWidget()
    host.resize(400, 700)
    shell = PhoneShell(host, PhoneHandles())
    shell.desk_button.setParent(host)
    shell._rail_status.setParent(host)
    shell._sweep_timer.stop()
    host.show()                                   # children get Show events only under a shown parent
    phone_layout.set_on(True)
    try:
        yield shell
    finally:
        phone_layout.set_on(False)
        host.close()
        host.deleteLater()


def test_a_widget_declared_after_the_switch_is_adapted_when_it_is_shown(qapp, shell):
    """The case the 300 ms sweep exists for — a page built after the switch —
    with the sweep off. Wrong implementation: a shell that listens but only
    remembers the widget, or one that adapts inside the constructor (the
    widget's own ``__init__`` had not finished, so ``_touch`` was not there)."""
    w = _TouchWidget(shell._win)
    assert not w.isTouchMode(), "the shell adapted a widget inside its own constructor"
    w.show()
    _pump(qapp)
    assert w.isTouchMode()


def test_a_widget_that_is_not_declared_is_left_alone_with_the_sweep_off(qapp, shell):
    """Rules out a pass: the first test would also pass if every widget with a
    ``setTouchMode`` were adapted by something else."""
    w = _TouchWidget(shell._win, declare=False)
    w.show()
    _pump(qapp)
    assert not w.isTouchMode()


def test_nothing_is_watched_while_the_desk_layout_is_on(qapp, shell):
    from core import phone_layout
    phone_layout.set_on(False)
    w = _TouchWidget(shell._win)
    w.show()
    _pump(qapp)
    assert not w.isTouchMode()
    assert not w.property("_awPhoneWatched")


def test_a_declared_kind_is_read_before_any_class_name(qapp, shell):
    """A widget the shell would not recognise by name still has its kind
    adapted, and a registered custom form is handed the mode both ways."""
    from ui.phone_shell import _adapt_kind
    calls = []
    w = QWidget(shell._win)
    fr.register(w, calls.append)
    assert _adapt_kind(w) == "custom"
    shell.adapt(w, True)
    shell.adapt(w, False)
    assert calls == [True, False]


def test_a_shell_that_is_gone_leaves_no_listener(qapp):
    from ui.phone_shell import PhoneHandles, PhoneShell
    before = len(fr._listeners)
    host = QWidget()
    shell = PhoneShell(host, PhoneHandles())
    assert len(fr._listeners) == before + 1
    del shell
    host.deleteLater()
    _pump(qapp)
    gc.collect()
    fr.declare(QWidget(), "stepper")              # a stale listener removes itself when called
    assert len(fr._listeners) == before


# ── the shared kinds declare themselves (P2) ────────────────────────────────

def test_a_stepper_built_after_the_switch_takes_its_touch_form_without_a_sweep(qapp, shell):
    """The real ``Stepper``, with nothing recognising it by name. Wrong
    implementation: a stepper that does not declare, which the sweep used to
    find by its class name and now nothing does."""
    from widgets.stepper import Stepper

    st = Stepper(shell._win, value=3.0, minimum=0.0, maximum=10.0)
    assert fr.kind_of(st) == "stepper"
    assert not st.isTouchMode()
    st.show()
    _pump(qapp)
    assert st.isTouchMode()
