"""Which widgets have an alternate form, and of what kind.

A reusable widget that can take a different *form* in another layout — a stepper
with − and + beside its field, a toolbar that is hidden, a row that stacks its
label above its field — says so here, once, where it is constructed::

    form_registry.declare(self, "stepper")

and whoever is listening (the application shell) is told. This module knows
**kinds, not layouts**: it has no idea which layout will ask for the other form, and
imports nothing from the application, so ``widgets/`` can use it without breaking its rule that it
carries no All-Well knowledge. What a kind *becomes* is decided by the listener.

The declaration is made inside the widget's constructor, so a listener must not
touch the widget when it is called beyond remembering it: the widget is not
finished, and a subclass's own ``__init__`` has not run. Adapt it when it is
first shown.

A view that has a form of its own to give a widget no shared kind covers
registers an ``apply`` callable instead::

    form_registry.register(panel, panel_apply)     # kind "custom"

Everything is held weakly. A widget that is deleted drops out of
:func:`declared` — the live check is ``shiboken6.isValid``, because a wrapper can
outlive the C++ widget it wraps.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable

import shiboken6
from PySide6.QtWidgets import QWidget

from util import aw_safe

#: The kind of a widget registered with an ``apply`` callable.
CUSTOM = "custom"

#: The Qt property the kind is stored in. A property, not a Python attribute, so
#: it is there for whoever holds another wrapper of the same C++ widget.
KIND_PROPERTY = "formKind"

#: Every widget declared, in order. Weak: a registry that kept widgets alive
#: would keep every page ever built.
_declared: list[weakref.ReferenceType] = []

#: ``register``'s callables, by widget. Weak-keyed for the same reason.
_custom: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

_listeners: list[Callable[[QWidget, str], None]] = []


def declare(widget: QWidget, kind: str) -> None:
    """Say that *widget* has an alternate form of *kind*, and tell the listeners.

    Call it once, at the end of the widget's constructor. Declaring a widget
    again with the same kind is a no-op; with another kind, the new kind wins and
    the listeners are told again."""
    if not kind:
        raise ValueError("a form kind must be a non-empty word")
    if kind_of(widget) == kind:
        return
    widget.setProperty(KIND_PROPERTY, kind)
    _declared.append(weakref.ref(widget))
    for listener in list(_listeners):
        try:
            listener(widget, kind)
        except Exception:                      # a listener must not break a constructor
            aw_safe.swallowed("a form-registry listener failed")


def register(widget: QWidget, apply: Callable[[bool], None]) -> None:
    """Give *widget* a form of its own: ``apply(True)`` puts it in the alternate
    form, ``apply(False)`` back in the default one. Kind :data:`CUSTOM`."""
    _custom[widget] = apply
    declare(widget, CUSTOM)


def kind_of(widget: QWidget) -> str | None:
    """The kind *widget* was declared with, or ``None``."""
    try:
        kind = widget.property(KIND_PROPERTY)
    except RuntimeError:                      # the C++ widget is gone
        return None
    return kind if isinstance(kind, str) and kind else None


def custom_apply(widget: QWidget) -> Callable[[bool], None] | None:
    """The callable :func:`register` stored for *widget*, or ``None``."""
    return _custom.get(widget)


def declared(kind: str | None = None) -> list[QWidget]:
    """The live declared widgets, of *kind* when given, in declaration order.

    Dead references are dropped as they are met, so the list does not grow."""
    live: list[weakref.ReferenceType] = []
    out: list[QWidget] = []
    for ref in _declared:
        widget = ref()
        if widget is None or not shiboken6.isValid(widget):
            continue
        live.append(ref)
        if kind is None or kind_of(widget) == kind:
            out.append(widget)
    _declared[:] = live
    return out


def add_listener(fn: Callable[[QWidget, str], None]) -> None:
    """Call ``fn(widget, kind)`` on every later declaration. Widgets declared
    before *fn* was added are not replayed: ask :func:`declared` for them."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[QWidget, str], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)
