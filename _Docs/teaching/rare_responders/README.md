# Rare responders: teaching material and how its numbers are made

This folder holds a slide deck and a chapter that teach the exceedance test for rare responders in nanowell
assays, together with everything needed to reproduce every number they quote. Nothing in it is part of the
application; the scripts import the application's fitting code, and the application never imports them.

The published copies are claude.ai artifacts ("Nanowell DC–T cell assay (condensed, restructured)" and
"Detecting Rare Responders"). The files here are their source.

## Layout

| Path | What it is |
|---|---|
| `sims/` | One script per computation. Each states its design and settings at the top, uses a fixed seed, and writes its output to `results/`. |
| `results/` | The outputs: JSON (with the settings, seed and Monte Carlo standard errors inside) and one `.npy` array. |
| `deck/` | The deck. `build.py` regenerates slides 10 to 40 from `results/` with `gen_stats2.py`; slides 1 to 9 are drawn by hand in `project/slides/`. `project/` is the deck as published (`deck.json` gives the slide order). |
| `chapter/` | The chapter, `detecting-rare-responders.html`, as published. It is edited by hand, so `check.py` confirms the values it quotes. |
| `check.py` | Confirms that the material still agrees with the code and the results. |

## The example plate

Four control wells and four wells of each molecule, 1,000 nanowells per well. Every well is counted against one
cutoff: the 99th percentile of the fold change of all 4,000 control nanowells pooled, so exactly 40 of them lie above
it. The counts are what that rule gives on the simulated nanowells:

| Wells | Counts past the cutoff |
|---|---|
| control | 7, 8, 14, 11 |
| molecule A | 21, 24, 28, 23 |
| molecule B | 9, 10, 64, 13 |
| molecule C | 13, 15, 15, 14 |

## Which script makes which number

| Script | Results | Used by |
|---|---|---|
| `sims/example_plate.py` | `example_plate.json`: both fits for A, B and C, D, z, p, BH q, odds ratios and profile intervals, the naive tests, the fit with a separate r per group, the spread of the wells | deck slides 12 to 24, 30, 33, 36, 38, 40; chapter section 3.4 and 4.1 |
| `sims/null_gain.py` | `null_gain.npy`, `null_gain.json`: 1,500 no-effect plates drawn from A's fit without a gap | deck slide 18; chapter Figure 15 |
| `sims/loo_variance.py` | `loo_variance.json`: control-well variance with a pooled, a leave-one-out and a known cutoff | deck slide 37 |
| `sims/cutoff_tradeoff.py` | `cutoff_tradeoff.json`: false positives and detection at the 95th, 99th and 99.9th percentile, 4,000 plates per row | deck slide 27 |
| `sims/end_to_end_null.py` | `end_to_end_null.json`: 40,000 no-effect plates simulated from the nanowells up, through the pooled cutoff | deck slide 31, footnote and notes |
| `sims/calibration.py` | `calibration.json`: false-positive rate by design, counts drawn from A's fit without a gap, 4,000 plates per design | deck slide 31, bars |
| `sims/power.py` | `power.json`: detection rate by design, r and odds ratio, 1,000 plates per point | deck slide 35 |

`calibration.py` and `power.py` read `results/example_plate.json`, so run `example_plate.py` first.
`plate_model.py` (nanowell-level plates) and `count_model.py` (count-level plates and a multi-start fit) are shared by
the scripts.

## Running

Use the repository's test environment (Python with numpy, scipy; Node with Playwright for the deck's layout check):

```bash
cd _Docs/teaching/rare_responders/sims
python example_plate.py            # seconds
python null_gain.py                # about a minute
python loo_variance.py             # seconds
python cutoff_tradeoff.py 4000 4   # plates per row, processes; about 5 minutes
python end_to_end_null.py 40000 4  # about 4 minutes
python calibration.py 4000 4       # about 5 minutes
python power.py 1000 4             # about 10 minutes
cd ../deck && python build.py      # regenerate slides 10 to 40
cd .. && python check.py           # about a minute; add --sims to rerun every simulation too
```

## Keeping it accurate

- **After changing the application's exceedance fit** (`review/features/nanowell/rare_exceedance_compute.py`), run
  `python check.py --sims`. Every number here goes through that code; a change that moves a fit shows up as a
  result that no longer reproduces.
- **After changing a number in the deck**, change the script or result it comes from and rebuild; do not edit a
  generated slide by hand. `check.py` fails if a stored slide differs from what `build.py` produces.
- **After changing the chapter**, run `check.py`; its `claims()` list names the values the chapter must quote.
  Add to it when the chapter quotes a new computed value.
- **Simulated figures carry their uncertainty.** Each results file stores the number of plates and the standard
  error; quote a simulated rate with no more precision than that supports.
- A fresh seed should agree with the stored results within about three standard errors. Agreement at the same seed
  shows the code is unchanged; agreement at a new seed shows the result does not depend on the seed.
