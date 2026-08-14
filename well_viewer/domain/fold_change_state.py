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

from well_viewer.domain.control_ref import ControlRef


@dataclass(frozen=True)
class FoldChangeState:
    """What normalization a collection pass should apply.

    ``control`` is the reference every member is divided by, or ``None`` when
    the control axis has nothing to resolve. It is a
    :class:`~well_viewer.domain.control_ref.ControlRef` rather than a string
    because a string cannot say whether ``A01`` means a well or a replicate set
    of the same name. Equality and hashing are the dataclass defaults, so this
    is safe as a dict key and in a set.
    """

    vs_control_on: bool = False
    control: ControlRef | None = None
    vs_t0_on: bool = False
    #: ``"single"`` — ``control`` divides everything. ``"per_member"`` —
    #: ``control_map`` gives each plotted member its own.
    control_mode: str = "single"
    #: ``((member key, ControlRef), …)``, sorted. A tuple rather than a dict so
    #: the state stays hashable; :attr:`control_map_dict` is the lookup form.
    control_map: tuple = ()
    #: What a member that is itself somebody's control draws as — ``"self"``
    #: or ``"hidden"``.
    control_render: str = "self"

    @property
    def active(self) -> bool:
        """True when either normalization axis is on."""
        return bool(self.vs_control_on or self.vs_t0_on)

    @property
    def per_member(self) -> bool:
        """True when each member resolves its own control."""
        return self.control_mode == "per_member"

    @property
    def control_map_dict(self) -> dict:
        return dict(self.control_map)

    @property
    def wants_control(self) -> bool:
        """True when a control denominator is requested and there is one to find.

        The condition every collector guards its control lookup with. Keeping
        it here rather than re-spelling ``vs_control_on and control`` at each
        site is what stops the two halves drifting apart.

        In per-member mode this is True even with an empty mapping, and that is
        deliberate: "the user asked for per-sample controls and mapped nobody"
        is a state to report, member by member, not a quiet fall-through to raw
        values under a fold-change axis.
        """
        if not self.vs_control_on:
            return False
        if self.per_member:
            return True
        return self.control is not None


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
            # A tuple's middle element predates the kind being recorded, so it
            # is read as a legacy reference and resolved by the old precedence.
            control=ControlRef.coerce(label),
            vs_t0_on=bool(vs_t0),
        )
    raise TypeError(
        f"fc_state must be a FoldChangeState, a 3-tuple or None, got {type(value)!r}"
    )
