"""Cellpose 4's masks for a batch of nanowell crops, built together instead of one crop at a time.

Cellpose 4 runs its network over a batch of images in shared passes, then builds each image's masks on its own
(``CellposeModel._compute_masks`` loops over the images). Building masks is two loops of small GPU steps per image —
following the flows (about 200 steps) and, for the flow-error check, diffusing each mask's own flows back out (twice
the largest mask's extent) — and on a nanowell crop each step touches a few thousand pixels, so the GPU waits on the
loop rather than computing. Reported on an A10G: 0.05 s a nanowell for the network and 0.27 s for its masks.

This module runs those two loops once for the whole batch and keeps everything else per crop, so each crop's masks
are the ones Cellpose would build for it alone:

* **Following the flows** (Cellpose's ``dynamics.steps_interp``) is the same arithmetic on a batch dimension. Every
  crop of a batch has the same shape (``pipeline.cellpose._segment_cellpose_many`` centres them in one), so the
  normalisation by the image's size is the same for all, and ``grid_sample`` reads each point from its own image.
* **The flow-error diffusion** (``dynamics.masks_to_flows_gpu`` → ``_extend_centers_gpu``) runs on every crop's
  mask pixels at once. Each crop stops at its own number of steps — Cellpose runs ``2 × ext.max()`` for the image it
  was given, and a crop's largest mask is not the batch's — so a crop's pixels are left alone once it has had its
  steps. A pixel's neighbours are always in its own crop, so no crop sees another's values.
* **Seeds, the area cap, the flow-error comparison and hole filling** are Cellpose's own functions, called per crop.
  Their cost is per seed and per mask, not per step, so batching them would buy little and cost the exactness.

It reaches into Cellpose's internals (``dynamics.get_masks_torch``, ``dynamics.get_centers``,
``utils.fill_holes_and_remove_small_masks``) and copies two of its loops, so it is used only on the Cellpose it was
proven against (``SUPPORTED_VERSION``, the pin in ``requirements-remote-cellpose.txt``); on any other the caller lets
Cellpose build the masks. ``tests/test_cellpose_batched_masks.py`` compares the two on Cellpose itself.

Torch and Cellpose are imported inside the functions, as in ``pipeline.cellpose``, so importing this module loads
neither.
"""

from __future__ import annotations

import numpy as np

#: The Cellpose release whose mask code this module reproduces. Its loops were copied from 4.1.1's ``dynamics.py``;
#: a later release may change them, and then a crop's masks here would quietly differ from Cellpose's own.
SUPPORTED_VERSION = "4.1.1"


def supported(version: str | None) -> bool:
    """Whether masks for a Cellpose of *version* may be built here. ``None`` (not installed) is not."""
    return version is not None and str(version).split("+")[0] == SUPPORTED_VERSION


def installed_version() -> str | None:
    """The installed Cellpose's version from its package metadata (imports neither Cellpose nor Torch), or ``None``."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cellpose")
    except PackageNotFoundError:
        return None


def default_niter(diameter: float | None) -> int:
    """The flow-following steps Cellpose 4's ``eval`` runs when given no ``niter``: 200 at its own scale, fewer for
    an image it enlarges (``image_scaling`` is 30 over the diameter)."""
    scaling = 30.0 / diameter if diameter is not None and diameter > 0 else 1.0
    return int(200 / scaling)


def batch_masks(dP: np.ndarray, cellprob: np.ndarray, *, niter: int, cellprob_threshold: float,
                flow_threshold: float, min_size: int, max_size_fraction: float, device) -> list[np.ndarray]:
    """One label image per crop from the network's flows ``dP`` ``(2, N, H, W)`` and cell probability ``cellprob``
    ``(N, H, W)``: what ``dynamics.resize_and_compute_masks`` returns for each crop alone, up to label numbering."""
    from cellpose import dynamics, utils

    n = cellprob.shape[0]
    shape = cellprob.shape[1:]
    selected = [np.nonzero(cellprob[i] > cellprob_threshold) for i in range(n)]
    with_cells = [i for i in range(n) if len(selected[i][0])]
    masks = [np.zeros(shape, "uint16") for _ in range(n)]
    if not with_cells:
        return masks
    flows = np.stack([dP[:, i] * (cellprob[i] > cellprob_threshold) / 5.0 for i in with_cells])
    finals = _follow_flows_batched(flows, [selected[i] for i in with_cells], niter, device)
    for i, final in zip(with_cells, finals):
        masks[i] = dynamics.get_masks_torch(final.int(), selected[i], shape, max_size_fraction=max_size_fraction)
    if flow_threshold is not None and flow_threshold > 0:
        # Cell pixels need not converge into a seed (Cellpose's "no seeds found"), so a batch can have none to check.
        checked = [i for i in with_cells if masks[i].max() > 0]
        for i, mask_flows in zip(checked, _masks_to_flows_batched([masks[i] for i in checked], device)):
            masks[i] = _remove_bad_flow_masks(masks[i], mask_flows, dP[:, i], flow_threshold)
    out = []
    for mask in masks:
        if mask.max() < 2 ** 16 and mask.dtype != "uint16":
            mask = mask.astype("uint16")
        out.append(utils.fill_holes_and_remove_small_masks(mask, min_size=min_size))
    return out


def _follow_flows_batched(flows: np.ndarray, inds: list, niter: int, device) -> list:
    """Each image's final pixel positions, ``(2, P)``: ``dynamics.steps_interp`` run on all of *flows*
    ``(N, 2, H, W)`` at once. The arithmetic is Cellpose's, step for step; only the batch dimension is added, and
    images with fewer cell pixels than the most are padded with points that are discarded."""
    import torch

    n, _two, ly, lx = flows.shape
    most = max(len(ix[0]) for ix in inds)
    pt = torch.zeros((n, 1, most, 2), dtype=torch.float32, device=device)
    im = torch.zeros((n, 2, ly, lx), dtype=torch.float32, device=device)
    for b, ix in enumerate(inds):
        for d in range(2):
            pt[b, 0, :len(ix[d]), 2 - d - 1] = torch.from_numpy(ix[d]).to(device, dtype=torch.float32)
            im[b, 2 - d - 1] = torch.from_numpy(flows[b, d]).to(device, dtype=torch.float32)
    shape = np.array((ly, lx))[::-1].astype("float") - 1
    for k in range(2):
        im[:, k] *= 2. / shape[k]
        pt[..., k] /= shape[k]
    pt *= 2
    pt -= 1
    for _t in range(niter):
        step = torch.nn.functional.grid_sample(im, pt, align_corners=False)
        for k in range(2):
            pt[..., k] = torch.clamp(pt[..., k] + step[:, k], -1., 1.)
    pt += 1
    pt *= 0.5
    for k in range(2):
        pt[..., k] *= shape[k]
    return [pt[b, 0, :len(ix[0])][:, [1, 0]].T for b, ix in enumerate(inds)]


def _masks_to_flows_batched(masks: list, device) -> list[np.ndarray]:
    """Each mask image's flows, ``(2, H, W)`` float64: ``dynamics.masks_to_flows_gpu`` for every image at once.

    The images are laid side by side, one pixel of padding round each (the padding Cellpose adds), so a pixel's
    neighbours are its own image's. Each image diffuses for its own ``2 × ext.max()`` steps and then stops."""
    import torch
    from cellpose import dynamics
    from scipy.ndimage import find_objects

    if not masks:
        return []
    ly0, lx0 = masks[0].shape
    ly, lx = ly0 + 2, lx0 + 2
    canvas = np.zeros((ly, lx * len(masks)), np.int64)
    centres, centre_steps, image_steps, offset = [], [], [], 0
    for b, mask in enumerate(masks):
        canvas[1:ly0 + 1, b * lx + 1:b * lx + 1 + lx0] = np.where(mask > 0, mask.astype("int64") + offset, 0)
        offset += int(mask.max())                                # labels unique across images
        c, ext = dynamics.get_centers(mask, find_objects(mask))
        centres.append(c + np.array([1, b * lx + 1]))           # the padding, then the image's place on the canvas
        image_steps.append(2 * int(ext.max()))
        centre_steps.append(np.full(len(c), image_steps[-1]))
    meds = torch.from_numpy(np.concatenate(centres)).to(device).long()
    med_steps = torch.from_numpy(np.concatenate(centre_steps)).to(device)
    padded = torch.from_numpy(canvas).to(device)
    y, x = torch.nonzero(padded, as_tuple=True)
    y, x = y.int(), x.int()
    neighbors = torch.zeros((2, 9, y.shape[0]), dtype=torch.int, device=device)
    yxi = [[0, -1, 1, 0, 0, -1, -1, 1, 1], [0, 0, 0, -1, 1, -1, 1, -1, 1]]
    for i in range(9):
        neighbors[0, i] = y + yxi[0][i]
        neighbors[1, i] = x + yxi[1][i]
    isneighbor = torch.ones((9, y.shape[0]), dtype=torch.bool, device=device)
    m0 = padded[neighbors[0, 0], neighbors[1, 0]]
    for i in range(1, 9):
        isneighbor[i] = padded[neighbors[0, i], neighbors[1, i]] == m0
    del m0, padded
    ys, xs = y.cpu().numpy(), x.cpu().numpy()
    image = xs // lx                     # nonzero walks the canvas row by row, so pixels are not grouped by image
    pixel_steps = torch.from_numpy(np.asarray(image_steps)[image]).to(device)
    # Cellpose diffuses in float64 unless the (padded) image is over 4e7 pixels, or on MPS; a crop is far under.
    dtype = torch.float if ly * lx > 4e7 or getattr(device, "type", None) == "mps" else torch.double
    T = torch.zeros(canvas.shape, dtype=dtype, device=device)
    live_n, live_iso = neighbors, isneighbor
    for it in range(int(med_steps.max())):
        if not bool((med_steps > it).all()):           # an image has had its steps: leave its pixels as they are
            keep, active = med_steps > it, pixel_steps > it
            meds, med_steps = meds[keep], med_steps[keep]
            live_n, live_iso, pixel_steps = live_n[:, :, active], live_iso[:, active], pixel_steps[active]
        T[tuple(meds.T)] += 1
        near = T[tuple(live_n)]
        near *= live_iso
        T[tuple(live_n[:, 0])] = near.mean(axis=0)
    # As Cellpose: the gradient at every mask pixel, from its four direct neighbours (+y, -y, +x, -x).
    grads = T[neighbors[0, [2, 1, 4, 3]], neighbors[1, [2, 1, 4, 3]]]
    mu = np.stack(((grads[0] - grads[1]).cpu(), (grads[2] - grads[3]).cpu()), axis=-2).astype("float64")
    mu /= (1e-60 + (mu ** 2).sum(axis=0) ** 0.5)
    out = []
    for b in range(len(masks)):
        mine = image == b
        flows = np.zeros((2, ly0, lx0))
        flows[:, ys[mine] - 1, xs[mine] - b * lx - 1] = mu[:, mine]
        out.append(flows)
    return out


def _remove_bad_flow_masks(mask: np.ndarray, mask_flows: np.ndarray, net_flows: np.ndarray, threshold: float):
    """``dynamics.remove_bad_flow_masks`` given the mask's flows already computed: the masks whose flows depart
    from the network's by more than *threshold* are cleared."""
    from scipy.ndimage import mean

    errors = np.zeros(mask.max())
    for i in range(mask_flows.shape[0]):
        errors += mean((mask_flows[i] - net_flows[i] / 5.) ** 2, mask, index=np.arange(1, mask.max() + 1))
    bad = 1 + (errors > threshold).nonzero()[0]
    mask[np.isin(mask, bad)] = 0
    return mask
