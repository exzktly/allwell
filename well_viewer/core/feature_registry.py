"""The Review-side feature registry — the seam that makes tab-features
non-destructively removable.

A ``FeatureDescriptor`` is the single declaration of everything the core used
to hard-code about a feature: how to build its tab, how its state slice is
constructed, and the dispatch hooks that fire when it is activated / the
selection changes / the channel changes / a dataset loads. The descriptors are
assembled once in ``well_viewer/features/registry.py`` (the *only* module
allowed to import ``well_viewer.features.*``); core reads them through a
``FeatureRegistry`` and never imports a feature directly.

This generalizes the seam ARCHITECTURE.md §5.10 already gives Split View: a
feature is fully present only while its descriptor is in the ``FEATURES`` list,
and commenting that one line removes it with no other edits — there is no
``from well_viewer.features.<x>`` left in core to break, no hard-coded tab-name
branch to leave dead, and no ``AppState`` field importing its state class.

Design mirrors the existing registries (``persistence/registry.py``,
``core/scope_registry.py``): the registry holds descriptors keyed by name; the
feature-aware ``build_feature_registry`` does the wiring. All hook callables are
lazy (they import their feature module on call), so importing the registry does
not eagerly pull heavy feature deps (TF / skimage / matplotlib-QtAgg).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class PersistenceSpec:
    """A feature-owned data-dir persistence domain.

    Mirrors a ``PersistenceRegistry.register`` call; ``build_persistence_registry``
    folds these in so feature persistence stays declared with the feature.
    """

    name: str
    save: Callable
    load: Optional[Callable] = None


@dataclass(frozen=True)
class StateSpec:
    """A feature-owned slice of ``AppState``.

    ``attr`` is the ``AppState`` field name (e.g. ``"bar"``); ``factory`` builds
    the slice object. The registry attaches these at app construction so
    ``AppState`` itself never imports a feature state class. A feature may own
    several slices (Image Browser owns ``review_image`` / ``preview`` /
    ``image_table`` / ``playground``).

    ``subtab``, when set, marks a slice that belongs to a composite parent's
    sub-tab (Image Browser's ``image_table`` / ``playground``). ``build_feature_registry``
    attaches it only when the active profile admits that sub-tab — so a subset
    build that narrows the parent's sub-tabs drops the slice (and never imports
    its physically-stripped state module) instead of crashing at startup.
    """

    attr: str
    factory: Callable
    subtab: str = ""


@dataclass(frozen=True)
class FeatureDescriptor:
    """One Review-side feature's full declaration.

    Only ``name`` is required. ``build``/``group`` are set for top-level rail
    entries (the units the centre page-stack constructs); leaf sub-tabs that
    live inside a composite container (the Plotting / Image Browser nested
    stacks) register with ``build=None`` and carry only their dispatch hooks,
    so the selection / channel / tab-activation dispatch can still resolve them
    by leaf name.

    Every hook takes the ``ViewerContext`` (``ctx``) and is optional; a ``None``
    hook means "this feature does nothing on that event". Hooks are expected to
    be self-guarding (``ctx.widgets.has(...)`` / ``getattr(ctx.window, …)`` checks
    for lazily-built widgets), exactly as the inline branches they replace were.
    """

    name: str
    group: str = ""                      # "Analysis" | "Images" | "Data"; "" = sub-leaf
    parent: str = ""                     # composite container a sub-leaf lives in
                                         # (e.g. "Plotting"); "" for top-level entries
    build: Optional[Callable] = None     # build(ctx, frame) -> None
    lazy_only: bool = False              # skip the background drain; build on first click
    # State contribution (AppState import-inversion).
    states: Tuple[StateSpec, ...] = ()
    # Dispatch hooks (each: fn(app) -> None).
    on_activate: Optional[Callable] = None
    on_selection_changed: Optional[Callable] = None
    on_channel_changed: Optional[Callable] = None
    on_dataset_loaded: Optional[Callable] = None
    # Feature-owned figure redraw. Lazy factory returning the redraw callable:
    # ``() -> ((ctx) -> None)``. Plot orchestration (redraw_app, tab activation)
    # looks the feature up by name and calls its ``redraw`` so ``core/`` never
    # imports the feature renderer (B1b / terminal-goal cond. 3).
    redraw: Optional[Callable] = None
    # Persistence + fold-change contributions.
    persistence: Tuple[PersistenceSpec, ...] = ()
    fold_change_scope_factory: Optional[Callable] = None   # () -> FoldChangeScope
    # A feature that decorates every plot card (e.g. Saved Plots' "save to
    # gallery" hover button). Lazy: ``() -> ((ctx, card, tab_name) -> None)``.
    # Plot tabs apply all registered decorators, so they never name the feature.
    plot_card_decorator: Optional[Callable] = None
    # A feature that owns its own view-state (per-tab) serialization, so
    # ``persistence/view_state.py`` never names it. Lazy factory returning a
    # ``(snapshot, apply)`` pair: ``snapshot(ctx) -> dict`` (stored under the
    # descriptor name in the ``tabs`` block) and ``apply(ctx, state) -> None``.
    view_state_io: Optional[Callable] = None
    # Selection-behaviour flags (replace the hard-coded tab-name string sets).
    per_well_selection: bool = False     # was membership in _PER_WELL_TABS
    single_well_selection: bool = False  # was the smFISH single-well check


class FeatureRegistry:
    """Name-keyed collection of ``FeatureDescriptor`` objects.

    Insertion order is preserved so fan-out (channel / dataset-load refresh)
    runs in a deterministic, declaration-driven order.
    """

    def __init__(self, descriptors=()) -> None:
        self._ordered: list[FeatureDescriptor] = []
        self._by_name: dict[str, FeatureDescriptor] = {}
        for d in descriptors:
            self.add(d)

    def add(self, descriptor: FeatureDescriptor) -> None:
        if descriptor.name in self._by_name:
            raise ValueError(f"duplicate feature descriptor: {descriptor.name!r}")
        self._ordered.append(descriptor)
        self._by_name[descriptor.name] = descriptor

    def get(self, name: str) -> Optional[FeatureDescriptor]:
        return self._by_name.get(name)

    def all(self) -> "list[FeatureDescriptor]":
        return list(self._ordered)

    def top_level(self) -> "list[FeatureDescriptor]":
        """Descriptors that own a top-level rail entry (have a group).

        The page-stack enumerates these; a descriptor with ``build=None`` (a
        bespoke composite container such as Plotting) resolves its builder from
        the page-stack's local fallback map.
        """
        return [d for d in self._ordered if d.group]

    def in_group(self, group: str) -> "list[FeatureDescriptor]":
        return [d for d in self.top_level() if d.group == group]

    def with_hook(self, attr: str) -> "list[FeatureDescriptor]":
        """Descriptors whose *attr* hook is set, in declaration order."""
        return [d for d in self._ordered if getattr(d, attr, None) is not None]

    def redraw(self, ctx, name: str) -> bool:
        """Invoke feature *name*'s registered ``redraw`` callable, if any.

        Lets ``core/`` orchestration (``redraw_app`` / tab activation) trigger a
        plot feature's figure redraw by name without importing its renderer.
        Returns ``True`` when a redraw ran, ``False`` when the feature is absent
        (e.g. profiled out) or has no redraw hook."""
        d = self._by_name.get(name)
        if d is None or d.redraw is None:
            return False
        d.redraw()(ctx)
        return True

    def sub_leaves(self, parent: str) -> "list[str]":
        """Names of the sub-leaf descriptors under composite *parent*
        (e.g. ``"Plotting"`` → the plot sub-tab names), in declaration order.

        Lets composite containers enumerate their sub-tabs from the registry
        instead of a hard-coded list, so a profile that drops one sub-leaf drops
        its sub-tab automatically."""
        return [d.name for d in self._ordered if d.parent == parent]
