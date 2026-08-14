"""The fold-change request object threaded through every collection path.

Historically this was a bare ``(vs_control_on, control_label, vs_t0_on)``
tuple, unpacked positionally at roughly ten call sites across the bar
collector, the line collector, the three batch-export paths and the CSV export
service. Per-sample controls need to carry more than three things, and a tuple
cannot grow without every one of those unpack sites silently dropping whatever
was appended.

So it is a frozen dataclass, read by **name** everywhere. It briefly also
emulated the old tuple — iterating, indexing and comparing equal to one — so
the widening could land without touching the call sites. That emulation is
gone, because it was a trap in two directions:

* it broke Python's ``a == b implies hash(a) == hash(b)`` contract, so a state
  compared equal to the tuple while hashing differently, and ``fc in {tuple}``
  was False. Nothing keyed a dict on it *yet*, which is exactly the kind of
  latent breakage that surfaces long after the change that caused it;
* it made "unpack three values" keep working, so a call site that had not been
  taught about a new field would drop it in silence rather than fail. For this
  object that failure mode is not abstract: dropping the field that says
  "resolve a control per member" produces raw values drawn under a fold-change
  axis label — degraded output indistinguishable from good output.

``coerce_fc_state`` still *accepts* a legacy three-tuple, because a large
number of tests construct one inline, but nothing hands one back out.

Qt-free by contract (``tests/test_qt_free_layers.py``) and free of
``features/`` imports (``tests/test_layering.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FoldChangeState:
    """What normalization a collection pass should apply.

    ``control_label`` names the control every member is divided by; an empty
    string means the control axis has nothing to resolve. Equality and hashing
    are the dataclass defaults, so this is safe as a dict key and in a set.
    """

    vs_control_on: bool = False
    control_label: str = ""
    vs_t0_on: bool = False

    @property
    def active(self) -> bool:
        """True when either normalization axis is on."""
        return bool(self.vs_control_on or self.vs_t0_on)

    @property
    def wants_control(self) -> bool:
        """True when a control denominator is both requested and named.

        The condition every collector guards its control lookup with. Keeping
        it here rather than re-spelling ``vs_control_on and control_label`` at
        each site is what stops the two halves drifting apart.
        """
        return bool(self.vs_control_on and self.control_label)


#: The "give me raw values" sentinel. Frozen, so sharing one module-level
#: instance is safe; callers pass it to skip normalization entirely (the
#: additive CSV emitter builds both a raw and a normalized pass this way).
FC_STATE_OFF = FoldChangeState()


def coerce_fc_state(value: Any) -> FoldChangeState:
    """Normalise whatever a caller passed as ``fc_state`` into an instance.

    Accepts an instance (returned unchanged), a legacy
    ``(vs_control_on, control_label, vs_t0_on)`` tuple, or ``None``. ``None``
    means "read the live state" and is **not** resolved here — this module
    knows nothing about ``ctx`` — so it returns the raw-values default and the
    caller is responsible for having substituted ``fold_change_state(ctx)``
    first. Every collector does exactly that before calling this.

    The tuple form is an input convenience for the many tests that spell a
    fold-change request inline. Nothing produces one.
    """
    if isinstance(value, FoldChangeState):
        return value
    if value is None:
        return FoldChangeState()
    if isinstance(value, (tuple, list)):
        vs_ctrl, label, vs_t0 = value
        return FoldChangeState(
            vs_control_on=bool(vs_ctrl),
            control_label=str(label or ""),
            vs_t0_on=bool(vs_t0),
        )
    raise TypeError(
        f"fc_state must be a FoldChangeState, a 3-tuple or None, got {type(value)!r}"
    )
