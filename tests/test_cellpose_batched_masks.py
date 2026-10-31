"""A batch of nanowell crops gets the masks Cellpose would build for each crop alone, from one batched build.

Reported on an A10G: Cellpose-SAM's network took 0.05 s a nanowell and building its masks 0.27 s, because Cellpose 4
builds each image's masks on its own — two loops of small GPU steps per crop. ``pipeline.cellpose_masks`` runs those
loops once for the whole batch. The point of the module is that nothing about a crop's masks changes, so the first
half of this file holds it to that against Cellpose itself (skipped where Cellpose and Torch are not installed, as in
CI); the second half holds the wiring in ``pipeline.cellpose._segment_cellpose_many`` with a fake model.

Why the first half does not run in CI (review tracker L11, declined): ``cellpose_masks`` is Torch code throughout —
the batched flow following is ``torch.nn.functional.grid_sample`` over tensors — so a frozen fixture of Cellpose's
own masks would still need Torch to compute the side under test, and the test stack leaves Torch out on purpose
(``tests/requirements-dev.txt``, CLAUDE.md §1). What CI does hold is the guard that keeps the batched build to the
one Cellpose it was proven against (``test_only_the_proven_cellpose_is_batched``, ``SUPPORTED_VERSION``) and the
wiring below. Run this file where Cellpose and Torch are installed when ``cellpose_masks`` changes.
"""

from __future__ import annotations

import inspect
import types

import numpy as np
import pytest
from scipy.ndimage import find_objects

from pipeline import cellpose as cp
from pipeline import cellpose_masks as cm

SHAPE = (96, 96)


def _cells(rng, n_cells: int, radius: tuple[int, int]) -> np.ndarray:
    """A label image of *n_cells* discs, drawn in turn so later ones overlap earlier ones (touching cells)."""
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    labels = np.zeros(SHAPE, np.int32)
    for k in range(n_cells):
        r = rng.integers(*radius)
        cy, cx = rng.integers(r, SHAPE[0] - r), rng.integers(r, SHAPE[1] - r)
        labels[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = k + 1
    # A disc drawn over completely leaves a gap in the numbering, which Cellpose's own labels never have.
    present = np.unique(labels)
    return np.searchsorted(present, labels).astype(np.int32)


def _network_like(labels: np.ndarray, rng, noise: float):
    """What a network would return for *labels*: flows (5 x the masks' own, Cellpose's scale) and a cell
    probability, both noisy, so some masks fail the flow-error check and the check is exercised."""
    from cellpose import dynamics

    flows = dynamics.masks_to_flows_gpu(labels, device=_cpu())
    dP = 5.0 * np.asarray(flows, np.float32) + rng.normal(0, noise, (2, *SHAPE)).astype(np.float32)
    cellprob = np.where(labels > 0, 3.0, -3.0).astype(np.float32) + rng.normal(0, 1.0, SHAPE).astype(np.float32)
    return dP, cellprob


def _cpu():
    import torch

    return torch.device("cpu")


def _same_partition(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two label images are the same masks up to their numbering."""
    if not np.array_equal(a > 0, b > 0):
        return False
    pairs = set(zip(a[a > 0].tolist(), b[b > 0].tolist()))
    return len(pairs) == len({p[0] for p in pairs}) == len({p[1] for p in pairs})


def _batch(rng):
    """Crops of different loading, so their largest masks differ and each diffuses for a different number of steps:
    an empty one, one cell, a sparse one, a crowded one, a noisy crowded one and one that fills the crop."""
    crops = [np.zeros(SHAPE, np.int32), _cells(rng, 1, (8, 9)), _cells(rng, 4, (5, 9)), _cells(rng, 20, (4, 10)),
             _cells(rng, 25, (3, 12)), _cells(rng, 1, (40, 41))]
    noise = [0.5, 0.5, 0.5, 0.5, 3.0, 0.5]
    made = [_network_like(lab, rng, s) for lab, s in zip(crops, noise)]
    dP = np.stack([m[0] for m in made], axis=1)                      # (2, N, H, W), as eval returns it
    cellprob = np.stack([m[1] for m in made])
    return dP, cellprob


@pytest.mark.parametrize("max_size_fraction", [1.0, 0.4])
def test_a_batch_s_masks_are_each_crop_s_own(max_size_fraction):
    pytest.importorskip("torch")
    pytest.importorskip("cellpose")
    from cellpose import dynamics

    rng = np.random.default_rng(7)
    dP, cellprob = _batch(rng)
    kw = dict(niter=cm.default_niter(23.0), cellprob_threshold=0.0, flow_threshold=0.4, min_size=15,
              max_size_fraction=max_size_fraction)
    got = cm.batch_masks(dP, cellprob, device=_cpu(), **kw)
    removed_by_flow_check = 0
    for i in range(cellprob.shape[0]):
        alone = dynamics.resize_and_compute_masks(dP[:, i], cellprob[i], device=_cpu(), **kw)
        unchecked = dynamics.resize_and_compute_masks(dP[:, i], cellprob[i], device=_cpu(),
                                                      **{**kw, "flow_threshold": 0.0})
        removed_by_flow_check += len(np.unique(unchecked)) - len(np.unique(alone))
        assert _same_partition(got[i], alone), f"crop {i}: {got[i].max()} masks against Cellpose's {alone.max()}"
    # The fixture exercises the flow-error check: without it, a pass here would say nothing about that half.
    assert removed_by_flow_check > 0


def test_a_batch_whose_cell_pixels_form_no_mask_comes_back_empty():
    """Found running the real eval: cell pixels that converge into no seed leave no mask to check, and the batched
    flow check was handed nothing and raised, which would have failed the timepoint."""
    pytest.importorskip("torch")
    pytest.importorskip("cellpose")
    cellprob = np.full((2, *SHAPE), -3.0, np.float32)
    cellprob[:, ::20, ::20] = 3.0                       # isolated pixels, no flow: none gathers more than 10
    got = cm.batch_masks(np.zeros((2, 2, *SHAPE), np.float32), cellprob, device=_cpu(), niter=200,
                         cellprob_threshold=0.0, flow_threshold=0.4, min_size=15, max_size_fraction=1.0)
    assert [g.max() for g in got] == [0, 0] and [g.shape for g in got] == [SHAPE, SHAPE]


def test_each_crop_diffuses_for_its_own_steps_not_the_batch_s():
    """Cellpose runs the flow-error diffusion for twice the largest mask's extent in the image it is given. A small
    cell's flows after its crop's steps differ from its flows after a crowded neighbour's, so a batch that ran every
    crop for the most steps would change which masks pass."""
    pytest.importorskip("torch")
    pytest.importorskip("cellpose")
    from cellpose import dynamics

    rng = np.random.default_rng(3)
    small, large = _cells(rng, 3, (4, 6)), _cells(rng, 1, (40, 41))
    got = cm._masks_to_flows_batched([small, large], _cpu())
    for mask, flows in zip((small, large), got):
        own = dynamics.masks_to_flows_gpu(mask, device=_cpu())
        # Equal to rounding: a mean over a tensor of another length may round its last bit differently (measured:
        # 1e-16 at 2 of 211 pixels; alone in a batch of one, bit for bit).
        assert np.allclose(flows, own, rtol=0, atol=1e-12)
    # What the tolerance would let through if every crop ran for the batch's steps: far outside it.
    steps = 2 * int(dynamics.get_centers(large, find_objects(large))[1].max())
    longer = dynamics.masks_to_flows_gpu(small, device=_cpu(), niter=steps)
    assert not np.allclose(got[0], longer, rtol=0, atol=1e-6)


def test_only_the_proven_cellpose_is_batched():
    assert cm.supported("4.1.1") and cm.supported("4.1.1+local")
    assert not cm.supported("4.1.2") and not cm.supported("3.1.1") and not cm.supported(None)
    assert cm.default_niter(None) == 200 and cm.default_niter(15.0) == 100 and cm.default_niter(45.0) == 300


class _FlowModel:
    """A Cellpose 4 model that returns flows, as ``eval(compute_masks=False)`` does, and records its arguments."""

    device = "cpu"

    def __init__(self):
        self.calls = []

    def eval(self, x, batch_size=8, flow_threshold=0.4, cellprob_threshold=0.0, min_size=15, max_size_fraction=0.4,
             compute_masks=True, **kw):
        x = np.asarray(x)
        self.calls.append({"shape": x.shape, "batch_size": batch_size, "compute_masks": compute_masks, **kw})
        n, h, w = x.shape[:3]
        cellprob = np.where(x[..., 0] > 0.5, 1.0, -1.0).astype(np.float32)
        dP = np.zeros((2, n, h, w), np.float32)
        masks = (x[..., 0] > 0.5).astype(int) * 9 if compute_masks else np.zeros(0)
        return masks, [None, dP, cellprob], None


def _wire(monkeypatch, version):
    model = _FlowModel()
    monkeypatch.setattr(cp, "_cellpose_model", lambda model_type=None: model)
    monkeypatch.setattr(cp, "cellpose_major", lambda version=None: 4)
    monkeypatch.setattr(cm, "installed_version", lambda: version)
    monkeypatch.setattr(cp, "CELLPOSE_SECONDS", {"network": 0.0, "masks": 0.0, "timed": 0.0})
    monkeypatch.setattr(cp, "_NETWORK_BATCHES", {})
    built = []

    def batch_masks(dP, cellprob, **kw):
        built.append((dP.shape, cellprob.shape, kw))
        return [np.where(c > 0, i + 1, 0).astype("uint16") for i, c in enumerate(cellprob)]

    monkeypatch.setattr(cm, "batch_masks", batch_masks)
    return model, built


def test_the_proven_cellpose_is_asked_for_flows_and_the_batch_s_masks_are_built_together(monkeypatch):
    model, built = _wire(monkeypatch, "4.1.1")
    crops = [np.full((3, 4), 250.0), np.full((2, 5), 10.0), np.array([[250.0, 10.0]])]
    got = cp._segment_cellpose_many(crops, lowhigh=(10.0, 250.0), model_type="cpsam", diameter=23.0,
                                    max_size_fraction=1.0)
    (call,) = model.calls
    assert call["compute_masks"] is False and call["batch_size"] == cp.NETWORK_BATCH == 32
    ((dshape, pshape, kw),) = built
    assert dshape == (2, 3, 3, 5) and pshape == (3, 3, 5)
    defaults = inspect.signature(_FlowModel.eval).parameters
    assert kw["niter"] == 153 and kw["min_size"] == defaults["min_size"].default
    assert kw["max_size_fraction"] == 1.0 and kw["flow_threshold"] == 0.4 and kw["device"] == "cpu"
    # Each crop's labels come back cut to its own shape, from its own plane of the batch.
    assert [g.shape for g in got] == [(3, 4), (2, 5), (1, 2)]
    assert np.all(got[0] == 1) and not got[1].any() and got[2].tolist() == [[3, 0]]
    assert cp.CELLPOSE_SECONDS["masks"] > 0 and cp.CELLPOSE_SECONDS["timed"] == 3


def test_a_crop_with_no_size_cap_gets_cellpose_s_own(monkeypatch):
    _model, built = _wire(monkeypatch, "4.1.1")
    cp._segment_cellpose_many([np.zeros((3, 4)), np.zeros((3, 4))], lowhigh=(0.0, 1.0), model_type="cpsam")
    assert built[0][2]["max_size_fraction"] == 0.4 and built[0][2]["niter"] == 200


def test_another_cellpose_builds_its_own_masks_with_the_larger_network_batch(monkeypatch):
    model, built = _wire(monkeypatch, "4.2.0")
    got = cp._segment_cellpose_many([np.full((3, 4), 250.0), np.full((3, 4), 10.0)], lowhigh=(10.0, 250.0),
                                    model_type="cpsam")
    (call,) = model.calls
    assert call["compute_masks"] is True and call["batch_size"] == 32 and not built
    assert np.all(got[0] == 9) and not got[1].any()


def test_importing_the_module_loads_neither_torch_nor_cellpose():
    source = inspect.getsource(cm)
    head = source.split("def ", 1)[0]
    assert "import torch" not in head and "from cellpose" not in head
    assert isinstance(cm, types.ModuleType)
