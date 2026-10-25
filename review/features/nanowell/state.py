"""The Nanowells page's state: what the user chose (persisted) and the data computed from the dataset (not).

Qt-free and stdlib-only. The persisted subset is :meth:`NanowellState.to_config`; :meth:`apply_config` accepts
only values it recognises, so a block written by another version, or edited by hand, cannot put the page in
a state its controls cannot show.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

BASELINES = ("earliest", "first_measurable")
AXES = ("time", "wells")            # what the Distribution view's x-axis carries
VIEWS = ("distribution", "trajectories", "array", "position")
#: Views of earlier versions, and the view each is now. A page saved on Array map or Array QC opens on the Array
#: view with the layers that view drew (:meth:`ArrayLayers.preset`).
_RENAMED_VIEWS = {"array_map": "array", "array_qc": "array"}
#: The page's metrics (``distribution_compute.METRICS`` holds their labels; a test holds the two equal).
METRIC_KEYS = ("n", "c", "brightness", "signal", "count_fc", "log2_count_fc", "confluence_fc", "brightness_fc",
               "signal_fc", "growth_rate", "occupancy_class")


#: What the Array view draws under the nanowells: nothing, the run's downsampled montage, or the raw tiles at
#: whatever resolution the view asks for.
IMAGES = ("off", "montage", "tiles")
#: Where a nanowell is drawn: where the run measured it (the well frame, where the image and tiles are), or at
#: its address on an ideal lattice, where it keeps its place from one timepoint to the next (decision U4).
POSITIONS = ("measured", "lattice")
#: What a nanowell's fill says: the page's metric, or the state the run recorded for it.
COLOURINGS = ("metric", "state")
#: The layers that are on or off, as :class:`ArrayLayers` names them.
LAYER_SWITCHES = ("nanowells", "addresses", "values", "cells", "tiles", "anchor", "record")
#: The layers drawn in colour over the image, which the page's one *Overlays* button takes off and puts back
#: together (:meth:`ArrayLayers.without_overlays`). The labels are text, not colour, and keep their own switches.
OVERLAY_SWITCHES = ("nanowells", "cells", "tiles", "anchor")


@dataclass
class ArrayLayers:
    """What the Array view draws. One view, every element switchable: what used to be the Array map, Array QC and
    the Full montage window are three settings of this (:meth:`preset`)."""
    image: str = "montage"
    positions: str = "measured"
    colour: str = "metric"
    nanowells: bool = True                  # the nanowells, coloured by :attr:`colour`
    addresses: bool = True                  # each nanowell's ``q,r``, where it is drawn large enough to hold it
    values: bool = True                     # each nanowell's value of the page's metric, written the same way
    cells: bool = False                     # a dot at each counted cell's centre, from the run's cell table
    tiles: bool = False                     # each tile's placement, coloured by how the run placed it
    anchor: bool = True                     # the anchor set by hand, and the grid status that says whether to set one
    record: bool = False                    # the grid record of every timepoint, beside the figure

    PRESETS = ("map", "qc", "full")

    @classmethod
    def preset(cls, name: str) -> ArrayLayers:
        """The layers of one of the views this one replaced: ``map`` (lattice places coloured by the metric),
        ``qc`` (the montage, the tiles by how they were placed, the nanowells by state, the grid record) or
        ``full`` (the raw tiles at full resolution, the nanowells by state with their addresses, the anchor)."""
        if name == "map":
            return cls(image="off", positions="lattice", colour="metric", addresses=False, values=False,
                       tiles=False, anchor=False, record=False)
        if name == "qc":
            return cls(image="montage", positions="measured", colour="state", addresses=False, values=False,
                       tiles=True, anchor=False, record=True)
        if name == "full":
            return cls(image="tiles", positions="measured", colour="state", addresses=True, tiles=False,
                       anchor=True, record=False)
        return cls()

    def lattice(self) -> bool:
        return self.positions == "lattice"

    def without_overlays(self) -> ArrayLayers:
        """These layers with every coloured overlay off (``OVERLAY_SWITCHES``): what the view draws while the
        *Overlays* button hides them. The switches themselves are untouched, so showing them again restores
        exactly what was on."""
        return replace(self, **{name: False for name in OVERLAY_SWITCHES})

    def to_config(self) -> dict:
        return {"image": self.image, "positions": self.positions, "colour": self.colour,
                **{name: getattr(self, name) for name in LAYER_SWITCHES}}

    @classmethod
    def from_config(cls, config) -> ArrayLayers:
        """Each recognised value of *config*; anything else keeps its default."""
        out = cls()
        config = config if isinstance(config, dict) else {}
        for name, allowed in (("image", IMAGES), ("positions", POSITIONS), ("colour", COLOURINGS)):
            if config.get(name) in allowed:
                setattr(out, name, config[name])
        for name in LAYER_SWITCHES:
            if isinstance(config.get(name), bool):
                setattr(out, name, config[name])
        return out


@dataclass
class NanowellState:
    # ── user configuration (persisted) ──────────────────────────────────────
    view: str = "distribution"              # the page's default is the Distribution beeswarm (§3.10.3)
    metric: str = "n"
    baseline: str = "earliest"
    axis: str = "time"
    well: str = ""                          # the well the Array view shows
    timepoint: str = ""                     # the Array view's timepoint; "" = the earliest
    selected_sites: list = field(default_factory=list)  # [[well, q, r], ...] — not a plate selection
    control: str = ""                       # the group compared against on *Along x: Wells* ("" = none)
    cohort: bool = False                    # only nanowells with a value at every timepoint (tracker I8)
    layers: ArrayLayers = field(default_factory=ArrayLayers)   # what the Array view draws
    #: The user's "responding above" cut per metric column (``brightness_fc_GFP``, ``n``; tracker I9). A metric
    #: with none has no fraction responding: there is no default cut.
    thresholds: dict = field(default_factory=dict)

    # ── computed from the dataset (never persisted) ─────────────────────────
    data: object = None                     # collect.NanowellData, or None for a dataset with no nanowells
    metrics: object = None                  # series_compute.site_metrics(data, baseline)
    load_error: str = ""
    loaded_for: object = None               # (data_dir, baseline) the data and metrics were computed for
    #: The wells the data was read for (the page's wells when it asked, a well without a table included), and
    #: each well's parts as read (``collect.WellPart``) for the data dir in ``parts_for``, so a well selected again
    #: is not read again. Only the wells the page draws are read (``controller.ensure_loaded``).
    loaded_wells: frozenset = frozenset()
    well_parts: dict = field(default_factory=dict)
    parts_for: object = None
    computing: bool = False                 # a load or recompute is running on a worker
    request: int = 0                        # the latest load request; an older result is discarded
    hits: object = None                     # renderer.HitMap of the figure on screen
    model: object = None                    # distribution_compute.DistributionModel on screen
    pending_for: object = None              # the (data_dir, baseline) of the request running
    bridge: object = None                   # core.worker_bridge.WorkerBridge of the running request
    config_for: object = None               # the data dir whose saved choices are on the state
    crops: dict = field(default_factory=dict)            # (well, q, r) -> {timepoint: image_compute.Crop}
    montages: dict = field(default_factory=dict)         # (well, timepoint) -> (image_compute.Montage | None, why)
    fetching: set = field(default_factory=set)           # ("crops" | "montages", key) being read on a worker
    fetch_bridges: list = field(default_factory=list)    # each worker bridge, kept alive until it delivers
    #: The page's figure shows the first selected nanowell's crops at every timepoint, large, in place of the view
    #: (``renderer.render_crop_grid``); off again when the selection is cleared. Never persisted.
    crop_view: bool = False
    # The Array view's interaction, never persisted: where it is zoomed to (``(left, top, right, bottom)`` in the
    # frame it draws, for the ``(well, positions)`` in ``region_for``), the press a drag or click started with,
    # whether a click sets the anchor, and what the last draw put on screen (``renderer.ArrayDrawn``).
    region: object = None
    region_for: object = None
    press: object = None
    anchor_mode: bool = False
    #: The *Overlays* button: the coloured layers hidden for a look at the image beneath, without touching which
    #: layers are on. Not persisted — a page reopened with its overlays silently missing would look broken.
    overlays_hidden: bool = False
    drawn: object = None
    array_message: str = ""                 # what the last anchor action could not do, said on the figure
    # Full resolution: the raw tiles of one (well, timepoint), read on a worker as the view asks for them.
    tile_key: object = None                 # the (well, timepoint) the store holds
    tile_store: object = None               # full_montage_compute.TileStore, or None with ``tile_why`` saying why
    tile_why: str = ""
    tile_pending: set = field(default_factory=set)       # (tile, level) reads on their way
    tile_bridges: list = field(default_factory=list)

    def release_tiles(self) -> None:
        """Stop the reads of the tiles held so far and let their pixels go: another well, timepoint or dataset."""
        if self.tile_store is not None:
            self.tile_store.cancelled = True
        self.tile_key, self.tile_store, self.tile_why = None, None, ""
        self.tile_pending, self.tile_bridges = set(), []

    def reset_data(self) -> None:
        self.data, self.metrics, self.load_error, self.loaded_for = None, None, "", None
        self.loaded_wells, self.well_parts, self.parts_for = frozenset(), {}, None
        self.crops, self.montages, self.fetching, self.fetch_bridges = {}, {}, set(), []
        self.region = self.region_for = self.press = self.drawn = None
        self.anchor_mode, self.array_message = False, ""
        self.release_tiles()

    def to_config(self) -> dict:
        return {"view": self.view, "metric": self.metric, "baseline": self.baseline, "axis": self.axis,
                "well": self.well, "timepoint": self.timepoint, "control": self.control, "cohort": self.cohort,
                "layers": self.layers.to_config(),
                "selected_sites": [list(s) for s in self.selected_sites], "thresholds": dict(self.thresholds)}

    def apply_config(self, config: dict) -> None:
        """Take each recognised value from *config*; anything else keeps its default."""
        fresh = NanowellState()
        view = config.get("view")
        self.view = _RENAMED_VIEWS.get(view, view) if _RENAMED_VIEWS.get(view, view) in VIEWS else fresh.view
        self.baseline = config.get("baseline") if config.get("baseline") in BASELINES else fresh.baseline
        self.axis = config.get("axis") if config.get("axis") in AXES else fresh.axis
        self.metric = config.get("metric") if config.get("metric") in METRIC_KEYS else fresh.metric
        for name in ("well", "timepoint", "control"):
            value = config.get(name)
            setattr(self, name, value if isinstance(value, str) else getattr(fresh, name))
        self.cohort = config.get("cohort") is True
        self.layers = _layers_from(config)
        sites = []
        for item in config.get("selected_sites") or []:
            if (isinstance(item, (list, tuple)) and len(item) == 3 and isinstance(item[0], str)
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in item[1:])):
                sites.append([item[0], int(item[1]), int(item[2])])
        self.selected_sites = sites
        thresholds = config.get("thresholds")
        self.thresholds = {k: float(v) for k, v in (thresholds.items() if isinstance(thresholds, dict) else ())
                           if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v)}


def _layers_from(config: dict) -> ArrayLayers:
    """The Array view's layers from a saved block — or, from a block saved on Array map or Array QC before there
    was one view, the layers that view drew, with Array QC's *Show: Montage / Overlay* boxes carried over."""
    if isinstance(config.get("layers"), dict):
        return ArrayLayers.from_config(config["layers"])
    if config.get("view") == "array_map":
        return ArrayLayers.preset("map")
    if config.get("view") == "array_qc":
        layers = ArrayLayers.preset("qc")
        if config.get("qc_montage") is False:
            layers.image = "off"
        if config.get("qc_overlay") is False:
            layers.tiles = layers.nanowells = False
        return layers
    return ArrayLayers()
