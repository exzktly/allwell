"""Design 96 well colours: the eight the user approved, plus 88 chosen and ordered by measurement.

Run from anywhere: ``python _Docs/design/well_palette_build.py``. It prints the comparison with the current
palette and the 96 values, and writes ``palette.json`` in the working directory. The run is deterministic
(the swap search is seeded). ``DESIGN_LANGUAGE_TRACKER.md`` row D30 records its output.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from domain.well_palette import WELL_COLORS_TUPLE as OLD

FIXED = ["#338CED", "#E76624", "#00A888", "#D28E05", "#E24D8B", "#0AAC23", "#9887EF", "#EB6263"]
SURF = {"grey plot": "#0A0C10", "blue plot": "#131A24", "white": "#FFFFFF"}

def hex2rgb(h): h = h.lstrip("#"); return np.array([int(h[i:i+2], 16) / 255 for i in (0, 2, 4)])
def lin(c): return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
def unlin(c): return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)
def lum(l): return l @ np.array([0.2126, 0.7152, 0.0722])
M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566], [0.0883024619, 0.2817188376, 0.6299787005]])
M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099], [0.0259040371, 0.7827717662, -0.8086757660]])
def oklab(l): return np.cbrt(l @ M1.T) @ M2.T
def oklch2lin(L, C, h):
    a, b = C * np.cos(h), C * np.sin(h)
    lab = np.stack([L, a, b], -1)
    lms = (lab @ np.linalg.inv(M2).T) ** 3
    return lms @ np.linalg.inv(M1).T
# Machado, Oliveira & Fernandes 2009, severity 1.0, applied to linear RGB
CVD = {"deutan": np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]),
       "protan": np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]])}
def views(l):  # normal, deutan, protan in OKLab x100
    return [oklab(l) * 100] + [oklab(np.clip(l @ m.T, 0, 1)) * 100 for m in CVD.values()]
def contrast(Y, bg): yb = lum(lin(hex2rgb(bg))); hi, lo = np.maximum(Y, yb), np.minimum(Y, yb); return (hi + 0.05) / (lo + 0.05)

# candidates: every in-gamut OKLCH colour whose luminance is 3:1 on the darkest-needed and on white
Ls, Cs, Hs = np.meshgrid(np.arange(0.50, 0.80, 0.01), np.arange(0.07, 0.33, 0.01), np.deg2rad(np.arange(0, 360, 2)), indexing="ij")
l = oklch2lin(Ls.ravel(), Cs.ravel(), Hs.ravel())
ok = (l.min(1) >= 0) & (l.max(1) <= 1)
l = l[ok]; Y = lum(l)
WHITE_FLOOR = 2.75   # the approved yellow, #D28E05, sets this: it is 2.76 on white
band = (contrast(Y, SURF["blue plot"]) >= 3.0) & (contrast(Y, SURF["white"]) >= WHITE_FLOOR)
cand = l[band]
print("candidates", len(cand))
fx = lin(np.array([hex2rgb(h) for h in FIXED]))
V = views(np.vstack([fx, cand]))
def dist(i, js):  # normal-vision distance, capped by twice the worse colour-blind distance
    dn = np.linalg.norm(V[0][js] - V[0][i], axis=1)
    dc = np.minimum(np.linalg.norm(V[1][js] - V[1][i], axis=1), np.linalg.norm(V[2][js] - V[2][i], axis=1))
    return np.minimum(dn, 2.0 * dc)
chosen = list(range(8)); n = len(V[0])
mind = np.min([dist(i, np.arange(n)) for i in chosen], axis=0)
while len(chosen) < 96:
    k = int(np.argmax(mind)); chosen.append(k); mind = np.minimum(mind, dist(k, np.arange(n)))
allv = [v[chosen] for v in V]; alll = np.vstack([fx, cand])[chosen]

# order: ranks 0-7 fixed; each later rank takes the colour farthest from its plate neighbours
def pair(a, b): return min(np.linalg.norm(allv[0][a] - allv[0][b]), 2 * min(np.linalg.norm(allv[1][a] - allv[1][b]), np.linalg.norm(allv[2][a] - allv[2][b])))
order = list(range(8)); left = set(range(8, 96))
for r in range(8, 96):
    nb = [order[r - d] for d in (1, 2, 12, 11, 13) if r - d >= 0]
    best = max(left, key=lambda c: min(pair(c, x) for x in nb)); order.append(best); left.remove(best)
# improve the order by swapping ranks 8-95 while it lowers the penalty on plate neighbours
m = 96; NBR = [(i, j) for i in range(m) for j in (i + 1, i + 12) if j < m and not (j == i + 1 and j % 12 == 0)]
Dn = np.linalg.norm(allv[0][:, None] - allv[0][None], axis=2)
Dc = np.minimum(np.linalg.norm(allv[1][:, None] - allv[1][None], axis=2), np.linalg.norm(allv[2][:, None] - allv[2][None], axis=2))
def penalty(o):
    a = np.array([o[i] for i, _ in NBR]); b = np.array([o[j] for _, j in NBR])
    return float((np.clip(10 - Dc[a, b], 0, None) ** 2).sum() + 0.5 * (np.clip(14 - Dn[a, b], 0, None) ** 2).sum())
rng = np.random.default_rng(7); cur = penalty(order)
for _ in range(60000):
    i, j = rng.integers(8, 96, 2)
    if i == j: continue
    order[i], order[j] = order[j], order[i]; p = penalty(order)
    if p <= cur: cur = p
    else: order[i], order[j] = order[j], order[i]
final_l = alll[order]
hexes = ["#%02X%02X%02X" % tuple(np.clip(np.round(unlin(c) * 255), 0, 255).astype(int)) for c in final_l]
hexes[:8] = FIXED

def report(name, hx):
    L = lin(np.array([hex2rgb(h) for h in hx])); vs = views(L); Yh = lum(L); m = len(hx)
    D = [np.linalg.norm(v[:, None] - v[None], axis=2) + np.eye(m) * 1e9 for v in vs]
    nbr = [(i, j) for i in range(m) for j in (i + 1, i + 12) if j < m and not (j == i + 1 and j % 12 == 0)]
    out = {"palette": name, "worst pair, normal vision": round(float(D[0].min()), 1),
           "worst plate neighbour, normal": round(float(min(D[0][i, j] for i, j in nbr)), 1),
           "worst plate neighbour, colour-blind": round(float(min(min(D[1][i, j], D[2][i, j]) for i, j in nbr)), 1),
           "plate neighbours under 8 colour-blind": int(sum(min(D[1][i, j], D[2][i, j]) < 8 for i, j in nbr)), "neighbour pairs": len(nbr)}
    for s, bg in SURF.items():
        c = contrast(Yh, bg); out[f"under 3:1 on {s}"] = int((c < 3).sum()); out[f"lowest on {s}"] = round(float(c.min()), 2)
    return out
res = [report("current", list(OLD)), report("new", hexes)]
for r in res: print(json.dumps(r))
print("first eight on white:", [round(float(contrast(lum(lin(hex2rgb(h))), "#FFFFFF")), 2) for h in FIXED])
Path("palette.json").write_text(json.dumps({"colours": hexes, "report": res}, indent=1), encoding="utf-8")
for i in range(0, 96, 12): print(" ".join(hexes[i:i + 12]))
