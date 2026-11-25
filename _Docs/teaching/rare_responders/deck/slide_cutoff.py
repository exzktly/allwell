"""Slide 27 ('cutoff'): fill the table, its note and the speaker notes from ../results/cutoff_tradeoff.json.

Usage: python slide_cutoff.py <path to project/slides/cutoff.html>. Rewrites the file in place.
"""
import json
import re
import sys
from pathlib import Path

R = json.loads((Path(__file__).resolve().parents[1] / 'results' / 'cutoff_tradeoff.json').read_text())
PCTS = ('95', '99', '99.9')
LABELS = ['No effect: false positives (should be 5&#160;%)', '1&#160;% responders, far out (2–4× the 99th)',
          '1&#160;% responders, just past (1–1.5× the 99th)', '3&#160;% responders, modest (0.8–1.2× the 99th)']
pc = lambda f: f'{f * 100:.0f}&#160;%' if f >= 0.095 or f == 0 else f'{f * 100:.1f}&#160;%'


def rows():
    out = []
    for i, r in enumerate(R['rows']):
        v = [r['cells'][p]['share'] for p in PCTS]
        best = None if i == 0 else max(range(3), key=lambda j: v[j])
        cells = ''.join(f'<td style="text-align:right">{pc(x)}{" best" if j == best else ""}</td>' for j, x in enumerate(v))
        out.append(f'<tr><td>{LABELS[i]}</td>{cells}</tr>')
    return '\n'.join(out)


def main(path):
    t = Path(path).read_text()
    head = '<tr><th style="width:46%">Simulated plate</th>'
    i = t.index(head); j = t.index('</table>', i); k = t.index('</tr>', i) + 5
    t = t[:k] + '\n' + rows() + '\n' + t[j:]
    plates = R['rows'][0]['plates']
    se = max(c['se'] for r in R['rows'] for c in r['cells'].values())
    t = re.sub(r'<p class="tnote">.*?</p>', '<p class="tnote">Share of plates called significant by the exceedance test, one-sided p &lt; 0.05<br>'
               f'4 control and 4 molecule wells, 1,000 nanowells each, {plates:,} simulated plates per row<br>'
               f'Each figure is uncertain by at most ±{2 * se * 100:.1f} points (2 standard errors)</p>', t, count=1, flags=re.S)
    n = R['rows'][0]['cells']
    fp = [n[p]['share'] * 100 for p in PCTS]
    far, past, mod = ([R['rows'][i]['cells'][p]['share'] * 100 for p in PCTS] for i in (1, 2, 3))
    aside = (f'Why the top 1 %? The cutoff is a trade-off between how far out a nanowell must be to count and how many nanowells are left to count. '
             f'To measure it, we simulated {plates:,} plates for each row, each with four control wells and four molecule wells of 1,000 nanowells, '
             'with wells differing a little from one another, and ran the exceedance test with one cutoff for every well at the 95th, 99th or 99.9th percentile '
             'of the pooled control nanowells. '
             f'With no effect, the three cutoffs call {fp[0]:.1f}, {fp[1]:.1f} and {fp[2]:.1f} percent of plates significant. All three are above the 5 percent '
             'they promise, which is the few-wells excess of slide 31, and the excess is a little larger at the lower cutoffs. '
             f'When 1 % of a molecule well’s nanowells are far-out responders, two to four times the control 99th percentile, the 99.9th finds them {far[2]:.0f} percent of '
             f'the time, the 99th {far[1]:.0f} percent, and the 95th only {far[0]:.0f} percent, because at 95 % the responders are swamped by fifty ordinary nanowells per well. '
             f'When the responders are only just past the 99th percentile, the 99th is best, {past[1]:.0f} percent against {past[2]:.0f} for the 99.9th. '
             f'And when the effect is a modest rise in 3 % of nanowells rather than a few big jumps, the 99.9th finds it only {mod[2]:.0f} percent of the time, about its '
             'false-positive rate, so it is effectively blind to it. '
             'The 99th percentile is the only one with no blind spot: it is never the worst, and it finds every kind of responder well, which is why it is the default. '
             'It is best not to rely on one choice: run the test at the 95th and 99.9th too and report all three, decided before the data are seen, because picking '
             'whichever percentile gives the smallest p would be a hidden search. Higher Criticism, a second test, scans the tail and needs no cutoff at all. '
             'These are simulations with made-up effect sizes, a guide to the trade-off rather than a property of any real plate.')
    t = re.sub(r'<aside>.*?</aside>', '<aside>' + aside + '</aside>', t, count=1, flags=re.S)
    Path(path).write_text(t)


if __name__ == '__main__':
    main(sys.argv[1])
