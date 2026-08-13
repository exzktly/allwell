"""The fold-change request object threaded through every collection path.

Historically this was a bare ``(vs_control_on, control_label, vs_t0_on)``
tuple, unpacked positionally at roughly ten call sites across the bar
collector, the line collector, the three batch-export paths and the CSV
export service. Per-sample controls need three more pieces of information —
whether the per-member mapping is in force, the resolved mapping itself, and
how a plotted control should be rendered — and a tuple cannot grow without
breaking every one of those unpack sites at once.

So the tuple becomes this frozen dataclass, which still *behaves* as the old
three-tuple: it unpacks to three values, compares equal to a three-tuple, and
supports positional indexing. That compatibility is deliberate rather than
incidental. It keeps the roughly sixty existing fold-change tests, the batch
panels' ``(False, "", False)`` literals and ``renderer``'s ``_fc_state[0]``
indexing working untouched, so the widening lands as one reviewable change
instead of a rewrite that touches every assertion in the suite.

The compatibility has one sharp edge, and it is the reason
``tests/test_guards_bite.py`` carries a case for it: code that unpacks three
values silently drops ``per_member`` and ``controls``. A collector that does
that would fall through to the un-normalised path and draw **raw** values
under a fold-change axis label — degraded output indistinguishable from good
output. Every collector must therefore branch on :attr:`per_member`
explicitly, and emit placeholders when it is set without a resolved mapping.

Qt-free by contract (``tests/test_qt_free_layers.py``) and free of
``features/`` imports (``tests/test_layering.py``): ``controls`` is typed
loosely here so this module does not have to import the pairing types.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class FoldChangeState:
    """What normalization a collection pass should apply.

    ``control_label`` names a single control for every member — the historic
    behaviour, still the default. When ``per_member`` is set that field is
    empty and ``controls`` carries a resolved per-member mapping instead;
    see :mod:`well_viewer.domain.fold_change_pairing`.
    """

    vs_control_on: bool = False
    control_label: str = ""
    vs_t0_on: bool = False
    #: True when the control is chosen per plotted member rather than globally.
    per_member: bool = False
    #: A ``PerMemberControls`` when one has been built, else ``None``. A
    #: collector handed ``per_member=True`` with this still ``None`` has not
    #: been taught about per-sample controls and must emit placeholders.
    controls: Any = None
    #: How a member that is somebody's control renders: ``"self"`` (normalised
    #: against itself, so exactly 1.0) or ``"hidden"``.
    render: str = "self"

    # ── legacy three-tuple compatibility ─────────────────────────────────────
    #
    # Every method below exists so that call sites written against the old
    # tuple keep working. Do not remove one without converting its callers.

    def __iter__(self):
        """Unpack as the legacy ``(vs_control_on, control_label, vs_t0_on)``."""
        return iter((self.vs_control_on, self.control_label, self.vs_t0_on))

    def __getitem__(self, index):
        """Index as the legacy tuple (``renderer`` reads ``[0]`` and ``[2]``)."""
        return (self.vs_control_on, self.control_label, self.vs_t0_on)[index]

    def __eq__(self, other):
        """Equal to an identical instance, or to the legacy three-tuple.

        Tuple comparison is what lets ``FC_STATE_OFF == (False, "", False)``
        and the existing test assertions stay true. It compares only the three
        legacy fields, so two states differing solely in ``per_member`` both
        equal the same tuple — acceptable, because a tuple cannot express that
        distinction in the first place, and every caller that cares reads
        ``per_member`` directly.
        """
        if isinstance(other, FoldChangeState):
            return (
                self.vs_control_on == other.vs_control_on
                and self.control_label == other.control_label
                and self.vs_t0_on == other.vs_t0_on
                and self.per_member == other.per_member
                and self.controls is other.controls
                and self.render == other.render
            )
        if isinstance(other, tuple):
            return tuple(self) == other
        return NotImplemented

    def __hash__(self):
        return hash((self.vs_control_on, self.control_label, self.vs_t0_on,
                     self.per_member, id(self.controls), self.render))

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True when either normalization axis is on."""
        return bool(self.vs_control_on or self.vs_t0_on)

    @property
    def needs_controls(self) -> bool:
        """True when this state asks for per-member controls it does not carry.

        A collector seeing this must emit placeholders rather than raw values:
        the caller requested per-sample normalization and no mapping was
        resolved, so there is no denominator for any member.
        """
        return bool(self.vs_control_on and self.per_member and self.controls is None)

    def with_controls(self, controls: Any) -> "FoldChangeState":
        """A copy carrying *controls* — how a collector attaches a mapping."""
        return replace(self, controls=controls)


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
