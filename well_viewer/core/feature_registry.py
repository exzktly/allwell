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

from dataclasses import dataclass, field
from collections.abc import Callable, Mapping


@dataclass(frozen=True)
class PersistenceSpec:
    """A feature-owned data-dir persistence domain.

    Mirrors a ``PersistenceRegistry.register`` call; ``build_persistence_registry``
    folds these in so feature persistence stays declared with the feature.
    """

    name: str
    save: Callable
    load: Callable | None = None


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
    build: Callable | None = None     # build(ctx, frame) -> None
    lazy_only: bool = False              # skip the background drain; build on first click
    # State contribution (AppState import-inversion).
    states: tuple[StateSpec, ...] = ()
    # Dispatch hooks (each: fn(app) -> None).
    on_activate: Callable | None = None
    on_selection_changed: Callable | None = None
    on_channel_changed: Callable | None = None
    on_dataset_loaded: Callable | None = None
    # Feature-owned figure redraw. Lazy factory returning the redraw callable:
    # ``() -> ((ctx) -> None)``. Plot orchestration (redraw_app, tab activation)
    # looks the feature up by name and calls its ``redraw`` so ``core/`` never
    # imports the feature renderer (B1b / terminal-goal cond. 3).
    redraw: Callable | None = None
    # Persistence + fold-change contributions.
    persistence: tuple[PersistenceSpec, ...] = ()
    fold_change_scope_factory: Callable | None = None   # () -> FoldChangeScope
    # A feature that decorates every plot card (e.g. Saved Plots' "save to
    # gallery" hover button). Lazy: ``() -> ((ctx, card, tab_name) -> None)``.
    # Plot tabs apply all registered decorators, so they never name the feature.
    plot_card_decorator: Callable | None = None
    # A feature that owns its own view-state (per-tab) serialization, so
    # ``persistence/view_state.py`` never names it. Lazy factory returning a
    # ``(snapshot, apply)`` pair: ``snapshot(ctx) -> dict`` (stored under the
    # descriptor name in the ``tabs`` block) and ``apply(ctx, state) -> None``.
    view_state_io: Callable | None = None
    # Named callables this feature offers to ``core/``, keyed ``"<area>.<verb>"``
    # (e.g. ``"labels.grid_refresh"``). Each value is a **lazy factory** —
    # ``() -> callable`` — exactly like ``redraw``, so declaring a service does
    # not import the feature module until something calls it.
    #
    # This is the seam for the calls a fan-out hook cannot express: a refresh
    # aimed at one named feature rather than broadcast to all of them, a builder,
    # and above all a **query** — a call that returns a value, which a
    # ``fn(ctx) -> None`` hook has nowhere to put. Core reaches them through
    # ``ctx.features.call`` / ``.query`` and so names no feature module. A
    # profile that drops the feature drops its services with it, which is what
    # makes the call sites survive a subset build: today they survive only
    # because a widget-presence check happens to return first, which is an
    # accident of layout rather than a contract.
    services: Mapping[str, Callable] = field(default_factory=dict)
    # Selection-behaviour flags (replace the hard-coded tab-name string sets).
    per_well_selection: bool = False     # was membership in _PER_WELL_TABS
    single_well_selection: bool = False  # was the smFISH single-well check
    # Whether this SUB-tab reads the rail plate's well selection, so the plate
    # is interactive while it is showing and greyed while it is not. Finer than
    # ``per_well_selection``, which is resolved per *page* (``current_centre_tab``
    # stops at Home) and answers whether the page keeps per-well selection at all.
    # Home is per-well as a page, yet only three of its five editors ask the
    # plate anything -- Groups edits a group's membership, Cell Gating draws its
    # CDFs from the selected wells, Well Labels' Add Prefix / Add Suffix apply to
    # them; Ratios and Pipeline Info do not.
    reads_well_selection: bool = False
    # Whether this sub-tab's frame takes a gutter. The two editable grids want
    # one; the report and the read-only panes paint to their own edges. The
    # descriptor carries the *decision* and the container supplies the spacing
    # token, so the registry never imports ``theme``.
    subtab_inset: bool = False
    # Whether a build profile may drop this descriptor. ``False`` marks a
    # sub-leaf the app cannot run without — its code is never stripped and its
    # tab is never narrowed away by a ``[subtabs]`` allowlist.
    #
    # It exists because a container that enumerates its sub-tabs from the
    # registry hands the profile a lever over *every* leaf, including ones other
    # code reaches unconditionally. Home's Groups tab is the case in point:
    # ``centre_view.build_centre`` builds the rail plate that edits it at
    # startup with no descriptor check, so a profile dropping the tab would
    # leave a plate editing something that is not there. Declaring the tab and
    # then relying on nobody excluding it is not a contract; this is.
    removable: bool = True


class FeatureRegistry:
    """Name-keyed collection of ``FeatureDescriptor`` objects.

    Insertion order is preserved so fan-out (channel / dataset-load refresh)
    runs in a deterministic, declaration-driven order.
    """

    def __init__(self, descriptors=()) -> None:
        self._ordered: list[FeatureDescriptor] = []
        self._by_name: dict[str, FeatureDescriptor] = {}
        self._services: dict[str, Callable] = {}
        self._service_owner: dict[str, str] = {}
        for d in descriptors:
            self.add(d)

    def add(self, descriptor: FeatureDescriptor) -> None:
        if descriptor.name in self._by_name:
            raise ValueError(f"duplicate feature descriptor: {descriptor.name!r}")
        for key, factory in descriptor.services.items():
            owner = self._service_owner.get(key)
            if owner is not None:
                raise ValueError(
                    f"duplicate feature service {key!r}: declared by both "
                    f"{owner!r} and {descriptor.name!r}. A service key is how "
                    f"core addresses one feature, so two claimants means the "
                    f"call site reaches whichever loaded first."
                )
            self._services[key] = factory
            self._service_owner[key] = descriptor.name
        self._ordered.append(descriptor)
        self._by_name[descriptor.name] = descriptor

    def register_service(self, key: str, factory: Callable, owner: str) -> None:
        """Register a service that belongs to no descriptor.

        For the **support libraries** — QC, crop, the export-style panel — which
        are always present, own no tab and therefore carry no descriptor, but
        which ``core/`` still has to reach. They go in the same lookup as the
        descriptor-declared services so there is one answer to "what can core
        call by name", and so the partition guard sees them.

        *owner* names the library, for the duplicate-key message.
        """
        prior = self._service_owner.get(key)
        if prior is not None:
            raise ValueError(
                f"duplicate feature service {key!r}: declared by both "
                f"{prior!r} and {owner!r}."
            )
        self._services[key] = factory
        self._service_owner[key] = owner

    def service(self, key: str) -> Callable | None:
        """The callable registered under *key*, or ``None`` if no present
        feature offers it.

        ``None`` is the answer for a profile that dropped the feature, so every
        caller has to decide what absence means rather than discovering it as an
        ``ImportError`` from a stripped tree.
        """
        factory = self._services.get(key)
        return None if factory is None else factory()

    def service_keys(self) -> list[str]:
        """Every registered service key, in declaration order."""
        return list(self._services)

    def call(self, key: str, *args, **kwargs) -> bool:
        """Invoke the void service *key*, if a present feature offers it.

        Returns whether it ran. The return value exists so a caller that needs
        to *know* — rather than one refreshing a widget that may not be built —
        can say so; ignoring it is the right thing for a refresh whose feature is
        legitimately absent.
        """
        fn = self.service(key)
        if fn is None:
            return False
        fn(*args, **kwargs)
        return True

    def query(self, key: str, *args, default=None, **kwargs):
        """Invoke the service *key* for its **value**, or return *default*.

        The form for a call whose answer core needs. *default* is required
        reading at the call site: it is what the app believes when the feature
        that would have answered is not in this build.
        """
        fn = self.service(key)
        if fn is None:
            return default
        return fn(*args, **kwargs)

    def get(self, name: str) -> FeatureDescriptor | None:
        return self._by_name.get(name)

    def all(self) -> list[FeatureDescriptor]:
        return list(self._ordered)

    def top_level(self) -> list[FeatureDescriptor]:
        """Descriptors that own a top-level rail entry (have a group).

        The page-stack enumerates these; a descriptor with ``build=None`` (a
        bespoke composite container such as Plotting) resolves its builder from
        the page-stack's local fallback map.
        """
        return [d for d in self._ordered if d.group]

    def in_group(self, group: str) -> list[FeatureDescriptor]:
        return [d for d in self.top_level() if d.group == group]

    def with_hook(self, attr: str) -> list[FeatureDescriptor]:
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

    def sub_leaves(self, parent: str) -> list[str]:
        """Names of the sub-leaf descriptors under composite *parent*
        (e.g. ``"Plotting"`` → the plot sub-tab names), in declaration order.

        Lets composite containers enumerate their sub-tabs from the registry
        instead of a hard-coded list, so a profile that drops one sub-leaf drops
        its sub-tab automatically."""
        return [d.name for d in self._ordered if d.parent == parent]
