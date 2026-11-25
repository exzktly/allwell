# Build slides 10 to 40 of the deck from the results files in ../results (run from anywhere).
import os as _os0, json as _jr
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve().parent; _RES = _HERE.parent / 'results'
_os0.chdir(_HERE)
"""Statistics section: how chance spread, the beta-binomial model, the test and the correction work."""
import math, random, sys
import numpy as np
from scipy import stats
sys.path.insert(0, str(_HERE.parents[3]))   # the repository root
from review.features.nanowell.rare_exceedance_compute import fit_bb

NAVY, OR, PUR, GRN, TEAL, CTRL, GRID = '#1B2A41', '#E07A2F', '#8E5BD0', '#3E9C6E', '#2A9D9D', '#9FB4D0', '#C9D3E0'
N, PI, R = 1000, 0.01, 0.01
C, A, B, CC = [7, 8, 14, 11], [21, 24, 28, 23], [9, 10, 64, 13], [13, 15, 15, 14]


def lab(x, y, w, h, cls='lab small', i=1, extra=''):
    assert x >= 0 and y >= 0 and x + w <= 1664, (x, y, w, h)
    return f'<p class="{cls}" style="left:{x:.0f}px; top:{y:.0f}px; width:{w:.0f}px; {extra}--i:{i}">{h}</p>'


def svg(label, body, h=620):
    return (f'<svg aria-label="{label}" width="1664" height="{h}" viewBox="0 0 1664 {h}" xmlns="http://www.w3.org/2000/svg">'
            + ''.join(body) + '</svg>')


STEPS = ['1 cutoff', '2 count K', '3 fit', '4 fit, δ = 0', '5 p-value', '6 correct']
def stepmap(on):
    """Slide 11's six steps in miniature, top right: the steps this slide covers are filled."""
    pos = [(0, 0), (176, 0), (352, 0), (352, 58), (176, 58), (0, 58)]
    sv, lb = [], []
    for i, (x, y) in enumerate(pos):
        hit = (i + 1) in on
        sv.append(f'<rect x="{x+2}" y="{y+2}" width="160" height="44" rx="10" fill="{"#2F6BDB" if hit else "#ECF1F8"}" stroke="#2F6BDB" stroke-width="2"/>')
        lb.append(f'<p class="mm" style="left:{x+2}px; top:{y+9}px; width:160px; {"color:#F7F5F0; font-weight:700" if hit else ""}">{STEPS[i]}</p>')
    sv.append('<path d="M164 24 h7 M340 24 h7 M432 48 v5 M350 82 h-7 M174 82 h-7" stroke="#2F6BDB" stroke-width="2.5"/>'
              '<path d="M170 19 l6 5 l-6 5 Z M346 19 l6 5 l-6 5 Z M427 52 l5 6 l5 -6 Z M344 77 l-6 5 l6 5 Z M168 77 l-6 5 l6 5 Z" fill="#2F6BDB"/>')
    return ('<div class="minimap" style="height:108px">\n<svg aria-label="The six steps of slide 11 in miniature; the ones this slide covers are filled: '
            + ', '.join(STEPS[i - 1] for i in sorted(on)) + '" width="520" height="108" viewBox="0 0 520 108" xmlns="http://www.w3.org/2000/svg">'
            + ''.join(sv) + '</svg>\n' + '\n'.join(lb) + '\n</div>')
import json as _j
_ORDER = _j.load(open('project/deck.json'))['order']
BAD = []
import re as _re2
def _twoletter(sid, html):
    t = _re2.sub(r'<[^>]+>', '\x00', html)
    for m in _re2.finditer(r'\bLR\b|(?<![A-Za-z])[KNLPxπβ][a-z](?![A-Za-z])', t):
        if m.group() not in ('No', 'Na', 'Pi'):
            BAD.append((sid, 'two-letter variable: ' + t[max(0, m.start() - 30):m.end() + 10]))
def slide(sid, eyebrow, title, svgs, labs, aside, after='', steps=None):
    labs = [_l.replace('<sub>', '<sub style="font-size:0.6em; vertical-align:-0.38em; line-height:0">').replace('<sub style="line-height:0">', '<sub style="font-size:0.6em; vertical-align:-0.38em; line-height:0">') for _l in labs]
    assert len(labs) <= 24, (sid, len(labs))
    if sid in _ORDER: _twoletter(sid, eyebrow + ' \x00 ' + title + ' \x00 ' + ' \x00 '.join(labs) + ' \x00 ' + aside + ' \x00 ' + ' '.join(svgs if isinstance(svgs, list) else [svgs]))
    # one idea per line: no " · " lists, no "; " joins, at most one "=" chain per line unless it is a single equation
    import re as _re, html as _h
    for _l in labs:
        _body = _re.sub(r'^<p[^>]*>|</p>$', '', _l)
        for _ln in _re.split(r'<br[^>]*>', _body):
            _s = _h.unescape(_re.sub(r'<[^>]+>', '', _ln)).strip()
            _s = _re.sub(r'^(\d+\.|•)\s+', '', _s)
            if sid in _ORDER and (' · ' in _s or '; ' in _s or _re.search(r'[a-z0-9%)²³]\. [A-Zβπ]', _s) or ('=' in _s and _re.search(r'[.:] [A-Za-zβπ]', _s.split('=')[-1] if False else _s) and _re.search(r'=.*\. [A-Z]|\. .*=', _s))):
                BAD.append((sid, _s[:120]))
    head = f'<div class="head">\n<p class="eyebrow">{eyebrow}</p>\n<h2>{title}</h2>\n</div>\n'
    if steps: head = f'<div class="headrow">\n{head}{stepmap(steps)}\n</div>\n'
    html = (f'<section id="{sid}" data-transition="fade">\n{head}'
            f'<div class="stage">\n{svgs}\n' + '\n'.join(labs) + f'\n</div>\n{after}<aside>{aside}</aside>\n</section>\n')
    assert len(svgs) < 52000
    open(f'project/slides/{sid}.html', 'w').write(html)
    print(sid, len(html), len(labs))


def stack(cx, base, k, color, d=15, cols=2, r=6.2):
    out = []
    for i in range(k):
        row, col = divmod(i, cols)
        out.append(f'<circle cx="{cx + (col - (cols - 1) / 2) * d:.1f}" cy="{base - d / 2 - row * d:.1f}" r="{r}" fill="{color}"/>')
    return out


def _lerp(a, b, f): return '#%02X%02X%02X' % tuple(round(int(a[i:i+2], 16) + (int(b[i:i+2], 16) - int(a[i:i+2], 16)) * f) for i in (1, 3, 5))
RAMP = ([_lerp('#E3EBF5', '#7F9CC4', q / 4) for q in range(5)] + [_lerp('#7F9CC4', '#1B2A41', q / 4) for q in range(1, 5)] + ['#1B2A41'])[:10]
FCW = [3, 9, 10, 8, 4, 1, 1]          # slide 9's control well: fold-change bins below the cutoff, most near x1
FCCOL = [RAMP[round(i * 9 / 6)] for i in range(7)]
def well_icon(cx, cy, r, k, seed, fill='#F7F5F0', sw=None):
    """A well drawn as on slide 9: blue circle, nanowell squares shaded by fold change (light blue low, navy high), K orange."""
    rnd = random.Random(seed); pitch = max(10.0, r / 4.6); sq = pitch * 0.79
    sw = sw or max(4, round(r * 0.07))
    n = int(r // pitch) + 1; cells = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if math.hypot(i * pitch, j * pitch) + pitch * 0.72 <= r - sw: cells.append((cx + i * pitch, cy + j * pitch))
    assert len(cells) >= k, (r, k, len(cells))
    hot = set(rnd.sample(range(len(cells)), k))
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#F7F5F0" stroke="#2F6BDB" stroke-width="{sw}"/>']
    for idx, (x, y) in enumerate(cells):
        col = OR if idx in hot else rnd.choices(FCCOL, FCW)[0]
        out.append(f'<rect x="{x-sq/2:.0f}" y="{y-sq/2:.0f}" width="{sq:.0f}" height="{sq:.0f}" rx="{sq*0.2:.0f}" fill="{col}"/>')
    return out

def fc_pattern(pid, g, seed=5):
    """A 3x3 tile of nanowell squares shaded by fold change, as on slide 9, for wells too dense to draw square by square."""
    rnd = random.Random(seed); c = g * 0.79; o = (g - c) / 2
    cells = ''.join(f'<rect x="{i*g+o:.2f}" y="{j*g+o:.2f}" width="{c:.2f}" height="{c:.2f}" rx="{c*0.2:.2f}" fill="{rnd.choices(FCCOL, FCW)[0]}"/>' for i in range(3) for j in range(3))
    return f'<pattern id="{pid}" width="{3*g}" height="{3*g}" patternUnits="userSpaceOnUse">{cells}</pattern>'

# ---------------------------------------------------------------- 1. two sources of spread
s, L = [], []
random.seed(3)
L.append(lab(0, 0, 1664, 'Five <b>control</b> wells from one donor: no molecule, so every exceedance is chance. '
             'Squares are nanowells, a sample of each well’s <b>n = 1,000</b>; <b class="orange">orange</b> = past the cutoff, every one drawn; <b>K</b> counts them, out of n.', 'lab left small', 1, 'line-height:1.35; '))
XS = [660, 880, 1100, 1320, 1540]
rowsS = [(180, None, [8, 12, 9, 11, 10]), (436, [0.5, 1.6, 0.8, 1.3, 0.7], [4, 17, 7, 14, 8])]
def fillfor(rate):
    if rate is None: return '#F7F5F0'
    return lerp('#FBF4EA', '#F2D7B5', min(1, (rate - 0.4) / 1.3))
def lerp(a_, b_, f): return '#%02X%02X%02X' % tuple(round(int(a_[i:i+2], 16) + (int(b_[i:i+2], 16) - int(a_[i:i+2], 16)) * f) for i in (1, 3, 5))
for ri, (cy, rates, ks) in enumerate(rowsS):
    for j, (cx, k) in enumerate(zip(XS, ks)):
        s += well_icon(cx, cy, 70, k, 300 + ri * 10 + j, sw=6)
        L.append(lab(cx - 70, cy + 76, 140, f'<b>K = {k}</b>', 'lab small', 2 + 2 * ri))
        if rates:
            L.append(lab(cx - 110, cy - 108, 220, f'true&#160;rate&#160;{rates[j]:g}&#160;%', 'lab small', 4))
s.append(f'<line x1="0" y1="300" x2="1664" y2="300" stroke="{GRID}" stroke-width="3"/>')
L.append(lab(0, 96, 560, '<b>1. Identical wells: binomial</b>', 'lab left', 2))
L.append(lab(0, 142, 580, 'Every nanowell, in every well, passes with one chance, π = 1&#160;%. Only <b>counting noise</b>: K lands 8 to 12.', 'lab left small', 2, 'line-height:1.3; '))
L.append(lab(0, 222, 580, '<b>K ~ Binomial(n, π)</b>  (“~” = “is drawn from”, not ≈)<br>Var(K) = nπ(1−π) = 9.9 → SD ≈ 3', 'lab left small', 2, 'font-family:\'Nunito Sans\', sans-serif; color:#1B2A41; line-height:1.4; '))
L.append(lab(0, 312, 560, '<b>2. Wells differ: beta-binomial</b>', 'lab left', 4))
L.append(lab(0, 356, 580, 'Each well gets its own rate p around π, spread set by <b>r</b>; then counting noise on top. K: 4 to 17.', 'lab left small', 4, 'line-height:1.3; '))
L.append(lab(0, 428, 580, '<b>p ~ Beta</b>(mean π, r): Var(p) = r·π(1−π)<br><b>K ~ Binomial(n, p)</b><br>Var(K) = nπ(1−π)[1 + (n−1)r] → SD ≈ 5 (r ≈ 0.002)', 'lab left small', 4, 'font-family:\'Nunito Sans\', sans-serif; color:#1B2A41; line-height:1.4; '))
s.append('<rect x="0" y="550" width="1664" height="70" rx="12" fill="#EEF3FA"/>')
L.append(lab(16, 555, 1632, '<b>r is not known in advance: it is fitted</b> from replicate wells of the same condition. Their observed spread of K is compared with what counting noise alone gives, nπ(1−π); the excess is r, roughly r ≈ (observed Var ÷ nπ(1−π) − 1) ÷ (n − 1). The real fit uses all wells of a comparison at once (slides 18–18).', 'lab left small', 5, 'line-height:1.25; color:#1B2A41; '))
slide('st_sources', 'The statistics · 1 of 7 · chance', 'Why wells’ counts K differ',
      svg('Two rows of five control wells, no molecule. Each well shows nanowell squares shaded by fold change, orange past the cutoff. Top row, wells '
          'identical with a true rate of 1 percent: counts 8, 12, 9, 11 and 10. Bottom row, wells that differ, each with its own true '
          'rate of 0.5, 1.6, 0.8, 1.3 and 0.7 percent: counts 4, 17, 7, 14 and 8.', s), L,
      'Before asking whether a molecule raised the count K, we need to know how much K moves when nothing is going on. So this slide '
      'shows only control wells, five wells of one donor with no molecule. Every orange square is a nanowell past the cutoff, and K is '
      'how many a well has out of its thousand. There are two reasons K differs between such wells. Top row: even if every well had '
      'exactly the same 1 % chance per nanowell, the counts would not all be 10; they land at 8, 12, 9 and so on, like tossing a '
      'thousand coins. That is counting noise. Bottom row: real wells are never quite identical. Small differences in the cells or '
      'conditions give each well its own true rate, here between 0.5 % and 1.6 %, and each count varies around its own rate, so the '
      'counts spread much more widely. The two rows are the two models the test chooses between. The top row is a binomial: a fixed '
      'chance per nanowell and nothing else, so the only spread is counting noise. The bottom row is a beta-binomial: two steps, first '
      'each well’s own true rate is drawn from a beta distribution, a curve of rates around the average, and then its count is drawn '
      'as a binomial around that rate. The beta-binomial has one more number than the binomial, r, how much the wells’ true rates '
      'differ; with r = 0 it is the binomial again. Strictly, a beta distribution has two parameters; written as its mean π and r, '
      'r fixes its width: the true rates have a variance of r × π(1 − π). r is the same number as the intra-well correlation, '
      'usually written ρ, the correlation between two nanowells of one well. In this row the true rates have an SD of about 0.45 '
      'percentage points, which is r ≈ 0.002, and the counts an SD of about 5. The two sources add, and in real plates the second is often the larger. The '
      'numbers are illustrative.')

# ---------------------------------------------------------------- 2. inflation
s, L = [], []
bi = stats.binom(N, PI)
a, b = PI * (1 - R) / R, (1 - PI) * (1 - R) / R
bb = stats.betabinom(N, a, b)
def icons(d, nw=100, kmax=40):
    p = np.array([d.pmf(k) for k in range(kmax + 1)]); p = p / p.sum() * nw
    c = np.floor(p).astype(int); rem = nw - c.sum()
    for i in np.argsort(-(p - c))[:rem]: c[i] += 1
    return c
X0, W, KMAX, P = 860, 18, 40, 13
for ti, (name, d, base, exk) in enumerate((('A: identical wells (r = 0)', bi, 280, 12), ('B: wells that differ (r = 0.01)', bb, 560, 17))):
    cnt = icons(d)
    for k, c in enumerate(cnt):
        for j in range(c):
            hl = (k == exk and j == c - 1)
            s.append(f'<circle cx="{X0 + k*W + W/2:.1f}" cy="{base - 8 - j*P:.1f}" r="{5.4 if not hl else 7}" '
                     f'fill="{NAVY if hl else ("#4A5A73" if k >= 20 else "#DCE6F2")}" stroke="{NAVY if hl else ("#4A5A73" if k >= 20 else "#2F6BDB")}" stroke-width="{1.6 if not hl else 2}"/>')
    s.append(f'<line x1="{X0-6}" y1="{base}" x2="{X0+(KMAX+1)*W}" y2="{base}" stroke="{NAVY}" stroke-width="3"/>')
    s.append(f'<line x1="{X0+20*W}" y1="{base-200}" x2="{X0+20*W}" y2="{base+6}" stroke="{NAVY}" stroke-width="2.5" stroke-dasharray="7 5"/>')
    # one example well, with an arrow to its marker in the stack
    ex, ey = 735, base - 80
    s += well_icon(ex, ey, 52, exk, 40 + ti)
    jtop = cnt[exk] - 1
    tx, ty = X0 + exk * W + W / 2, base - 8 - jtop * P
    s.append(f'<path d="M{ex+56} {ey} C {ex+110} {ey}, {tx-30} {ty-40}, {tx-6} {ty-9}" stroke="{NAVY}" stroke-width="3" fill="none"/>'
             f'<path d="M{tx-4} {ty-6} l-14 -4 l6 -12 Z" fill="{NAVY}"/>')
    L.append(lab(ex - 100, ey + 58, 200, f'one well: K&#160;=&#160;{exk}', 'lab small', 2 + ti))
    L.append(lab(X0, base - 236, 700, f'<b>{name}</b>: 100 untreated wells', 'lab left small', 2 + ti))
    L.append(lab(X0 + 20 * W + 12, base - 196, 300, f'K ≥ 20: <b style="color:#4A5A73">{d.sf(19)*100:.1f}&#160;%</b>'.replace('14.9', '15'), 'lab left small', 2 + ti))
for k in (0, 10, 20, 30, 40):
    L.append(lab(X0 + k * W + W / 2 - 30, 566, 60, str(k), 'lab small', 3))
L.append(lab(X0, 596, 740, 'K per well (its count of nanowells past the cutoff), not a fold change', 'lab small', 3))
L.append(lab(860, 0, 804, '1 <b>○</b> = 1 whole well, placed at its K · filled ● = K ≥ 20', 'lab left small', 1, 'line-height:1.25; '))
L.append(lab(0, 0, 740, 'Right: 100 imagined untreated wells (a real donor has 4). <b>r is never tested itself</b>: it sets how big a real effect must be (slide 23).', 'lab left small', 1, 'line-height:1.35; '))
L.append(lab(0, 84, 680, 'Var(K) = <span style="color:#2F6BDB">n</span><span style="color:#C2621F">π</span>(1−<span style="color:#C2621F">π</span>) × [1 + (<span style="color:#2F6BDB">n</span>−1) <span style="color:#8E5BD0">r</span>]', 'lab left', 1, 'font-size:40px; font-weight:700; '))
L.append(lab(0, 146, 680, '<b style="color:#2F6BDB">n</b> = 1,000 nanowells · <b style="color:#C2621F">π</b> = 0.01 · <b style="color:#8E5BD0">r</b> = how much wells differ<br>counting noise: <span style="color:#2F6BDB">1,000</span> × <span style="color:#C2621F">0.01</span> × 0.99 = 9.9; then × the bracket', 'lab left small', 1, 'line-height:1.3; '))
L.append(lab(0, 222, 680, '<b>Identical, <span style="color:#8E5BD0">r = 0</span>:</b> 9.9 × [1 + 999 × <b style="color:#8E5BD0">0</b>] = <b>9.9</b> → SD 3', 'lab left small', 2))
L.append(lab(0, 258, 680, '<b>Differing, <span style="color:#8E5BD0">r = 0.01</span>:</b> 9.9 × [1 + 999 × <b style="color:#8E5BD0">0.01</b>] = <b>109</b> → SD&#160;10', 'lab left small', 3))
L.append(lab(0, 306, 680, '<b>How r shapes the beta-binomial</b> (chance of each K; mean 10 throughout): larger r lowers the peak, pushes it toward 0 and stretches a long right tail.', 'lab left small', 4, 'line-height:1.25; '))
CX0, CX1, CY0, CYB = 10, 400, 404, 530
LEG = []
def cxk(k): return CX0 + k / 40 * (CX1 - CX0)
def cyp(p): return CYB - min(p, 0.13) / 0.13 * (CYB - CY0)
for rr, col, txt, i_ in ((0, NAVY, 'r = 0: Var ×1', 0), (0.002, '#2F6BDB', 'r = 0.002: Var ×3', 1), (0.01, PUR, 'r = 0.01: Var ×11', 2)):
    dd = stats.binom(N, PI) if rr == 0 else stats.betabinom(N, PI * (1 - rr) / rr, (1 - PI) * (1 - rr) / rr)
    s.append(f'<path d="M' + ' L'.join(f'{cxk(k):.1f} {cyp(dd.pmf(k)):.1f}' for k in range(0, 41)) + f'" stroke="{col}" stroke-width="4" fill="none"/>')
    s.append(f'<line x1="424" y1="{420 + i_*40}" x2="450" y2="{420 + i_*40}" stroke="{col}" stroke-width="5"/>')
    LEG.append(f'<span style="color:{col}">{txt}</span>')
s.append(f'<line x1="{CX0}" y1="{CYB}" x2="{CX1}" y2="{CYB}" stroke="{NAVY}" stroke-width="2.5"/>')
s.append(f'<line x1="{cxk(20)}" y1="{CY0}" x2="{cxk(20)}" y2="{CYB}" stroke="{NAVY}" stroke-width="2" stroke-dasharray="5 4"/>')
L.append(lab(458, 404, 222, '<br>'.join(LEG), 'lab left small', 4, 'line-height:40px; '))
for k in (0, 20, 40):
    L.append(lab(max(0, cxk(k) - 24), 534, 48, str(k), 'lab small', 4))
L.append(lab(0, 572, 680, '×1, ×3, ×11 = the bracket 1 + (n−1)r: the variance relative to the binomial. SD 3 → 5 → 10.', 'lab left small', 4, 'line-height:1.25; '))
slide('st_inflate', 'The statistics · 2 of 7 · chance', 'Wells that differ make the count far more variable',
      svg('Two stacks of small well icons, 100 untreated wells each, every icon placed at its count K. Top, wells identical: a tall narrow '
          'stack around 10, with K of 20 or more in 0.3 percent. Bottom, wells differing with r = 0.01: a long low spread from 0 to about '
          '40, with K of 20 or more, drawn orange, in 15 percent. A cartoon well on the left of each stack, with its orange squares, has an '
          'arrow to its own icon in the stack.', s), L,
      'This slide stacks up many untreated wells. Each small circle is one well, placed along the axis at its count K, the number of '
      'its nanowells past the cutoff; the big cartoon well beside each stack is one of them, with an arrow to its place. Top: if all '
      'wells were identical, a hundred wells pile up tightly around 10, and only 0.3 % reach 20 or more. Bottom: when wells differ by '
      'r = 0.01, the same hundred wells spread out, and 15 % reach 20 or more, drawn orange. The formula says why: counting noise alone '
      'gives a variance of n π (1 − π), 9.9 here, and differing wells multiply it by 1 + (n − 1) r, about 11, giving about 109, a '
      'standard deviation of 10 instead of 3. The multiplier is large because n is large. The same r is also the correlation between '
      'two nanowells in the same well: they share the well’s conditions. The small chart at the bottom left shows how r changes the '
      'shape of the beta-binomial itself, with the mean held at 10: the curve is the chance of each count K for one well. At r = 0 it '
      'is the binomial, a narrow peak at 10. At r = 0.002 the variance is three times larger, the peak lower and wider. At r = 0.01 it '
      'is eleven times larger: the most likely count moves toward zero, because many wells have a true rate below 1 %, while a long '
      'tail to the right holds the few wells whose true rate is high. That tail is the overdispersion, and it is why a count of 20 '
      'goes from rare to ordinary.')

# ---------------------------------------------------------------- merged: why K varies (binomial vs beta-binomial)
s, L = [], []
EQ2 = "line-height:1.35; color:#1B2A41; "
NC, PCc, RC = '#2F6BDB', '#C2621F', '#8E5BD0'
s.append(f'<line x1="0" y1="300" x2="790" y2="300" stroke="{GRID}" stroke-width="3"/>')
s.append(f'<line x1="830" y1="0" x2="830" y2="610" stroke="{GRID}" stroke-width="3"/>')
def _stack(cx, base, k, col):
    for m in range(k):
        s.append(f'<circle cx="{cx - 25 + (m % 6) * 10}" cy="{base - 7 - (m // 6) * 10}" r="4.2" fill="{col}"/>')
    return base - 7 - ((k - 1) // 6) * 10
_ib = lambda x: f'<span style="display:inline-block; width:90px; text-align:center">{x}</span>'
for ri, (base, ks, name, ncol, th) in enumerate(((236, C, 'Control', NAVY, None), (548, B, 'Molecule B', '#2E8B57', True))):
    s.append(f'<line x1="440" y1="{base}" x2="800" y2="{base}" stroke="{NAVY}" stroke-width="2.5"/>')
    for j, k in enumerate(ks):
        top = _stack(485 + j * 90, base, k, OR)
        txt = f'<b>{k}</b>' + (f'<br><span style="font-size:19px">π<sub>w</sub>≈{k / 10:.1f}&#160;%</span>' if th else '')
        L.append(lab(485 + j * 90 - 45, top - (62 if th else 40), 90, txt, 'lab small', 2 + 2 * ri, 'line-height:1.0; '))
L.append(lab(440, 0, 360, '<b>Control wells, slide 9</b><br>1 <span style="color:#E07A2F">●</span> = 1 nanowell past the cutoff<br>Stack height = K', 'lab left small', 2, 'line-height:1.2; font-size:21px; '))
L.append(lab(440, 312, 360, '<b style="color:#2E8B57">Molecule B wells, slide 9</b><br>each well’s π<sub>w</sub> ≈ K ÷ 1,000', 'lab left small', 4, 'line-height:1.2; font-size:21px; '))
L.append(lab(0, 0, 400, '<b>1. Identical wells: binomial</b>', 'lab left', 2))
L.append(lab(0, 42, 380, 'One chance per nanowell: π = 1&#160;%<br>Only <b>counting noise</b> moves K<br>7 to 14 is about what it gives', 'lab left small', 2, 'line-height:1.25; '))
L.append(lab(0, 160, 380, '<b>K ~ Binomial(n, π)</b><br>(~ = “is drawn from”)<br>Var(K) = nπ(1−π) = 9.9, SD 3', 'lab left small', 2, EQ2))
L.append(lab(0, 312, 400, '<b>2. Wells differ: beta-binomial</b>', 'lab left', 4))
L.append(lab(0, 352, 370, 'Each well has its own rate π<sub>w</sub><br>Set by T cells, DCs, plate spot<br>Counting noise then acts on top<br>A 64 is far beyond counting noise', 'lab left small', 4, 'line-height:1.2; '))
L.append(lab(0, 500, 400, f'<b>π<sub>w</sub> ~ Beta(mean π, r)</b><br>Var(π<sub>w</sub>) = r·π(1−π)<br><b>K ~ Binomial(n, π<sub>w</sub>)</b>', 'lab left small', 4, EQ2))
# right: the formula and how r shapes the curve
_nn, _pp, _rr = f'<span style="color:{NC}">n</span>', f'<span style="color:{PCc}">π</span>', f'<span style="color:{RC}">r</span>'
L.append(lab(870, 0, 794, f'Var(K) = {_nn}{_pp}(1−{_pp}) + {_nn}({_nn}−1) {_rr} {_pp}(1−{_pp})', 'lab left', 5, 'font-size:36px; font-weight:700; '))
_w2 = lambda rr: N * (N - 1) * rr * PI * (1 - PI)
L.append(lab(870, 56, 794, f'<b>First term: counting noise</b>, {N * PI * (1 - PI):.1f}, the binomial’s variance<br><b>Second term: wells differ</b>, {_nn}({_nn}−1) × Var(πᵢ), since Var(πᵢ) = {_rr}{_pp}(1−{_pp})<br>'
          f'Second term: 0 at r = 0, {_w2(0.002):.1f} at r = 0.002, {_w2(0.01):.1f} at r = 0.01<br>Total: {N * PI * (1 - PI):.1f}, {N * PI * (1 - PI) + _w2(0.002):.1f}, {N * PI * (1 - PI) + _w2(0.01):.1f} (n = 1,000, π = 0.01)', 'lab left small', 5, 'line-height:1.22; font-size:20px; '))
CX0, CX1, CY0, CYB = 900, 1420, 250, 490
def cxk(k): return CX0 + k / 40 * (CX1 - CX0)
def cyp(p): return CYB - min(p, 0.13) / 0.13 * (CYB - CY0)
LEG = []
for rr, col, txt, i_ in ((0, NAVY, 'r = 0: SD 3', 0), (0.002, '#2F6BDB', 'r = 0.002: SD 5', 1), (0.01, PUR, 'r = 0.01: SD 10', 2)):
    dd = stats.binom(N, PI) if rr == 0 else stats.betabinom(N, PI * (1 - rr) / rr, (1 - PI) * (1 - rr) / rr)
    s.append(f'<path d="M' + ' L'.join(f'{cxk(k):.1f} {cyp(dd.pmf(k)):.1f}' for k in range(0, 41)) + f'" stroke="{col}" stroke-width="5" fill="none"/>')
    s.append(f'<line x1="1440" y1="{296 + i_*44}" x2="1470" y2="{296 + i_*44}" stroke="{col}" stroke-width="6"/>')
    LEG.append(f'<span style="color:{col}">{txt}</span>')
L.append(lab(1478, 278, 186, '<br>'.join(LEG), 'lab left small', 5, 'line-height:44px; '))
s.append(f'<line x1="{CX0}" y1="{CYB}" x2="{CX1}" y2="{CYB}" stroke="{NAVY}" stroke-width="3"/>')
s.append(f'<line x1="{cxk(20)}" y1="{CY0}" x2="{cxk(20)}" y2="{CYB}" stroke="{NAVY}" stroke-width="2" stroke-dasharray="6 5"/>')
for k in (0, 20, 40):
    L.append(lab(cxk(k) - 30, CYB + 6, 60, str(k), 'lab small', 5))
L.append(lab(870, 196, 600, 'Chance of each K for one untreated well (mean 10):', 'lab left small', 5))
L.append(lab(870, 556, 794, 'K per well ≥ 20 (dashed): <b>0.3&#160;%</b> → <b>6&#160;%</b> → <b>15&#160;%</b><br>Larger r: lower peak, long right tail<br>r is fitted from replicate wells (slides 15 and 28)', 'lab left small', 6, 'line-height:1.25; '))
slide('st_vary', 'The statistics · why counts vary', 'Why wells’ counts K differ',
      svg('Left, two rows of four wells, each drawn as a stack of orange dots, one per nanowell past the cutoff, so the stack’s height is K. Top, identical wells '
          '(binomial): slide 9’s control wells, K = 7, 8, 14 and 11. Bottom, wells that differ (beta-binomial): slide 9’s molecule-B wells, K = 9, 10, 64 and 13, rates π<sub>w</sub> of about 0.9, 1.0, 6.4 and 1.3 percent. '
          'Right: the variance formula, and the chance of each K for one untreated well at r = 0, 0.002 and 0.01; larger r gives a lower '
          'peak and a longer right tail, and the share of wells with K of 20 or more rises from 0.3 to 6 to 15 percent.', s), L,
      'Before asking whether a molecule raised the count K, we need to know how much K moves when nothing is going on, so this slide '
      'uses control wells in its top row and slide 9’s molecule-B wells in its bottom row. K is how many of a well’s n = 1,000 nanowells pass the cutoff. Top left: if every nanowell in every well '
      'had the same 1 % chance, the counts would still differ, as slide 9’s control wells do, 7, 8, 14 and 11, like tossing a thousand coins: that is a binomial, and its '
      'spread is counting noise, a variance of n π (1 − π) = 9.9, SD 3. The tilde means “is drawn from”. Bottom left: real wells are never '
      'identical, for biological and technical reasons that act on a whole well at once. Each well receives its own random sample of '
      'T cells, and responders are rare, so one well may by chance hold two precursors that recognise the antigen and another none. '
      'Each well gets its own batch of dendritic cells, which differ in number, viability and how much antigen they took up and present. '
      'And each well has its own position on the plate, with its own temperature, evaporation and gas exchange at the edges, and its own '
      'focus and timing in imaging. Everything in one well shares these, so they give each well its own true '
      'rate π<sub>w</sub>, scattered around π by an amount set by r; then counting noise acts on top. The letter p is kept for p-values. That two-step model is the beta-binomial. The bottom row uses slide 9’s molecule-B wells because they show it: three sit near 1 %, one near 6 %, a well-level difference that counting noise, SD 3, cannot produce. On the '
      'right, the formula adds the two parts. The first term, n π (1 − π) = 9.9, is counting noise, the variance identical wells would have. The second, n (n − 1) r π (1 − π), is the extra variance from wells that differ: '
      'it is 0 for identical wells, 19.8 at r = 0.002 and 98.9 at r = 0.01, so the total is 9.9, 29.7 or 108.8. The same formula is often written factored, as n π (1 − π) times 1 + (n − 1) r. The curves show what that does to one untreated well’s count, with the mean held at '
      '10: the peak drops and moves toward zero, and a long tail appears to the right, so a count of 20 or more goes from rare, 0.3 %, to '
      'ordinary, 15 %. r is never known in advance; it is fitted from replicate wells, as the following slides show.')

# ---------------------------------------------------------------- road map: the steps to one p-value
s, L = [], []
CW_, CH_, GX, R1, R2 = 520, 254, 52, 92, 380
s.append('<rect x="0" y="0" width="1664" height="80" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(16, 8, 1632, '<b>Tested: only the gap δ</b> (step 3) between molecule and control.<br><b>Not tested: r</b>, which sets how far chance alone can move δ', 'lab left small', 1, 'color:#1B2A41; line-height:1.2; '))
steps = [
 ('1. Set the cutoff', 'The 99th percentile of the control nanowells’ fold change.<br>One line for all wells (slide 9).'),
 ('2. Count K per well', 'K: nanowells past the line, per well.<br>Control: 7, 8, 14, 11<br>A: 21, 24, 28, 23'),
 ('3. Fit as two groups', 'Control and A each get their own mean rate.<br>r: how much wells differ (slides 12–15).'),
 ('4. Fit as one group', 'One mean rate for all 8 wells: no gap, δ = 0.<br>r is fitted again (slides 15–17).'),
 ('5. Compare the fits → p', 'p: the chance of a gap this large if there were none.<br>A: p = 0.00008 (slide 18).'),
 ('6. Correct across molecules', 'Several molecules tested: more chance of a false call.<br>Each p becomes a corrected q (slide 24).')]
for i, (ti, tx) in enumerate(steps):
    col_ = i if i < 3 else 2 - (i - 3)
    cx0, cy0 = col_ * (CW_ + GX), (R1 if i < 3 else R2)
    s.append(f'<rect x="{cx0}" y="{cy0}" width="{CW_}" height="{CH_}" rx="18" fill="#ECF1F8"/>')
    if i < 2:
        ax = cx0 + CW_ + 6; ay = cy0 + CH_ / 2
        s.append(f'<path d="M{ax} {ay-8} h22 v-10 l18 18 l-18 18 v-10 h-22 Z" fill="{NAVY}"/>')
    elif i in (3, 4):
        ax = cx0 - 6; ay = cy0 + CH_ / 2
        s.append(f'<path d="M{ax} {ay-8} h-22 v-10 l-18 18 l18 18 v-10 h22 Z" fill="{NAVY}"/>')
    elif i == 2:
        s.append(f'<path d="M{cx0 + CW_/2 - 8} {cy0 + CH_ + 3} v10 h-10 l18 14 l18 -14 h-10 v-10 Z" fill="{NAVY}"/>')
    L.append(lab(cx0 + 20, cy0 + 10, CW_ - 40, f'<b>{ti}</b>', 'lab left', 2 + i))
    L.append(lab(cx0 + 20, cy0 + 50, CW_ - 40, tx, 'lab left small', 2 + i, 'line-height:1.2; '))
    by = cy0 + 222
    if i == 0:
        for bi_, nh in enumerate([1, 2, 4, 6, 7, 6, 4, 2, 1]):
            for j in range(nh):
                s.append(f'<circle cx="{cx0 + 150 + bi_ * 18}" cy="{by + 26 - j * 9}" r="4" fill="{FCCOL[min(6, bi_ * 6 // 8)]}"/>')
        for j in range(3):
            s.append(f'<circle cx="{cx0 + 336 + j * 12}" cy="{by + 26}" r="4" fill="{OR}"/>')
        s.append(f'<line x1="{cx0 + 318}" y1="{by - 40}" x2="{cx0 + 318}" y2="{by + 32}" stroke="{NAVY}" stroke-width="2.5" stroke-dasharray="6 4"/>')
    elif i == 1:
        s += well_icon(cx0 + 200, by - 8, 42, 8, 501, sw=4)
        L.append(lab(cx0 + 256, by - 26, 200, '→ <b>K = 8</b>', 'lab left small', 3))
    elif i in (2, 3):   # the 8 wells as dots on a K axis, under one curve per group (step 3) or one curve for all (step 4)
        ay_ = cy0 + 236; kx_ = lambda k: cx0 + 40 + k * 12.5
        def _crv(mu_, sd_, ht_): return 'M' + ' L'.join(f'{kx_(k):.1f} {ay_ - ht_ * math.exp(-0.5 * ((k - mu_) / sd_) ** 2):.1f}' for k in np.linspace(0, 34, 60))
        if i == 2:
            s.append(f'<path d="{_crv(10.0, 3.2, 66)}" stroke="{NAVY}" stroke-width="3.5" fill="none"/><path d="{_crv(24, 4.4, 56)}" stroke="{PUR}" stroke-width="3.5" fill="none"/>')
            for m_, c_ in ((10.0, NAVY), (24, PUR)):
                s.append(f'<line x1="{kx_(m_):.1f}" y1="{cy0 + 152}" x2="{kx_(m_):.1f}" y2="{ay_}" stroke="{c_}" stroke-width="2.5" stroke-dasharray="6 4"/>')
            s.append(f'<line x1="{kx_(10.0) + 10:.1f}" y1="{cy0 + 152}" x2="{kx_(24) - 10:.1f}" y2="{cy0 + 152}" stroke="#2F6BDB" stroke-width="4"/>'
                     f'<path d="M{kx_(10.0) + 12:.1f} {cy0 + 144} l-11 8 l11 8 Z M{kx_(24) - 12:.1f} {cy0 + 144} l11 8 l-11 8 Z" fill="#2F6BDB"/>')
            L.append(lab(int(kx_(17.0)) - 160, cy0 + 114, 320, '<b style="color:#2F6BDB">the gap (as log-odds: δ)</b>', 'lab small', 4, 'font-size:21px; '))
            L.append(lab(cx0 + 20, cy0 + 150, 100, '<b>control</b>', 'lab left small', 4, 'font-size:20px; '))
            L.append(lab(cx0 + 410, cy0 + 150, 90, '<b style="color:#6B3FB0">A</b>', 'lab left small', 4, 'font-size:20px; '))
        else:
            s.append(f'<path d="{_crv(17.0, 7.5, 40)}" stroke="#5A6679" stroke-width="3.5" fill="none"/>')
            s.append(f'<line x1="{kx_(17.0):.1f}" y1="{cy0 + 152}" x2="{kx_(17.0):.1f}" y2="{ay_}" stroke="#5A6679" stroke-width="2.5" stroke-dasharray="6 4"/>')
            L.append(lab(int(kx_(17.0)) - 160, cy0 + 118, 320, '<b style="color:#5A6679">one mean for all 8 wells</b>', 'lab small', 5, 'font-size:22px; '))
        s.append(f'<line x1="{cx0 + 34}" y1="{ay_}" x2="{cx0 + 470}" y2="{ay_}" stroke="{NAVY}" stroke-width="2.5"/>')
        for k_ in (7, 8, 14, 11): s.append(f'<circle cx="{kx_(k_):.1f}" cy="{ay_}" r="7.5" fill="{CTRL}" stroke="{NAVY}" stroke-width="2"/>')
        for k_ in (21, 24, 28, 23): s.append(f'<circle cx="{kx_(k_):.1f}" cy="{ay_}" r="7.5" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
        L.append(lab(cx0 + 474, ay_ - 16, 40, '<b>K</b>', 'lab left small', 2 + i, 'font-size:20px; '))
    elif i == 4:
        zs = np.linspace(-3, 4.2, 70)
        d = 'M' + ' L'.join(f'{cx0 + 60 + (z + 3) * 46:.1f} {by + 26 - stats.norm.pdf(z) * 120:.1f}' for z in zs)
        s.append(f'<path d="{d}" stroke="{NAVY}" stroke-width="3" fill="none"/>')
        zx = cx0 + 60 + 6.77 * 46
        s.append(f'<line x1="{zx:.1f}" y1="{by - 30}" x2="{zx:.1f}" y2="{by + 28}" stroke="{PUR}" stroke-width="4"/>')
        L.append(lab(cx0 + 60, by - 52, 200, 'chance alone', 'lab left small', 6, 'color:#4A5568; '))
        L.append(lab(int(zx) + 8, by - 30, 150, '<b style="color:#6B3FB0">A</b>', 'lab left small', 6))
    else:
        L.append(lab(cx0 + 20, by - 78, 230, '<b style="color:#6B3FB0">A: q = 0.00024 ✓</b><br>C: q = 0.061<br>B: q = 0.17', 'lab left small', 7, 'line-height:1.2; font-size:21px; '))
        L.append(lab(cx0 + 270, by - 78, 220, '<span style="color:#4A5568">significant<br>when q &lt; 0.05</span>', 'lab left small', 7, 'line-height:1.2; font-size:21px; '))
slide('st_road', 'The statistics · overview', 'Six steps from images to one p-value',
      svg('Six step cards in two rows with arrows between them. 1, set the cutoff from the control nanowells: a dot cloud with a dashed '
          'line and a few orange dots past it. 2, count K per well: a well cartoon. 3, fit with the gap: the eight wells as dots on a K axis under two curves, control and A, with a double arrow between the two means marked as the gap, which the model measures as δ on the log-odds scale. '
          '4, fit again with no gap: the same eight dots under one wider curve with one mean. 5, compare the fits: a bell curve with a line far '
          'in its tail. 6, correct across molecules: three bars.', s, h=654), L,
      'This slide is the map for the rest of the statistics. Step one: the control wells set one cutoff, the 99th percentile of '
      'their nanowells’ fold change, and the same line is used for every well. Step two: each well becomes one number, K, the count '
      'of its nanowells past that line. Step three: the beta-binomial model is fitted by maximum likelihood to all eight wells of '
      'the comparison at once. It estimates three things together: the control rate, the gap δ between molecule and control, and '
      'one shared r, how much replicate wells differ. r is not estimated first and on its own; it comes out of the same fit. For '
      'molecule A the gap is 0.89 on the log-odds scale, an odds ratio of 2.4, and r is close to zero. Step four: the same model is '
      'fitted again with the gap forced to zero, one rate for all eight wells; r is refitted and now has to absorb whatever gap there '
      'was, so it comes out larger, 0.0027 for A. Step five: the two fits are compared. Twice the difference in their log-likelihoods, '
      '14.2, is the likelihood ratio; its square root, 3.77, is a z-score, and the one-sided p-value is 0.00008. Step six: with several '
      'molecules on the donor, the p-values are corrected together by Benjamini–Hochberg, giving q, 0.00024 for A. The next slides '
      'take these steps one at a time.')

# ---------------------------------------------------------------- 3. wells vs nanowells
s, L = [], []
s.append('<defs>' + fc_pattern('g1000', 6) + ''
         '<clipPath id="wc1"><circle cx="90" cy="130" r="80"/></clipPath></defs>')
s.append('<circle cx="90" cy="130" r="86" fill="#F7F5F0" stroke="#2F6BDB" stroke-width="6"/><rect x="10" y="50" width="160" height="160" fill="url(#g1000)" clip-path="url(#wc1)"/>')
s.append(f'<path d="M210 120 H250 M210 140 H250" stroke="{NAVY}" stroke-width="5"/>')
_r91 = random.Random(91)
for i in range(91):
    rr, cc = divmod(i, 13)
    s.append(f'<rect x="{290 + cc*24}" y="{48 + rr*24}" width="19" height="19" rx="4" fill="{OR if i == 47 else _r91.choices(FCCOL, FCW)[0]}"/>')
for j, (cx, k) in enumerate(((70, 20), (230, 21), (390, 22))):
    s += well_icon(cx, 440, 62, k, 70 + j) if False else []
s += well_icon(80, 440, 62, 20, 61)
for j, cx in enumerate((320, 470, 620)):
    s += well_icon(cx, 440, 62, 20 + j, 71 + j)
L.append(lab(0, 0, 760, '<b>1 well of 1,000 nanowells</b> ≈ <b>91</b> independent ones (r = 0.01)', 'lab left small', 1))
L.append(lab(0, 228, 760, 'Nanowells in one well share its conditions, so they partly repeat each other<br>Effective count: n ÷ [1 + (n−1) r] = 1,000 ÷ 11', 'lab left small', 1, 'line-height:1.3; font-size:21px; '))
L.append(lab(0, 334, 220, '<b>One</b> well at K&#160;=&#160;20', 'lab small', 2))
L.append(lab(0, 510, 190, '<b>15&#160;%</b> of untreated wells do this: weak', 'lab small', 2, 'line-height:1.3; '))
L.append(lab(250, 334, 440, '<b>Three</b> independent wells all at 20+', 'lab small', 3))
L.append(lab(250, 510, 440, '0.15 × 0.15 × 0.15 ≈ <b>0.34&#160;%</b>: strong', 'lab small', 3))
def se(m, n): return math.sqrt(PI * (1 - PI) / (m * n) * (1 + (n - 1) * R)) * 100
PY0, PH, PW = 120, 300, 300
def ys(v): return PY0 + PH - v / 0.8 * PH
panels = ((960, 'more nanowells per well', 'with 4 replicate wells', lambda f: (4, 10 ** (2 + 2 * f)), ['100', '1,000', '10,000'], 'levels off at ± 0.50', [(4, 100), (4, 1000), (4, 10000)]),
          (1340, 'more replicate wells', 'with 1,000 nanowells each', lambda f: (2 + 14 * f, 1000), ['2', '9', '16'], 'keeps shrinking', [(2, 1000), (9, 1000), (16, 1000)]))
for pi_, (PX, t1, t2, fn, ticks, note, pts) in enumerate(panels):
    s.append(f'<path d="M{PX} {PY0} V{PY0+PH} H{PX+PW}" stroke="{NAVY}" stroke-width="3" fill="none"/>')
    d = 'M' + ' L'.join(f'{PX + f*PW:.1f} {ys(se(*fn(f))):.1f}' for f in np.linspace(0, 1, 50))
    s.append(f'<path d="{d}" stroke="{"#6B7686" if pi_ == 0 else "#2F6BDB"}" stroke-width="5" fill="none"/>')
    for (m_, n_), xf in zip(pts, (0, 0.5, 1)):
        s.append(f'<circle cx="{PX + xf*PW:.1f}" cy="{ys(se(m_, n_)):.1f}" r="7" fill="{NAVY}"/>')
        s.append(f'<line x1="{PX + xf*PW:.1f}" y1="{PY0+PH}" x2="{PX + xf*PW:.1f}" y2="{PY0+PH+8}" stroke="{NAVY}" stroke-width="3"/>')
    for (lbl, xf) in zip(ticks, (0, 0.5, 1)):
        L.append(lab(min(PX + xf * PW - 50, 1664 - 100), PY0 + PH + 12, 100, lbl, 'lab small', 3 + pi_))
    L.append(lab(PX - 20, PY0 + PH + 52, PW + 40, f'<b>{t1}</b><br>{t2}', 'lab small', 3 + pi_, 'line-height:1.25; '))
    L.append(lab(PX + 30, ys(0.72) - 14 if pi_ == 0 else ys(0.62) - 10, PW - 30, f'<b style="color:{"#4A5568" if pi_ == 0 else "#2F6BDB"}">{note}</b>', 'lab left small', 3 + pi_))
for v in (0, 0.4, 0.8):
    s.append(f'<line x1="952" y1="{ys(v)}" x2="960" y2="{ys(v)}" stroke="{NAVY}" stroke-width="3"/>')
    L.append(lab(870, ys(v) - 16, 76, f'±{v:g}', 'lab small', 2, 'text-align:right; '))
L.append(lab(870, 0, 794, '<b>Confidence comes from replicate wells</b><br>How precisely a group’s rate is known (± % points, 1 SE, r = 0.01)', 'lab left small', 2, 'line-height:1.3; '))
slide('st_wells', 'Backup · replicate wells', 'Replicate wells are the evidence, not nanowells',
      svg('Left top: a well drawn with a dense grid of 1,000 nanowells equals a grid of 91 separate squares, the independent nanowells it is '
          'worth. Left bottom: one well with 20 orange squares, then three wells each with about 20. Right: two small line charts on the same '
          'axis, how precisely a group’s rate is known. Left chart, more nanowells per well with 4 replicate wells: plus or minus 0.70, '
          '0.52 and 0.50 percentage points at 100, 1,000 and 10,000 nanowells, levelling off. Right chart, more replicate wells with 1,000 '
          'nanowells each: 0.74, 0.35 and 0.26 at 2, 9 and 16 wells, still falling.', s), L,
      'The question the test asks is whether a molecule’s wells sit further from the control wells than replicate wells of one '
      'condition differ from each other. So the confidence comes from the replicate wells, and this slide shows why. Nanowells in the '
      'same well share its conditions, so they partly repeat each other. Top left: at r = 0.01, a well of a thousand nanowells carries '
      'about as much evidence as 91 independent nanowells, a thousand divided by the multiplier of 11 from slide 10. Bottom left: '
      'one treated well with 20 exceedances is not convincing, since 15 % of untreated wells reach that by chance; three independent '
      'replicate wells that all reach it are, about 0.15 cubed, 0.34 %. On the right, both charts show the same thing on the same axis: '
      'how precisely a group’s rate is known, as one standard error in percentage points, for a group whose wells differ by r = 0.01. '
      'Left: keep four replicate wells and give each more nanowells. Going from 100 to 1,000 nanowells helps, but past that the '
      'precision stops improving at about plus or minus 0.5, because the wells’ own differences do not average away inside a well. '
      'Right: keep 1,000 nanowells per well and add replicate wells. The precision keeps improving, roughly with the square root of '
      'the number of wells. So a molecule is called different from the controls when its excess is large next to the spread between '
      'replicate wells, and the way to detect smaller effects is to add wells, not nanowells.')

# ---------------------------------------------------------------- 4. estimating r
s, L = [], []
X0, Y0, HH = 110, 100, 276
def yk(k): return Y0 + HH - k / 25 * HH
m = np.mean(C); bv = m * (1 - m / N); sd = math.sqrt(bv)
s.append(f'<rect x="{X0}" y="{yk(m+2*sd):.1f}" width="440" height="{yk(m-2*sd)-yk(m+2*sd):.1f}" fill="{CTRL}" fill-opacity="0.35"/>')
s.append(f'<path d="M{X0} {Y0} V{Y0+HH} H{X0+440}" stroke="{NAVY}" stroke-width="3" fill="none"/>')
for j, k in enumerate(C):
    cx = X0 + 60 + j * 108
    s.append(f'<circle cx="{cx}" cy="{yk(k):.1f}" r="13" fill="{CTRL}" stroke="{NAVY}" stroke-width="3"/>')
    s.append(f'<line x1="{cx}" y1="{yk(k)+14:.1f}" x2="{cx}" y2="{Y0+HH+18}" stroke="{NAVY}" stroke-width="2" stroke-dasharray="4 4"/>')
    s += well_icon(cx, Y0 + HH + 64, 40, k, 90 + j)
    L.append(lab(cx - 54, Y0 + HH + 106, 108, f'K&#160;=&#160;{k}', 'lab small', 1))
for v in (0, 10, 20):
    s.append(f'<line x1="{X0-8}" y1="{yk(v)}" x2="{X0}" y2="{yk(v)}" stroke="{NAVY}" stroke-width="3"/>')
    L.append(lab(X0 - 66, yk(v) - 16, 52, str(v), 'lab small', 1, 'text-align:right; '))
s.append('<rect x="0" y="0" width="1664" height="50" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
s.append(f'<line x1="690" y1="62" x2="690" y2="610" stroke="{GRID}" stroke-width="3"/>')
L.append(lab(16, 8, 1632, '<b>Backup to slides 12 and 23:</b> how well 4 control wells pin down r', 'lab left small', 1, 'color:#1B2A41; '))
L.append(lab(0, 60, 660, '<b>Left, quick check:</b> this donor’s 4 control wells alone', 'lab left', 1))
L.append(lab(X0 + 10, Y0 + 2, 430, 'K per well<br>Shaded: counting noise alone', 'lab left small', 1, 'line-height:1.15; font-size:21px; '))
L.append(lab(0, 536, 660, 'π = 1.0&#160;%, their mean<br>r ≈ (Var ÷ nπ(1−π) − 1) ÷ (n − 1)<br>= (10.0 ÷ 9.9 − 1) ÷ 999 ≈ 0.00001<br>So <b>r ≈ 0</b>', 'lab left small', 2, 'line-height:1.22; font-size:21px; '))
# right: three simulated sets of four wells, true r = 0.01, and what each estimates
EX = [([5, 5, 11, 13], 0.001), ([1, 8, 14, 19], 0.005), ([1, 2, 7, 28], 0.016)]
L.append(lab(720, 60, 944, '<b>Right, simulation with r known</b><br>3 made-up sets of 4 control wells, all drawn with <b>r = 0.01</b>', 'lab left', 3, 'line-height:1.25; '))
for i, (ks, est) in enumerate(EX):
    top_ = 134 + i * 142; cy = top_ + 86
    for j, k in enumerate(ks):
        s += well_icon(780 + j * 112, cy, 44, k, 120 + i * 4 + j)
    s.append(f'<path d="M1250 {cy-6} H1300 V{cy-17} L1326 {cy} L1300 {cy+17} V{cy+6} H1250 Z" fill="{NAVY}"/>')
    s.append(f'<rect x="722" y="{top_}" width="508" height="134" rx="22" fill="none" stroke="#9FB4D0" stroke-width="2.5" stroke-dasharray="8 6"/>')
    L.append(lab(1340, cy - 36, 324, f'K = {", ".join(map(str, ks))}<br>→ r = <b>{est:g}</b>', 'lab left', 3 + i, 'line-height:1.15; '))
L.append(lab(720, 556, 944, 'r compares <b>replicate wells of one condition only</b><br>One r is fitted from all 8 wells<br>Four replicates give a rough r, usually too small<br>The cure: more control wells (slide 23)', 'lab left small', 6, 'line-height:1.22; font-size:21px; '))
slide('st_r', 'Backup · fitting r', 'r is measured from replicate wells, and roughly',
      svg('Left: this donor’s four control wells drawn as cartoons with their orange squares, K = 7, 8, 14 and 11, each linked up to a dot '
          'on a K axis inside a shaded band where counting noise alone would put them. Right: three simulated sets of four wells, all '
          'with a true r of 0.01, and the r each set estimates: 0.001, 0.005 and 0.016.', s, h=680), L,
      'This backup slide supports slides 12 and 23. The fit estimates the gap and r together; r sets how far chance can move the gap. r cannot be looked up; it is estimated from how much replicate wells of the same condition disagree beyond counting noise. '
      'Replicates are compared only with replicates of their own condition: the control wells with each other around the control rate, '
      'and each molecule’s wells with each other around that molecule’s rate. The model uses one r for every condition, and on a plate '
      'like this one the wells of each group carry the information about it: r is fitted from how far wells sit from their own group’s mean. A molecule well is not compared with a control well '
      'to estimate r in the fit with a gap; that comparison is the gap itself (slide 17). On the left are '
      'this donor’s four control wells, with K = 7, 8, 14 and 11. Their spread, a variance of 10.0, is about what counting noise alone '
      'gives, 9.9, so these wells show almost no extra spread and the estimate of r is essentially zero. That does not prove the '
      'wells are identical. On the right are three simulated sets of four wells, all drawn from wells that really do differ, with a '
      'true r of 0.01. The first set happens to look tight and estimates 0.001, the second 0.005, the third 0.016. Over many such sets '
      'the estimate is usually too small: half the time it is below half the truth. An r that is too small makes untreated wells look '
      'more alike than they are, so a test built on it is a little too quick to call a difference. More control wells give a better '
      'estimate.')

# ---------------------------------------------------------------- 5. estimating each group's rate
s, L = [], []
X0, W0, YB = 60, 1000, 330
def xp(p): return X0 + p / 0.04 * W0
fitX = np.array([[1, 0]] * 4 + [[1, 1]] * 4)
fit = fit_bb(C + A, [N] * 8, fitX)
ORA = math.exp(fit.coef[1]); B0, B1 = fit.coef
WX = {'C': [110, 220, 330, 440], 'A': [600, 710, 820, 930]}
PC, PA = sum(C) / 4000, sum(A) / 4000
for key, mu, col, ks, by in (('C', PC, CTRL, C, 236), ('A', PA, PUR, A, 150)):
    sdw = math.sqrt(N * mu * (1 - mu)) / N; lo, hi = mu - 2 * sdw, mu + 2 * sdw
    s.append(f'<rect x="{xp(lo):.1f}" y="{by}" width="{xp(hi)-xp(lo):.1f}" height="26" rx="4" fill="{col}" fill-opacity="0.35" stroke="{col}" stroke-width="2"/>')
    for z in (-2, -1, 1, 2):
        s.append(f'<line x1="{xp(mu + z*sdw):.1f}" y1="{by-6}" x2="{xp(mu + z*sdw):.1f}" y2="{by+32}" stroke="{NAVY}" stroke-width="{3 if abs(z) == 2 else 1.5}"/>')
    L.append(lab(xp(hi) + 10, by - 2, 130, f'±{2*sdw*100:.2f}&#160;%', 'lab left small', 3 if key == 'A' else 2))
    s.append(f'<line x1="{xp(mu):.1f}" y1="{by-14}" x2="{xp(mu):.1f}" y2="{YB}" stroke="{NAVY}" stroke-width="4"/>')
    s.append(f'<path d="M{xp(mu):.1f} {by-14} l-10 -16 h20 Z" fill="{NAVY}"/>')
    for j, k in enumerate(sorted(ks)):
        dx = xp(k / 1000); wx = WX[key][j]
        s.append(f'<path d="M{wx} 446 L{dx:.1f} {YB+26}" stroke="{col}" stroke-width="2.5" stroke-dasharray="5 4"/>')
        s.append(f'<circle cx="{dx:.1f}" cy="{YB+16}" r="11" fill="{col}" stroke="{NAVY}" stroke-width="2"/>')
        s += well_icon(wx, 500, 50, k, 200 + j + (10 if key == 'A' else 0))
s.append(f'<line x1="{X0}" y1="{YB}" x2="{X0+W0}" y2="{YB}" stroke="{NAVY}" stroke-width="3"/>')
for p in (0, 0.01, 0.02, 0.03, 0.04):
    s.append(f'<line x1="{xp(p)}" y1="{YB}" x2="{xp(p)}" y2="{YB+8}" stroke="{NAVY}" stroke-width="3"/>')
L.append(lab(0, 0, 1080, 'Each <b>●</b> is one well’s rate, K ÷ 1,000. Each bar = where a <b>single well</b> of that group lands (estimate ± 2&#160;SD of one well; ticks at 1, 2&#160;SD), not the group’s uncertainty.', 'lab left small', 1))
L.append(lab(xp(PA) + 16, 96, 420, f'<b style="color:#6B3FB0">A estimate {PA*100:.1f}&#160;%</b>', 'lab left small', 3))
L.append(lab(xp(PC) + 16, 182, 380, f'<b>control estimate {PC*100:.1f}&#160;%</b>', 'lab left small', 2))
for p in (0, 0.02, 0.03, 0.04):
    L.append(lab(xp(p) - 40, YB - 40, 80, f'{p*100:g}&#160;%', 'lab small', 2))
s.append(f'<path d="M60 554 v8 H492 v-8 M550 554 v8 H982 v-8" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
L.append(lab(60, 564, 440, 'group: 4 control wells (K 7, 8, 11, 14)', 'lab small', 2))
L.append(lab(550, 564, 440, 'group: 4 A wells (K 21, 23, 24, 28)', 'lab small', 3))
L.append(lab(1140, 0, 524, f'<b>1. Rate</b> of a well = K ÷ 1,000 (●); of a group = all its orange nanowells ÷ all its nanowells (▼): <b>{PC*100:.1f}&#160;%</b> and <b>{PA*100:.1f}&#160;%</b>.', 'lab left small', 1, 'line-height:1.25; '))
L.append(lab(1140, 104, 524, '<b>2. Bars</b> = estimate ± 2&#160;SD of one well, SD = √(nπ(1−π)) ÷ n: <b>0.32&#160;%</b> and <b>0.48&#160;%</b>. Counting noise only, as r ≈ 0 here (slide 18).', 'lab left small', 2, 'line-height:1.25; '))
s.append('<rect x="1124" y="212" width="540" height="156" rx="14" fill="#EEF3FA" stroke="#2F6BDB" stroke-width="2"/>')
L.append(lab(1144, 222, 504, '<b>r, physically:</b> a well’s shared conditions (cells loaded, medium, plate position) move all its nanowells together. If one passes, another in that well passes with π + r(1−π): 1&#160;% → 2&#160;% at r = 0.01.', 'lab left small', 3, 'line-height:1.2; color:#1B2A41; '))
s.append('<rect x="1124" y="378" width="540" height="242" rx="14" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(1144, 388, 504, '<b>Slide 18’s rough r is not solved here.</b> What helps: r is fitted from <b>all 8 wells together</b>. What remains: few replicates tend to give <b>too small an r</b>, so the bars come out too narrow and p too small. The cure is <b>more control wells</b> (slide 29).', 'lab left small', 5, 'line-height:1.2; color:#1B2A41; '))
slide('st_model', 'The statistics · 4 of 7 · estimating', 'Estimating each group’s rate from its wells',
      svg('Bottom: eight cartoon wells, four control wells with K of 7, 8, 14 and 11 and four molecule-A wells with K of 21, 24, 28 and 23. '
          'Dashed lines lead from each well up to a dot on a 0 to 4 percent rate axis at its K divided by 1,000. A navy marker shows each '
          'group’s estimate, 1.0 percent for control and 2.4 percent for A, and a shaded bar around it spans the estimate plus or minus two '
          'standard deviations of a single well, with ticks at one and two: plus or minus 0.64 percent for control, 0.97 percent for A.', s), L,
      'This slide shows what is estimated, and from what. Step one, each well: its rate is its count K divided by its thousand '
      'nanowells, the dots on the axis, each linked to its cartoon well below. Step two, each group: the estimate of the group’s rate pools '
      'its wells, all its orange nanowells over all its nanowells: 41 of 4,000 for the four control wells, 1.0 %, and 96 of 4,000 for '
      'the four molecule-A wells, 2.4 %. Those are the navy markers. Step three, how far one well can stray from its group’s rate by '
      'chance. Each bar is the group’s estimate plus or minus two standard deviations of a single well, which holds about 95 % of single '
      'wells; the ticks mark one and two standard deviations. The standard deviation comes from the formula of slide 14, divided by the '
      'thousand nanowells to turn a count into a rate: the square root of n times the rate times one minus the rate, over n. For the '
      'control group that is the square root of 1,000 × 0.0103 × 0.9897, about 3.2 nanowells, or 0.32 percentage points; for molecule A '
      '0.48. That is counting noise alone, because for this donor r, how much wells differ, comes out close to zero (slide 18). If r were '
      'larger, the standard deviation would grow by the square root of 1 + (n − 1) r, and the bars with it. Note that the bars are not '
      'the spread of the four dots measured directly; they are what the model expects from the group’s rate, and the four dots should '
      'fall inside them, which they do. Step four, the question the test answers: '
      'are the two estimates further apart than wells stray by chance? Here every A well sits above every control well and the bars '
      'barely overlap, which is why the next slides find a strong effect. Molecules B and C are compared with the same four control '
      'wells in exactly the same way, one molecule at a time. Finally, the box answers the problem left by the previous slide, that '
      'four replicate wells give only a rough r. This slide does not solve it. Two things in the method help. First, r is not '
      'estimated from the four control wells alone: the model fits it from all eight wells of the comparison together, the control '
      'wells around the control rate and the molecule wells around the molecule rate, with one r shared by both. Second, wells on other '
      'plates join the same fit, with a term for the plate; their counts are never added together. What remains is that with few replicates r tends to come out too '
      'small, which makes these bars too narrow and the p-value too small. The cure is more control wells: the last slide shows how '
      'many, and that a fifth control well per donor lets the plate check itself for false positives. The blue box gives r a physical '
      'meaning. Nanowells in one well share that well’s conditions: how many cells were loaded, the batch of medium, the well’s position '
      'on the plate, its focus and timing in imaging. Whatever pushes one of its nanowells up pushes the others up too. r measures how '
      'strongly: it is the correlation between two nanowells of the same well. Concretely, if one nanowell in a well passes the cutoff, '
      'another nanowell in the same well passes with probability π + r(1 − π) instead of π: at π = 1 % and r = 0.01, about 2 % instead of '
      '1 %. Equivalently, the wells’ true rates scatter around π with a standard deviation of √(r π (1 − π)), about 1 percentage point at '
      'r = 0.01. With r = 0 the nanowells behave as independent coin tosses, which is what this donor’s control wells look like.')

# ---------------------------------------------------------------- 6. now that we have r: is the gap more than chance?
from scipy import optimize as _opt
from scipy.special import logit as _logit
from review.features.nanowell.rare_exceedance_compute import _bb_loglik
s, L = [], []
OC, OA = sum(C) / (4000 - sum(C)), sum(A) / (4000 - sum(A))
_k = np.array(C + A, float); _n = np.full(8, float(N)); _X = np.column_stack([np.ones(8), [0] * 4 + [1] * 4])
def at_r(rr):
    th = _logit(rr)
    full = _opt.minimize(lambda b: -_bb_loglik(_k, _n, _X @ b, th), [-4.5, 0.8])
    null = _opt.minimize(lambda b: -_bb_loglik(_k, _n, np.full(8, b[0]), th), [-4.2])
    z = math.sqrt(max(2 * (null.fun - full.fun), 0)) * np.sign(full.x[1])
    p_ = 1 - stats.norm.cdf(z)
    se_ = abs(full.x[1]) / max(z, 1e-9)            # the SE the likelihood test implies, so the 90 % bar clears 0 exactly when p < 0.05
    return full.x[1], se_, p_
ROWS = [(1e-6, 'r ≈ 0 (this donor)'), (0.002, 'r = 0.002'), (0.005, 'r = 0.005'), (0.01, 'r = 0.01')]
RES = [at_r(rr) for rr, _ in ROWS]
def fp(p): return '&lt;&#160;0.00001' if p < 1e-5 else (f'{p:.4f}' if p < 0.01 else f'{p:.2f}')
# left: the gap, fitted to each well's own K (counts are never added together)
fitB_ = fit_bb(C + B, [N] * 8, fitX); B1B_ = fitB_.coef[1]
GPC, GPA = 1 / (1 + math.exp(-B0)), 1 / (1 + math.exp(-(B0 + B1)))
def gx(p): return 40 + p / 0.035 * 720
L.append(lab(0, 0, 800, '<b>1. The gap:</b> how much higher is A’s rate?<br>Fitted to each well’s own K, never to a total.', 'lab left', 1, 'line-height:1.2; '))
for j, k in enumerate(C):
    s += well_icon(48 + j * 96, 118, 44, k, 230 + j)
for j, k in enumerate(A):
    s += well_icon(458 + j * 96, 118, 44, k, 240 + j)
L.append(lab(0, 166, 396, '<b>control</b> K = 7, 8, 14, 11', 'lab small', 2))
L.append(lab(412, 166, 388, '<b style="color:#6B3FB0">A</b> K = 21, 24, 28, 23', 'lab small', 2))
# cartoon: each well's rate K/1,000 on one axis, the two fitted means, and the gap between them
s.append(f'<line x1="40" y1="300" x2="780" y2="300" stroke="{NAVY}" stroke-width="3"/>')
for pv in (0, 0.01, 0.02, 0.03):
    s.append(f'<line x1="{gx(pv):.1f}" y1="300" x2="{gx(pv):.1f}" y2="308" stroke="{NAVY}" stroke-width="2.5"/>')
for mu in (GPC, GPA):
    s.append(f'<line x1="{gx(mu):.1f}" y1="252" x2="{gx(mu):.1f}" y2="300" stroke="{NAVY}" stroke-width="4"/>')
s.append(f'<path d="M{gx(GPC):.1f} 254 V244 H{gx(GPA):.1f} V254" stroke="{PUR}" stroke-width="3.5" fill="none"/>')
for k in C:
    s.append(f'<circle cx="{gx(k / 1000):.1f}" cy="300" r="9" fill="{CTRL}" stroke="{NAVY}" stroke-width="2"/>')
for k in A:
    s.append(f'<circle cx="{gx(k / 1000):.1f}" cy="300" r="9" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
L.append(lab(int(gx(GPC)) - 120, 204, int(gx(GPA) - gx(GPC)) + 240, '<b style="color:#6B3FB0">the gap δ = 0.86 (log odds ratio)</b>', 'lab small', 3))
L.append(lab(int(gx(GPC)) - 140, 312, 280, f'control mean {GPC*100:.1f}&#160;%', 'lab small', 3))
L.append(lab(int(gx(GPA)) - 110, 312, 220, f'A mean {GPA*100:.1f}&#160;%', 'lab small', 3))
L.append(lab(0, 312, 70, '0&#160;%', 'lab small', 3))
L.append(lab(0, 340, 800, 'Axis: each well’s K ÷ 1,000, the % of its nanowells past the cutoff.', 'lab left small', 3))
s.append(f'<line x1="850" y1="10" x2="850" y2="600" stroke="{GRID}" stroke-width="3"/>')
L.append(lab(890, 0, 774, '<b>2. The model behind the fit</b>', 'lab left', 4))
L.append(lab(890, 60, 774, '<b>Model, per well w:</b> log-odds = α + δ × A<sub>w</sub><br>A<sub>w</sub> = 1 for an A well, 0 for a control<br>odds = rate ÷ (1 − rate)', 'lab left small', 4, 'line-height:1.3; '))
# cartoon: each group's well -> its point on the log-odds axis
def lx(v): return 1130 + (v + 5.0) / 2.0 * 500
LY = 308
s += well_icon(930, 210, 34, 4, 611)
s += well_icon(930, 290, 34, 9, 612)
L.append(lab(976, 192, 150, 'control<br>A<sub>w</sub> = 0', 'lab left small', 4, 'line-height:1.1; '))
L.append(lab(976, 272, 200, 'Molecule A<br>A<sub>w</sub> = 1', 'lab left small', 4, 'line-height:1.1; color:#6B3FB0; '))
s.append(f'<line x1="{lx(-5)}" y1="{LY}" x2="{lx(-3)}" y2="{LY}" stroke="{NAVY}" stroke-width="3"/>')
for v in (-5, -4, -3):
    s.append(f'<line x1="{lx(v)}" y1="{LY}" x2="{lx(v)}" y2="{LY+8}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(int(lx(-5)) - 30, LY + 10, 60, '−5', 'lab small', 4))
L.append(lab(int(lx(-3)) - 30, LY + 10, 60, '−3', 'lab small', 4))
for v, col, yy in ((B0, NAVY, 210), (B0 + B1, PUR, 290)):
    s.append(f'<path d="M1080 {yy} C 1100 {yy}, {lx(v):.1f} {yy}, {lx(v):.1f} {LY-14}" stroke="{col}" stroke-width="2.5" fill="none" stroke-dasharray="6 4"/>')
    s.append(f'<circle cx="{lx(v):.1f}" cy="{LY}" r="10" fill="{col}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<path d="M{lx(B0):.1f} {LY+36} v8 H{lx(B0+B1):.1f} v-8" stroke="{PUR}" stroke-width="3" fill="none"/>')
L.append(lab(int(lx(B0)) - 10, LY + 46, 300, '<b style="color:#6B3FB0">δ = 0.86</b>', 'lab left small', 4))
L.append(lab(int(lx(B0)) - 30, 174, 280, '<b>α</b> = −4.57 = 1.0&#160;%', 'lab left small', 4))
L.append(lab(int(lx(B0 + B1)) + 14, 236, 190, '<b>α + δ</b><br>−3.71 = 2.4&#160;%', 'lab left small', 4, 'line-height:1.1; color:#6B3FB0; '))
L.append(lab(1500, LY + 46, 164, 'log-odds', 'lab left small', 4, 'color:#4A5568; '))
L.append(lab(890, 404, 774, f'<b>α = {B0:.2f}</b>'.replace('-', '−') + f': the log-odds of {GPC*100:.1f}&#160;%<br><b>δ = {B1:.2f}</b>: odds ratio <b>{math.exp(B1):.1f}</b> (≈ {GPA*100:.1f} ÷ {GPC*100:.1f})', 'lab left small', 5, 'line-height:1.3; '))
L.append(lab(890, 474, 774, f'B (9, 10, 64, 13): odds ratio <b>{math.exp(B1B_):.1f}</b><br>Adding up B’s counts instead would give 2.4', 'lab left small', 5, 'line-height:1.3; '))
s.append('<rect x="0" y="612" width="1664" height="78" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(890, 544, 774, '<b>Next:</b> step 4 fits the 8 wells as one group.<br>Step 5 compares the two fits: is 0.86 more than chance? (slide 18)', 'lab left small', 6, 'line-height:1.3; '))
L.append(lab(16, 618, 1632, '<b>The test asks one question: is the gap δ different from 0?</b><br>Step 3 gives δ. Steps 4 and 5 (slide 18) turn it into p.', 'lab left small', 11, 'line-height:1.25; color:#1B2A41; '))
slide('st_gap', 'The statistics · step 3', 'Fit as two groups: the gap',
      svg('Left: the four control wells and four molecule-A wells as cartoons with their K, 7, 8, 14, 11 and 21, 24, 28, 23; below them each well as a dot at K ÷ 1,000 on a 0 to 3.5 percent axis, the fitted control mean 1.0 percent and A mean 2.4 percent, and a bracket between them marked the gap beta one; the model, '
          'log-odds of each well = beta zero + beta one times A_w (1 for an A well, 0 for a control well), fitted to the eight counts: beta one = 0.86, odds ratio 2.4; molecule B, fitted the same way, 0.44 and 1.5. Right: the model and the fitted values, and what steps 4 and 5 do next.', s, h=700), L,
      'This slide is step 3 of the overview: the fit gives the gap, and r, how much replicate wells differ (slide 10), sets how far chance alone can move it. For this donor r comes out close to zero: the four control wells, with K = 7, 8, 14 and 11, vary no more than counting noise alone would make them. First the gap. It is not '
      'one well against one well, and it is not one total against another: all four control wells and all four molecule-A wells are drawn, each with its own count K. The model writes the log-odds of each well as α + δ × treated, with treated 0 for a control well and 1 for an A well, and the fit chooses α, δ and r that make the eight counts, each out of its own thousand nanowells, most likely together. The log-odds scale turns the effect into one number that is added, and keeps every implied rate between 0 and 100 %. For molecule A the fit gives δ = 0.86, an odds ratio of 2.4: A’s odds of a nanowell past the cutoff are 2.4 times the control’s. Molecule B is fitted the same way to its own four counts, 9, 10, 64 and 13: one wild well, so its wells disagree, and the fit gives δ = 0.44, an odds ratio of 1.5. Had the counts been added up, A and B would both have 96 of 4,000 and the same odds ratio of 2.4; fitting each well keeps the difference. '
      'Whether a gap of 0.86 is more than chance is decided in steps 4 and 5 on the next slide: the same eight wells are fitted as one group, and the two fits are compared. r, how much replicate wells differ, sets how far chance alone can move the gap: the more the wells differ, the bigger a gap must be.', steps={3})

# ---------------------------------------------------------------- 7. Benjamini-Hochberg (written as a table slide)
import subprocess
rows = [('A', '21, 24, 28, 23', 0.00008), ('C', '13, 15, 15, 14', 0.041), ('B', '9, 10, 64, 13', 0.17)]
m_ = len(rows)
q = []
prev = 1.0
for i in range(m_ - 1, -1, -1):
    prev = min(prev, rows[i][2] * m_ / (i + 1)); q.insert(0, prev)
def fmt(p): return f'{p:.5f}'.rstrip('0') if p < 0.001 else f'{p:.3f}'.rstrip('0').rstrip('.')
def wells_html(ks, col='#2F6BDB'):
    import sys as _s; _s.path.insert(0, 'inputs'); from wellsvg import icon as _icon
    return ''.join(f'<span style="display:inline-block; width:64px; margin-right:8px; text-align:center; vertical-align:middle">{_icon(int(k), 800 + 13 * j + int(k), 56)}'
                   f'<span style="display:block; font-size:24px; line-height:1; color:#1B2A41">{k}</span></span>' for j, k in enumerate(ks.split(', ')))
W_ = [566, 180, 130, 290, 210, 288]
AL_ = ['left', 'right', 'right', 'right', 'right', 'center']
def cell(i, x, extra=''):
    return f'<div style="width:{W_[i]}px; box-sizing:border-box; padding:0 8px; text-align:{AL_[i]}; {extra}">{x}</div>'
def row(cells, style=''):
    return f'<div style="display:flex; flex-direction:row; align-items:center; width:1664px; {style}">' + ''.join(cells) + '</div>'
def wellcell(nm, ks):
    return f'<b style="display:inline-block; width:64px; vertical-align:middle">{nm}</b>{wells_html(ks)}'
hdr = row([cell(i, h) for i, h in enumerate(['Its 4 wells (number = K)', 'p', 'rank', '0.05 × rank ÷ 3', 'q', 'detected?'])], 'font-weight:700; border-bottom:2px solid #C9D3E0; padding-bottom:6px')
ctl = row([cell(0, wellcell('Ctrl', '7, 8, 14, 11')),
           f'<div style="width:1098px; padding:0 8px">control wells: every molecule is compared with these</div>'], 'color:#6B7686')
trs = ''.join(row([cell(0, wellcell(nm, ks)), cell(1, fmt(p)), cell(2, str(i + 1)), cell(3, f'{0.05*(i+1)/m_:.3f}'), cell(4, fmt(q[i])),
                   cell(5, '<b>detected</b>' if q[i] < 0.05 else 'not detected')]) for i, (nm, ks, p) in enumerate(rows))
html = ('<section id="st_bh" data-transition="fade">\n<div class="head">\n<p class="eyebrow">The statistics · many molecules</p>\n'
        '<h2>Testing several molecules: correct for multiple tests</h2>\n</div>\n'
        '<p class="lead2">Test three inactive molecules at p &lt; 0.05 and one looks significant 14&#160;% of the time. Benjamini–Hochberg holds false calls to 5&#160;% of calls.</p>\n'
        '<div style="display:flex; flex-direction:column; gap:6px; width:1664px; font-size:28px; line-height:1.2; color:#1B2A41">' + hdr + ctl + trs + '</div>\n'
        '<p class="tnote">One donor’s plate; <b>C</b> is a third molecule, all four wells a little above the controls. Sort the p-values and keep every molecule up to the last p below 0.05 × rank ÷ 3; q is the corrected p. '
        '<b>C</b> would pass alone (p = 0.041) but not its threshold at rank 2 (0.033): q = 0.061, not detected.</p>\n'
        '<aside>With several molecules on a plate, each tested at p below 0.05, the chance of at least one false call grows with the '
        'number of molecules: about 14 % for three that do nothing. The Benjamini–Hochberg procedure corrects for this. Sort the '
        'p-values from smallest to largest, compare the one at rank k with 0.05 times k divided by the number of molecules, and call '
        'significant every molecule up to the largest rank that passes. Each molecule gets a corrected value, q, which is never smaller '
        'than its p, and the molecule is called significant when q is below 0.05. Here molecule A passes easily. Molecule C, whose four '
        'wells are all a little above the controls, has p = 0.041, which would count on its own, but it is compared with 0.033 at rank '
        'two and fails: its q is 0.061. Molecule B is not close. The procedure keeps the expected share of false calls among the molecules '
        'called significant at 5 %. Counts and p-values are illustrative.</aside>\n</section>\n')
open('project/slides/st_bh.html', 'w').write(html); print('st_bh', len(html), q)
# st_bh rebuilt as a stage (svg + pinned labels): the viewer turns HTML tables into document tables
import re as _re2
_aside = _re2.search(r'<aside>(.*?)</aside>', html, _re2.S).group(1)
_exc_aside = _re2.search(r'<aside>(.*?)</aside>', open('inputs/exctest_before_merge.html').read(), _re2.S).group(1).replace('Card 1: the wells. ', '').replace('Card 2 walks through the test one step per line', 'The columns walk through the test one step each')
def _icon_shapes(k, seed, cx, cy, px):
    """The well cartoon (blue ring, fold-change squares, K orange) at centre (cx, cy), px wide; squares merged into one path per colour."""
    rnd = random.Random(seed); r0, pitch, sw = 58, 10.0, 4; sq = pitch * 0.79; sc = px / 120
    n = int(r0 // pitch) + 1
    cells = [(i * pitch, j * pitch) for i in range(-n, n + 1) for j in range(-n, n + 1) if math.hypot(i * pitch, j * pitch) + pitch * 0.72 <= r0 - sw]
    hot = set(rnd.sample(range(len(cells)), k)); paths = {}
    for idx, (x, y) in enumerate(cells):
        col = OR if idx in hot else rnd.choices(FCCOL, FCW)[0]
        x0, y0, w = cx + (x - sq / 2) * sc, cy + (y - sq / 2) * sc, sq * sc
        paths.setdefault(col, []).append(f'M{x0:.1f} {y0:.1f}h{w:.1f}v{w:.1f}h-{w:.1f}z')
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r0*sc:.1f}" fill="#F7F5F0" stroke="#2F6BDB" stroke-width="{max(2.5, sw*sc):.1f}"/>']
    out += [f'<path d="{"".join(v)}" fill="{c}"/>' for c, v in paths.items()]
    return out
s, L = [], []
PITCH, HY = 80, 112
RY = [HY + PITCH * (i + 1) for i in range(4)]
IX = [124, 204, 284, 364]
COLS = [(440, 170, 'center'), (614, 170, 'center'), (788, 130, 'center'), (922, 80, 'center'), (1006, 130, 'center'), (1190, 110, 'center'), (1310, 130, 'center'), (1450, 150, 'center')]
L.append(lab(0, 0, 1664, 'Steps 3–5 test each molecule against the same 4 control wells; step 6 corrects for testing several molecules at once.', 'lab left', 1, 'line-height:1.3; '))
s.append(f'<path d="M442 {HY-40} v-8 H1134 v8 M1190 {HY-40} v-8 H1598 v8" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
L.append(lab(442, HY - 84, 692, '<b>Test of each molecule</b> (slides 11–14)', 'lab small', 2, 'color:#1B2A41; line-height:32px; '))
L.append(lab(1190, HY - 84, 410, '<b>Correction across the 3 molecules</b>', 'lab small', 2, 'color:#1B2A41; line-height:32px; '))
s.append(f'<rect x="0" y="{HY + 32}" width="1664" height="2" fill="{GRID}"/>')
allrows = [('Ctrl', '7, 8, 14, 11', None)] + rows
for i, (nm, ks, p) in enumerate(allrows):
    for j, k in enumerate(int(x) for x in ks.split(', ')):
        s += _icon_shapes(k, 800 + 13 * j + k, IX[j], RY[i] - 10, 48)
L.append(lab(0, HY - 18, 460, '<b>4 wells each</b> (K below; not to scale)', 'lab left small', 2, 'line-height:36px; '))
L.append(lab(0, HY + PITCH - PITCH // 2, 80, '<br>'.join(f'<b>{nm}</b>' for nm, _, _ in allrows), 'lab left', 2, f'line-height:{PITCH}px; '))
for j in range(4):
    ks_j = [ks.split(', ')[j] for _, ks, _ in allrows]
    L.append(lab(IX[j] - 40, RY[0] + 30 - PITCH // 2, 80, '<br>'.join(ks_j), 'lab small', 3, f'line-height:{PITCH}px; color:#1B2A41; '))
hdrs = ['3. with δ', '4. without', '5. D', '5. z', '5. p', '6. rank', '6. q', 'q &lt; 0.05?']
vals = [['−20.1', '−18.8', '−29.8'], ['−27.2', '−20.3', '−30.2'], ['14.2', '2.9', '0.8'], ['3.76', '1.71', '0.91'], [fmt(p) for _, _, p in rows], [str(i + 1) for i in range(3)], [fmt(x) for x in q],
        ['<b>yes</b>' if x < 0.05 else 'no' for x in q]]
H2 = {0: '<b>3. fit with gap</b><br>log-likelihood', 1: '<b>4. fit, no gap</b><br>log-likelihood', 2: '<b>5. D</b><br>2 × (3 − 4)', 3: '<b>z</b><br>√D', 4: '<b>p</b><br>one-sided'}
for ci, ((x, w, al), h, v) in enumerate(zip(COLS, hdrs, vals)):
    if ci in H2:
        L.append(lab(x, HY - 32, w, H2[ci], 'lab small', 4, 'line-height:1.15; color:#1B2A41; white-space:nowrap; '))
        L.append(lab(x, RY[1] - PITCH // 2, w, '<br>'.join(v), 'lab small', 4, f'line-height:{PITCH}px; text-align:{al}; color:#1B2A41; white-space:nowrap; '))
    else:
        L.append(lab(x, HY - PITCH // 2, w, '<br>'.join([f'<b>{h}</b>', '&#160;'] + v), 'lab small', 4, f'line-height:{PITCH}px; text-align:{al}; color:#1B2A41; white-space:nowrap; '))
L.append(lab(0, 512, 1664, '<b>Log-likelihood</b> (3, 4): how probable a fit makes the 8 counts, on a log scale; nearer 0 = better. <b>D, the likelihood-ratio statistic</b> (5): how much better the fit with a gap does, 2 × (3 − 4); z = √D; p = the chance of a z this large with no gap. '
             'B’s wild well raises its r to 0.008, so its D is small. <b>6:</b> q = p × 3 ÷ rank (rank 1 = smallest p), kept from falling as rank rises: C, 0.042 × 3 ÷ 2 = 0.064.', 'lab left small', 5, 'line-height:1.25; '))
L.append(lab(440, RY[0] - 18, 1160, 'control wells: every molecule is compared with these', 'lab left small', 4, 'color:#6B7686; line-height:36px; '))
slide('st_bh_old', 'The statistics · steps 3–6', 'The test, step by step',
      svg('Four rows of four well cartoons, K of their nanowells orange: control 7, 8, 14, 11; A 21, 24, 28, 23; C 13, 15, 15, 14; '
          'B 9, 10, 64, 13. Beside each molecule row the test step by step: log-likelihood with and without the gap, D, z, p, then rank, threshold 0.05 times rank over 3, corrected q and call.', s), L, _exc_aside + ' ' + _aside, steps={3, 4, 5, 6})

# ---------------------------------------------------------------- step 3 in detail: how the fit gets beta0, beta1 and r (maximum likelihood)
from scipy import optimize as _opt2
s, L = [], []
_km = np.array(C + A, float); _nm = np.full(8, float(N)); _tr = np.array([0] * 4 + [1] * 4)
_m0, _m1 = 1 / (1 + math.exp(-B0)), 1 / (1 + math.exp(-(B0 + B1)))
_lp = [stats.binom.logpmf(k, N, _m0 if t == 0 else _m1) for k, t in zip(C + A, _tr)]
def _prof(b):
    g = lambda v: -_bb_loglik(_km, _nm, v[0] + b * _tr, v[1])
    best = min((_opt2.minimize(g, [b0_, th_], method='L-BFGS-B', bounds=[(-7, -2), (-14, -1)]) for b0_ in (B0 - 0.4, B0, B0 + 0.3) for th_ in (-12, -7, -4)), key=lambda r: r.fun)
    return min(-best.fun, _FIT_TOP)
_FIT_TOP = fit.loglik
_bs = np.linspace(-0.1, 1.6, 35); _pl = [_prof(b) for b in _bs]
PX = [0, 562, 1124]; PW_ = 540
for px in PX:
    s.append(f'<rect x="{px}" y="0" width="{PW_}" height="298" rx="18" fill="#ECF1F8"/>')
for px in PX[:2]:
    s.append(f'<path d="M{px+PW_+4} 140 h10 v-9 l14 15 l-14 15 v-9 h-10 Z" fill="{NAVY}"/>')
# panel 1: chance of each K under a guess (the best one)
def k1x(k): return 40 + k / 40 * 470
def k1y(pv): return 250 - pv / 0.13 * 118
for m_, col in ((_m0, NAVY), (_m1, PUR)):
    s.append('<path d="M' + ' L'.join(f'{k1x(k):.1f} {k1y(stats.binom.pmf(k, N, m_)):.1f}' for k in range(0, 41)) + f'" stroke="{col}" stroke-width="3" fill="none"/>')
for k in C:
    s.append(f'<circle cx="{k1x(k):.1f}" cy="{k1y(stats.binom.pmf(k, N, _m0)):.1f}" r="8" fill="{CTRL}" stroke="{NAVY}" stroke-width="2"/>')
for k in A:
    s.append(f'<circle cx="{k1x(k):.1f}" cy="{k1y(stats.binom.pmf(k, N, _m1)):.1f}" r="8" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<line x1="40" y1="250" x2="510" y2="250" stroke="{NAVY}" stroke-width="2.5"/>')
for k in (0, 20, 40):
    s.append(f'<line x1="{k1x(k):.1f}" y1="250" x2="{k1x(k):.1f}" y2="257" stroke="{NAVY}" stroke-width="2.5"/>')
_x0, _x1 = k1x(N * _m0), k1x(N * _m1)
for _xx, _cc in ((_x0, NAVY), (_x1, PUR)):
    s.append(f'<line x1="{_xx:.1f}" y1="250" x2="{_xx:.1f}" y2="108" stroke="{_cc}" stroke-width="2.5" stroke-dasharray="6 4"/>')
s.append(f'<path d="M{_x0:.1f} 116 v-8 H{_x1:.1f} v8" stroke="{PUR}" stroke-width="3" fill="none"/>')
L.append(lab(18, 92, int(_x0) - 26, '<b>α</b> 1.0&#160;%', 'lab small', 1, 'text-align:right; color:#1B2A41; '))
L.append(lab(int(_x1) + 8, 92, 200, '<b>α + δ</b> 2.4&#160;%', 'lab left small', 1, 'color:#6B3FB0; '))
L.append(lab(int(_x0) + 6, 116, int(_x1 - _x0) - 12, '<b>δ</b>', 'lab small', 1, 'color:#6B3FB0; '))
L.append(lab(18, 10, 504, '<b>1. Guess the dials</b>', 'lab left', 1))
L.append(lab(18, 52, 504, 'Guess: control 1.0&#160;%, A 2.4&#160;%, r ≈ 0', 'lab left small', 1, 'color:#4A5568; '))
L.append(lab(24, 262, 60, '0', 'lab small', 1)); L.append(lab(int(k1x(20)) - 30, 262, 60, '20', 'lab small', 1)); L.append(lab(int(k1x(40)) - 40, 262, 60, '40', 'lab small', 1))
# panel 2: score = sum of log chances
P2 = PX[1]
mx = max(-v for v in _lp)
for i, v in enumerate(_lp):
    y = 96 + i * 24; w = -v / 3.2 * 300
    s.append(f'<rect x="{P2+150}" y="{y}" width="{w:.1f}" height="18" rx="4" fill="{CTRL if i < 4 else PUR}"/>')
L.append(lab(P2 + 18, 10, 504, '<b>2. Score the guess</b>', 'lab left', 2))
L.append(lab(P2 + 18, 52, 504, 'ln P(K<sub>w</sub> | π, r) for each well, added up', 'lab left small', 2, 'color:#4A5568; '))
L.append(lab(P2 + 18, 90, 130, '<br>'.join([f'{k}' for k in C + A]), 'lab small', 2, 'line-height:24px; text-align:right; color:#1B2A41; font-size:22px; '))
# panel 3: try every beta1 (beta0 and r re-picked for each), keep the best
P3 = PX[2]
def p3x(b): return P3 + 50 + (b + 0.1) / 1.7 * 450
def p3y(v): return 112 + (-20.0 - v) / 8.0 * 130
s.append('<path d="M' + ' L'.join(f'{p3x(b):.1f} {p3y(v):.1f}' for b, v in zip(_bs, _pl)) + f'" stroke="{NAVY}" stroke-width="3" fill="none"/>')
s.append(f'<line x1="{P3+50}" y1="250" x2="{P3+510}" y2="250" stroke="{NAVY}" stroke-width="2.5"/>')
s.append(f'<circle cx="{p3x(B1):.1f}" cy="{p3y(_prof(B1)):.1f}" r="10" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<circle cx="{p3x(0):.1f}" cy="{p3y(_prof(0)):.1f}" r="9" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2.5"/>')
s.append(f'<line x1="{p3x(B1):.1f}" y1="{p3y(_prof(B1))+10:.1f}" x2="{p3x(B1):.1f}" y2="250" stroke="{PUR}" stroke-width="2" stroke-dasharray="5 4"/>')
for b in (0, 1):
    s.append(f'<line x1="{p3x(b):.1f}" y1="250" x2="{p3x(b):.1f}" y2="257" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(P3 + 18, 10, 504, '<b>3. Try every guess, keep the best</b>', 'lab left', 3))
L.append(lab(P3 + 18, 52, 504, 'log L for each gap δ', 'lab left small', 3, 'color:#4A5568; '))
L.append(lab(int(p3x(B1)) + 14, int(p3y(_prof(B1))) - 40, 200, f'<b style="color:#6B3FB0">best: δ = {B1:.2f}</b>', 'lab left small', 3))
L.append(lab(P3 + 18, 140, 220, f'○ δ = 0: {_prof(0):.1f}'.replace('-', '−'), 'lab left small', 3))
L.append(lab(int(p3x(0)) - 30, 262, 60, '0', 'lab small', 3)); L.append(lab(int(p3x(1)) - 30, 262, 60, '1', 'lab small', 3))

# the model and its fit, written out under the panels it belongs to
EY = 308
def _tm(x, c): return f'<span style="color:{c}; font-weight:700">{x}</span>'
cK, cM, cR, cB = '#A9501A', '#1B2A41', '#8E5BD0', '#2F6BDB'
for i, px in enumerate(PX):
    s.append(f'<rect x="{px}" y="{EY}" width="{PW_}" height="{632-EY}" rx="18" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
    s.append(f'<path d="M{px+PW_//2-14} {EY-2} l14 -16 l14 16 Z" fill="#2F6BDB"/>')
cT = '#2E7D57'
_K, _mu, _r = _tm("K<sub>w</sub>", cK), _tm("π", cM), _tm("r", cR)
_b0, _b1, _th = _tm("α", cB), _tm("δ", cB), _tm("π<sub>w</sub>", cT)
L.append(lab(18, EY + 8, 504, f'<span style="font-size:21px">{_th} ~ Beta(mean {_mu}, {_r})<br>{_K} ~ Binomial(n, {_th})<br>'
          f'ln[{_mu} ÷ (1−{_mu})] = {_b0} + {_b1}·A<sub>w</sub><br>Var({_K}) = n{_mu}(1−{_mu})[1 + (n−1){_r}]</span><br>'
          f'<span style="display:block; height:8px"></span>{_th}: well w’s own rate<br>{_K}: count past the cutoff, of n = 1,000<br>A<sub>w</sub>: 1 for an A well, 0 for control<br>'
          f'<b>{_mu} = E[{_K}] ÷ n: the rate</b><br>{_r}: how much wells differ<br>{_b0}: log-odds of control<br>{_b1}: log-odds of A minus control', 'lab left', 5, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
L.append(lab(PX[1] + 18, EY + 12, 504, f'log L = Σ<sub>w</sub> ln P({_K} | {_mu}, {_r})', 'lab left', 6, 'font-size:30px; line-height:1.4; color:#1B2A41; '))
L.append(lab(PX[1] + 18, EY + 62, 504, f'P({_K} | {_mu}, {_r}) = C(n,{_K})·B({_K}+a, n−{_K}+b) ÷ B(a,b)<br>'
          f'a = {_mu}(1−{_r})/{_r}<br>b = (1−{_mu})(1−{_r})/{_r}<br>B: the beta function<br>'
          f'{_mu} = 1 ÷ (1 + e<sup>−({_b0} + {_b1}A<sub>w</sub>)</sup>), the logistic<br>'
          f'If {_r} → 0: ln P = {_K} ln {_mu} + (n − {_K}) ln(1 − {_mu}) + const<br>'
          f'P: the dot height in panel 1<br>Σ<sub>w</sub>: adds the bars of panel 2<br>Here: log L = <b>{sum(_lp):.1f}</b> (nearer 0 = more probable)'.replace('<b>-', '<b>−'),
          'lab left small', 6, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
L.append(lab(PX[2] + 18, EY + 12, 504, f'best {_b0}, {_b1}, {_r} = arg max log L', 'lab left', 7, 'font-size:30px; line-height:1.4; color:#1B2A41; '))
L.append(lab(PX[2] + 18, EY + 62, 504, f'arg max: the values giving the highest log L<br>If {_r} → 0, each group’s best π is its mean K ÷ n<br>'
          f'Control: (7+9+14+11)/4 ÷ 1,000 = <b>1.0&#160;%</b><br>→ {_b0} = −4.57<br>'
          f'A: (21+24+28+23)/4 ÷ 1,000 = <b>2.4&#160;%</b><br>→ {_b0} + {_b1} = −3.71, so {_b1} = 0.86<br>'
          f'{_r} ≈ 0: in each group, Var({_tm("K", cK)}) ≤ counting noise<br>Control: 8.9 vs 10.1<br>A: 8.7 vs 23<br>'
          'At δ = 0, the best log L is −27.2 (step 4)',
          'lab left small', 7, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
s.append('<rect x="0" y="644" width="1664" height="100" rx="14" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(16, 650, 900, '<b>What β is fitted to:</b> K → share K ÷ n → odds = share ÷ (1 − share) → log-odds = ln(odds)<br>'
          '<b>Control</b>: 10.25 of 1,000 → 1.025&#160;% → odds 1 to 97 → <b>−4.57 = α</b><br>'
          '<b>Molecule A</b>: 24 of 1,000 → 2.4&#160;% → odds 1 to 41 → <b>−3.71 = α + δ</b>', 'lab left small', 8, 'line-height:1.3; color:#1B2A41; font-size:20px; '))
L.append(lab(930, 650, 718, 'δ = −3.71 − (−4.60) = 0.89<br>δ is the log of the odds ratio: e<sup>0.86</sup> = 2.4× the odds<br>Log-odds span −∞ to +∞, so any β gives a share between 0 and 1', 'lab left small', 8, 'line-height:1.3; color:#1B2A41; font-size:20px; '))
slide('st_ml', 'The statistics · steps 3–4, in detail', 'How the fit finds α, δ and r',
      svg('Three panels. 1: two curves over K from 0 to 40, the chance of each count for one well at the control mean, 1.0 percent, and at A’s mean, 2.4 percent; '
          'the eight observed counts sit on the curves as dots. 2: the log of each well’s chance as eight bars, which add up to the log-likelihood, −20.1. '
          '3: the log-likelihood as the gap δ is varied, a hill with its peak at δ = 0.86; at δ = 0 it is −27.2.', s, h=748), L,
      'This slide opens step 3: how the counts K become α, δ and r. The model has three dials, the control mean, written as log-odds α, the gap δ, and r. '
      'Panel 1: pick values for the dials, here the best ones, a control mean of 1.0 %, an A mean of 2.4 % and r close to zero. For a well at a given mean, the '
      'beta-binomial of slide 10 gives the chance of every possible count; with r near zero it is the binomial, and a larger r would make these curves wider. '
      'Each observed well sits on its group’s curve, and its height is how probable that count is under this guess. Panel 2: the guess is scored by multiplying '
      'the eight chances, or equivalently adding their logarithms; the sum is the log-likelihood, here −20.1. A guess that puts the means far from the data gives '
      'low chances and a more negative score. Panel 3: the computer repeats this for every possible gap δ, each time re-tuning the control mean and r to their best, '
      'and keeps the highest score. The peak is at δ = 0.86, with α = −4.57, a control mean of 1.0 %, and r near zero. This search is maximum likelihood. '
      'The left end of the curve, δ = 0, is exactly the fit of step 4, −27.2, and step 5 compares the peak with it. Written out, the beta-binomial chance of a count K is C(n, K) · B(K + a, n − K + b) ÷ B(a, b), with a = π(1 − r)/r and b = (1 − π)(1 − r)/r, B the beta function; as r goes to 0 it becomes the binomial.', steps={3, 4})

# ---------------------------------------------------------------- step 3: what each dial does to K (beta -> average, r -> scatter)
s, L = [], []
GB = '#2E8B57'
def dy(k): return 500 - k * 5.6
for px, pw in ((0, 800), (860, 804)):
    s.append(f'<rect x="{px}" y="0" width="{pw}" height="560" rx="18" fill="#ECF1F8"/>')
def _axis(x0, x1):
    s.append(f'<path d="M{x0} {dy(70)} V{dy(0)} H{x1}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
    for k in range(0, 71, 10):
        s.append(f'<line x1="{x0 - 6}" y1="{dy(k)}" x2="{x0}" y2="{dy(k)}" stroke="{NAVY}" stroke-width="2"/>')
    L.append(lab(x0 - 58, dy(70) - 20, 48, '<br>'.join(str(k) for k in range(70, -1, -10)), 'lab small', 1, f'line-height:{5.6 * 10:.0f}px; text-align:right; color:#4A5568; font-size:20px; '))
def _dots(cx, ks, col):
    for j, k in enumerate(ks):
        s.append(f'<circle cx="{cx - 36 + j * 24}" cy="{dy(k):.1f}" r="8" fill="{col}" stroke="{NAVY}" stroke-width="1.5"/>')
# panel 1: beta = the average
_axis(110, 770)
_mc, _ma = np.mean(C), np.mean(A)
_mb1 = np.mean(B)
for cx, ks, col, m in ((200, C, CTRL, _mc), (440, A, PUR, _ma), (660, B, GB, _mb1)):
    s.append(f'<line x1="{cx - 70}" y1="{dy(m):.1f}" x2="{cx + 70}" y2="{dy(m):.1f}" stroke="{NAVY if col == CTRL else col}" stroke-width="4"/>')
    _dots(cx, ks, col)
s.append(f'<path d="M315 {dy(_mc):.1f} V{dy(_ma) + 12:.1f}" stroke="#2F6BDB" stroke-width="3.5"/><path d="M307 {dy(_ma) + 14:.1f} l8 -14 l8 14 Z" fill="#2F6BDB"/>')
L.append(lab(18, 10, 764, '<b>1. β sets the average K of each group</b>', 'lab left', 1))
L.append(lab(18, 50, 764, 'Dot: one well’s K, its nanowells past the cutoff (of 1,000)<br>Bar: the group’s average', 'lab left small', 1, 'color:#4A5568; font-size:20px; line-height:1.2; '))
L.append(lab(120, 300, 240, f'<b>control</b>: average 10.25<br>= 1.0&#160;%<br>→ <b>α = −4.57</b>', 'lab left small', 1, 'line-height:1.2; font-size:20px; '))
L.append(lab(360, 236, 240, f'<b style="color:#8E5BD0">A</b>: average 24<br>= 2.4&#160;%<br>→ <b>α + δ = −3.71</b>', 'lab left small', 1, 'line-height:1.2; font-size:20px; '))
L.append(lab(322, 420, 104, '<b style="color:#2F6BDB">δ = 0.86</b>', 'lab left small', 1, 'font-size:21px; '))
L.append(lab(140, 506, 120, '<b>control</b>', 'lab small', 1, 'font-size:20px; '))
L.append(lab(380, 506, 120, '<b style="color:#8E5BD0">A</b>', 'lab small', 1, 'font-size:20px; '))
L.append(lab(600, 506, 120, '<b style="color:#2E8B57">B</b>', 'lab small', 1, 'font-size:20px; '))
L.append(lab(430, 140, 220, '<b style="color:#2E8B57">B</b>: average 24, as A<br>Fitted with r = 0.008<br>(right), so δ = 0.44,<br>not 0.86', 'lab left small', 1, 'line-height:1.2; font-size:19px; text-align:right; '))
L.append(lab(130, 532, 640, 'β is the % on the log-odds scale (slide 14)', 'lab left small', 1, 'color:#4A5568; font-size:19px; '))
# panel 2: r = the scatter
P2 = 860
_axis(P2 + 110, P2 + 780)
def _band(cx, m, mu, rr, fill, dash=''):
    sd = math.sqrt(N * mu * (1 - mu) * (1 + (N - 1) * rr)); lo, hi = max(m - 2 * sd, 0), m + 2 * sd
    s.append(f'<rect x="{cx - 62}" y="{dy(hi):.1f}" width="124" height="{dy(lo) - dy(hi):.1f}" rx="8" fill="{fill}"{dash}/>')
    return lo, hi
_mb = np.mean(B)
_band(P2 + 545, _mb, _mb / N, 0.0079, 'none', f' stroke="{GB}" stroke-width="2.5" stroke-dasharray="8 6"')
for cx, ks, col, m in ((P2 + 245, C, CTRL, _mc), (P2 + 395, A, PUR, _ma), (P2 + 545, B, GB, _mb)):
    _band(cx, m, m / N, 0, '#C9D6E8')
    s.append(f'<line x1="{cx - 62}" y1="{dy(m):.1f}" x2="{cx + 62}" y2="{dy(m):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
    _dots(cx, ks, col)
L.append(lab(P2 + 18, 10, 764, '<b>2. r sets how far wells may scatter around it</b>', 'lab left', 2))
L.append(lab(P2 + 18, 50, 764, 'Shaded: where counting noise alone (r = 0) puts a well’s K<br>Each band: the group’s average ± 2 SD', 'lab left small', 2, 'color:#4A5568; font-size:20px; line-height:1.2; '))
L.append(lab(P2 + 616, 96, 180, f'<b style="color:{GB}">B</b>: average also 24<br>64 far above<br>9, 10, 13 below<br>→ r rises to 0.008<br><span style="color:{GB}">dashed: B’s range<br>at r = 0.008</span>', 'lab left small', 2, 'line-height:1.15; font-size:20px; '))
L.append(lab(P2 + 140, 120, 340, '<b>control</b> and <b style="color:#8E5BD0">A</b>:<br>all 8 wells inside<br>→ <b>r ≈ 0</b>', 'lab left small', 2, 'line-height:1.15; font-size:21px; '))
for _cx, _nm in ((P2 + 245, '<b>control</b>'), (P2 + 395, '<b style="color:#8E5BD0">A</b>'), (P2 + 545, '<b style="color:#2E8B57">B</b>')):
    L.append(lab(_cx - 60, 506, 120, _nm, 'lab small', 2, 'font-size:20px; '))
L.append(lab(0, 574, 1664, '<b>One r per test, shared by its two groups.</b><br>A vs control: r ≈ 0, so the step from 10.25 to 24 is far beyond the scatter, <b>z = 3.76</b><br>'
          'B vs control: B’s wells raise r to 0.008, the scatter widens, and a similar step is not surprising, <b>z = 0.91</b> (z: slide 18)', 'lab left small', 3, 'line-height:1.25; color:#1B2A41; '))
slide('st_dials', 'The statistics · step 3, the two dials', 'β sets the average K, r sets the scatter',
      svg('Two panels on the same K axis, 0 to 70. Left: the four control wells (7, 8, 14, 11) and the four A wells (21, 24, 28, 23) as dots, with a bar at each group average, 10.25 and 24; '
          'the control average is 1.0 percent, α = −4.57; the A average is 2.4 percent, α + δ = −3.71; an arrow between the bars is δ = 0.86. '
          'Right: control, A and B wells with a shaded band for each, the range counting noise alone allows: about 4 to 17 for control, 14 to 34 for A and B. '
          'Control and A wells all fall inside. B’s well at 64 is far outside; a dashed band shows B’s range once r = 0.008, about 0 to 53.', s, h=680), L,
      'This slide separates the two kinds of dial the fit turns. Left: β only sets where each group’s wells sit on average. The control wells average 10.25 of 1,000 nanowells past the cutoff, 1.0 percent, and α is that percentage on the log-odds scale, −4.57. '
      'The A wells average 24, 2.4 percent, which is α + δ = −3.71; δ, 0.86, is how far A sits above control. '
      'Right: r only sets how far wells may scatter around their average. The shaded bands show where a well would land from counting noise alone, the average plus or minus two standard deviations of a binomial. '
      'All four control wells and all four A wells are inside their bands, so there is no extra scatter to explain, and r is estimated at zero. B’s wells average 24 too, but one of them is at 64, far outside, '
      'so when B is tested against control the fit raises r to 0.008, which widens the range, the dashed band. Each test fits one r shared by its two groups. '
      'That is why the two tests disagree: with r near zero, A’s step up from control is far beyond the scatter, z = 3.76; with r = 0.008, B’s step of similar size is within it, z = 0.91.', steps={3})

# ---------------------------------------------------------------- steps 3-4: the log-likelihood worked well by well
s, L = [], []
_kk = np.array(C + A); _XX = np.column_stack([np.ones(8), [0] * 4 + [1] * 4])
_rows = {}
for _nm, _X in (('gap', _XX), ('nogap', np.ones((8, 1)))):
    _f = fit_bb(_kk, [N] * 8, _X); _mu = 1 / (1 + np.exp(-(_X @ _f.coef))); _rr = _f.rho
    if _nm == 'gap':
        _mu = np.array([np.mean(C)] * 4 + [np.mean(A)] * 4) / N
    if _rr < 1e-6:
        _lpk = stats.binom.logpmf(_kk, N, _mu)
    else:
        _lpk = stats.betabinom.logpmf(_kk, N, _mu * (1 - _rr) / _rr, (1 - _mu) * (1 - _rr) / _rr)
    _rows[_nm] = (_mu, _lpk, _rr, float(np.sum(_lpk)))
TY, LH = 84, 40
for i in range(8):
    y = TY + LH * (i + 1) + 4
    s.append(f'<rect x="0" y="{y}" width="985" height="{LH - 4}" rx="6" fill="{"#E3EAF3" if i < 4 else "#EEE6F8"}"/>')
s.append(f'<rect x="0" y="{TY + 9 * LH + 6}" width="985" height="3" fill="{NAVY}"/>')
for gx, key in ((492, 'gap'), (905, 'nogap')):
    for i, lp in enumerate(_rows[key][1]):
        y = TY + LH * (i + 1) + 12
        s.append(f'<rect x="{gx}" y="{y}" width="{-lp * 19:.1f}" height="{LH - 20}" rx="3" fill="{PUR if i >= 4 else CTRL}"/>')
def _col(x, w, head, vals, foot, extra=''):
    L.append(lab(x, TY + 6, w, '<br>'.join([f'<b>{head}</b>'.replace('<sub>', '<sub style="line-height:0">')] + vals + [foot]), 'lab small', 2, f'line-height:{LH}px; color:#1B2A41; white-space:nowrap; ' + extra))
L.append(lab(175, 0, 400, '<b>Step 3: fit as two groups</b><br>π: control 1.0&#160;%, A 2.4&#160;%<br>r ≈ 0', 'lab small', 1, 'line-height:1.15; color:#2F6BDB; font-size:21px; '))
L.append(lab(590, 0, 400, '<b>Step 4: fit as one group (δ = 0)</b><br>π: 1.7&#160;% for all 8 wells<br>r = 0.0027', 'lab small', 1, 'line-height:1.15; color:#2F6BDB; font-size:21px; '))
_col(0, 110, 'well', [f'control {i + 1}' for i in range(4)] + [f'A {i + 1}' for i in range(4)], '&#160;', 'text-align:left; ')
_col(110, 60, 'K<sub>w</sub>', [str(k) for k in _kk], '&#160;')
for x0, key in ((175, 'gap'), (590, 'nogap')):
    mu, lp, rr, ll = _rows[key]
    _col(x0, 90, 'π', [f'{m * 100:.1f}&#160;%' for m in mu], '&#160;')
    _col(x0 + 90, 140, 'P(K<sub>w</sub> | π, r)', [f'{math.exp(v):.3f}' for v in lp], '&#160;')
    _col(x0 + 230, 80, 'ln P', [f'{v:.2f}'.replace('-', '−') for v in lp], f'<b>Σ {ll:.1f}</b>'.replace('-', '−'))
L.append(lab(0, TY + 10 * LH + 10, 985, '<b>K<sub>w</sub></b>: well w’s count of nanowells past the cutoff, of n = 1,000<br><b>π</b>: the rate the fit gives well w’s group (slide 13)<br>'
          '<b>P(K<sub>w</sub> | π, r)</b>: the chance that a well with rate π and scatter r has exactly K<sub>w</sub> past the cutoff<br>'
          'Bars: −ln P, so a longer bar means a less probable count<br><b>log L = Σ ln P</b>: one number per fit', 'lab left small', 3, 'line-height:1.22; color:#1B2A41; font-size:19px; '))
# right: where each P comes from, on one K axis
s.append('<rect x="1020" y="0" width="644" height="606" rx="18" fill="#ECF1F8"/>')
def _kx(k): return 1100 + k * 13.4
_PB = 420
def _py(p): return _PB - p * 1500
_d1 = stats.binom(N, np.mean(C) / N); _dA = stats.binom(N, np.mean(A) / N); _m0, _r0 = _rows['nogap'][0][0], _rows['nogap'][2]
_d0 = stats.betabinom(N, _m0 * (1 - _r0) / _r0, (1 - _m0) * (1 - _r0) / _r0)
for d, col, dash in ((_d1, NAVY, ''), (_dA, PUR, ''), (_d0, '#7A8699', ' stroke-dasharray="7 5"')):
    s.append('<path d="M' + ' L'.join(f'{_kx(k):.1f} {_py(d.pmf(k)):.1f}' for k in range(0, 41)) + f'" stroke="{col}" stroke-width="3.5" fill="none"{dash}/>')
for kq, dsol in ((7, _d1), (28, _dA)):
    s.append(f'<line x1="{_kx(kq)}" y1="{_PB}" x2="{_kx(kq)}" y2="{_py(dsol.pmf(kq)) - 6:.1f}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="4 4"/>')
    for d in (dsol, _d0):
        s.append(f'<circle cx="{_kx(kq)}" cy="{_py(d.pmf(kq)):.1f}" r="8" fill="{OR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<path d="M{_kx(0)} {_py(0.125):.1f} V{_PB} H{_kx(40)}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
for k in range(0, 41, 10):
    s.append(f'<line x1="{_kx(k)}" y1="{_PB}" x2="{_kx(k)}" y2="{_PB + 9}" stroke="{NAVY}" stroke-width="2"/>')
    L.append(lab(int(_kx(k)) - 24, _PB + 9, 48, str(k), 'lab small', 5, 'font-size:19px; color:#1B2A41; '))
for pv in (0.05, 0.10):
    s.append(f'<line x1="{_kx(0) - 6}" y1="{_py(pv):.1f}" x2="{_kx(0)}" y2="{_py(pv):.1f}" stroke="{NAVY}" stroke-width="2"/>')
L.append(lab(1022, int(_py(0.10) - 0.05 * 1500 / 2), 68, '0.10<br>0.05', 'lab small', 5, f'line-height:{0.05 * 1500:.1f}px; text-align:right; font-size:19px; color:#1B2A41; '))
L.append(lab(1022, int(_py(0.125)) - 30, 68, '<b>P</b>', 'lab small', 5, 'font-size:20px; text-align:right; '))
RY = 478
s.append(f'<line x1="{_kx(0)}" y1="{RY}" x2="{_kx(40)}" y2="{RY}" stroke="#B8C4D6" stroke-width="2"/>')
for mk, col, dash in ((np.mean(C), NAVY, ''), (np.mean(A), PUR, ''), (np.mean(C + A), '#7A8699', ' stroke-dasharray="5 4"')):
    s.append(f'<line x1="{_kx(mk):.1f}" y1="{RY - 16}" x2="{_kx(mk):.1f}" y2="{RY + 16}" stroke="{col}" stroke-width="4"{dash}/>')
for i_, k in enumerate(C + A):
    off = -7 if (k, i_) == (23, 7) else (7 if (k, i_) == (24, 5) else 0)
    s.append(f'<circle cx="{_kx(k) + off:.1f}" cy="{RY}" r="6.5" fill="{CTRL if i_ < 4 else PUR}" stroke="{NAVY}" stroke-width="1.5"/>')
L.append(lab(1040, 8, 610, '<b>Where each P comes from</b><br>P = the height of the well’s curve at its K<sub>w</sub><br><b>Solid curves</b>: two groups, control at 1.0&#160;% and <span style="color:#8E5BD0">A at 2.4&#160;%</span> (r ≈ 0)<br>'
          '<span style="color:#5A6679"><b>Dashed curve</b>: one group at 1.7&#160;%, widened by r = 0.0027</span>', 'lab left small', 4, 'line-height:1.25; font-size:19px; color:#1B2A41; '))
L.append(lab(1380, 124, 276, f'<b style="color:#A9501A">Two wells marked</b><br>Control 1 (K = 7): solid {_d1.pmf(7):.3f}, dashed {_d0.pmf(7):.3f}<br>A 3 (K = 28): solid {_dA.pmf(28):.3f}, dashed {_d0.pmf(28):.3f}', 'lab left small', 5, 'line-height:1.2; font-size:19px; color:#1B2A41; '))
L.append(lab(1040, RY + 22, 610, '<b>x-axis: K<sub>w</sub></b>, nanowells past the cutoff (of 1,000)<br>Dots under the axis: the 8 wells<br>Short vertical marks: the averages 10, 24 and <span style="color:#5A6679">17 (all 8, dashed)</span>', 'lab left small', 5, 'line-height:1.2; font-size:19px; color:#1B2A41; '))
s.append('<rect x="0" y="624" width="1664" height="74" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(16, 631, 1632, '<b>So far:</b> two searches gave two fits: step 3 with a gap, step 4 without (slide 15)<br><b>Next:</b> step 4’s r is 0.0027, not 0: why, and why it matters (slide 17)', 'lab left small', 9, 'line-height:1.25; color:#1B2A41; font-size:21px; '))
slide('st_ll', 'The statistics · steps 3–4 · the two fits', 'The two fits, well by well',
      svg('A table of the 8 wells: for each, its count K, and under each fit its mean π, the chance P of its exact count, and ln P, with bars of −ln P. '
          'With the gap the ln P values are −2.2 to −3.0 and add to −20.2; without the gap, one mean of 1.70 percent, they are −2.9 to −4.2 and add to −27.4. '
          'A box works control well 1 by hand under both fits, then adds the columns and forms D = 14.2.', s, h=706), L,
      'This slide does the arithmetic behind the log-likelihood for every well. Under the fit with the gap, control wells have mean 1.000 percent and A wells 2.400 percent, and r is essentially zero, so each well’s chance is a plain binomial: '
      'for control well 1, K = 7, it is C(1000, 7) times 0.01 to the 7th times 0.99 to the 993rd, 0.090, whose log is −2.41. The eight logs add to −20.2. '
      'Under the fit without the gap, all eight wells share one mean, 1.700 percent, and r rises to 0.0027 to absorb the spread, so the chance is the beta-binomial with a = 6.35 and b = 367: for the same well it is 0.029, log −3.55. '
      'Every well is less probable without the gap, because the control wells now sit below the shared mean and the A wells above it; the logs add to −27.4. Twice the difference, 14.2, is the likelihood ratio, and its square root 3.77 is the z of step 5.', steps={3, 4})

# ---------------------------------------------------------------- the test, steps 3-5, worked step by step (main)
s, L = [], []
_FITS = {}
for _nm, _ks in (('A', A), ('C', CC), ('B', B)):
    _f1 = fit_bb(C + _ks, [N] * 8, fitX); _f0 = fit_bb(C + _ks, [N] * 8, np.ones((8, 1)))
    _lr = 2 * (_f1.loglik - _f0.loglik); _z = math.sqrt(max(_lr, 0)) * np.sign(_f1.coef[1])
    _FITS[_nm] = dict(l1=_f1.loglik, l0=_f0.loglik, lr=_lr, z=_z, p=1 - stats.norm.cdf(_z), r1=_f1.rho, r0=_f0.rho,
                      m0=1 / (1 + math.exp(-_f1.coef[0])), m1=1 / (1 + math.exp(-sum(_f1.coef))), mn=1 / (1 + math.exp(-_f0.coef[0])))
FA = _FITS['A']
def _m(v): return f'{v:.1f}'.replace('-', '−')
PX3 = [0, 562, 1124]; PW3 = 540
for px in PX3:
    s.append(f'<rect x="{px}" y="48" width="{PW3}" height="326" rx="18" fill="#ECF1F8"/>')
for px in PX3[:2]:
    s.append(f'<path d="M{px+PW3+4} 200 h10 v-9 l14 15 l-14 15 v-9 h-10 Z" fill="{NAVY}"/>')
def _yr(p): return 238 - p / 0.03 * 118
def _dots(px):
    out = []
    for j, k in enumerate(C):
        out.append(f'<circle cx="{px+110+j*28}" cy="{_yr(k/1000):.1f}" r="9" fill="{CTRL}" stroke="{NAVY}" stroke-width="2"/>')
    for j, k in enumerate(A):
        out.append(f'<circle cx="{px+270+j*28}" cy="{_yr(k/1000):.1f}" r="9" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
    out.append(f'<path d="M{px+70} 112 V238 H{px+400}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
    return out
# step 3: two means
s += _dots(PX3[0])
s.append(f'<line x1="{PX3[0]+95}" y1="{_yr(FA["m0"]):.1f}" x2="{PX3[0]+209}" y2="{_yr(FA["m0"]):.1f}" stroke="{NAVY}" stroke-width="4"/>')
s.append(f'<line x1="{PX3[0]+255}" y1="{_yr(FA["m1"]):.1f}" x2="{PX3[0]+369}" y2="{_yr(FA["m1"]):.1f}" stroke="{PUR}" stroke-width="4"/>')
# step 4: one mean
s += _dots(PX3[1])
s.append(f'<line x1="{PX3[1]+95}" y1="{_yr(FA["mn"]):.1f}" x2="{PX3[1]+369}" y2="{_yr(FA["mn"]):.1f}" stroke="{NAVY}" stroke-width="4" stroke-dasharray="10 6"/>')
# step 5: chance alone, and A's z
def _xz(z): return PX3[2] + 40 + (z + 3.2) / 8.2 * 460
zz = np.linspace(-3.2, 5.0, 90)
s.append('<path d="M' + ' L'.join(f'{_xz(z):.1f} {238 - stats.norm.pdf(z) * 300:.1f}' for z in zz) + f'" stroke="{NAVY}" stroke-width="3" fill="none"/>')
tz = np.linspace(FA['z'], 5.0, 20)
s.append(f'<path d="M{_xz(FA["z"]):.1f} 238 ' + ' '.join(f'L{_xz(z):.1f} {238 - stats.norm.pdf(z) * 300 - 3:.1f}' for z in tz) + f' L{_xz(5.0):.1f} 238 Z" fill="{OR}"/>')
s.append(f'<line x1="{PX3[2]+30}" y1="238" x2="{PX3[2]+510}" y2="238" stroke="{NAVY}" stroke-width="2.5"/>')
s.append(f'<line x1="{_xz(FA["z"]):.1f}" y1="150" x2="{_xz(FA["z"]):.1f}" y2="238" stroke="{PUR}" stroke-width="4"/>')
L.append(lab(0, 0, 1664, '<b>The question now:</b> is log L = −20.1 (two groups) against −27.2 (one group) more of a difference than chance would give?', 'lab left small', 1, 'color:#1B2A41; '))
T3 = [('3. Fit as two groups', 'Each well: K of 1,000 nanowells at its own rate π<sub>w</sub><br>π<sub>w</sub> spreads around its group’s mean by r<br>Fit: the 2 means and r that best explain the 8 counts<br>Σ ln P = <b>log L</b> (slide 16)'),
      ('4. Fit as one group', f'One mean for all 8 wells: δ = 0<br>r is refitted and absorbs the gap: r = {FA["r0"]:.4f}<br>One mean fits the counts worse: a lower log L'),
      ('5. Compare the fits', f'<b>D</b> = 2 × (log L two groups − log L one group)<br>= 2 × ({_m(FA["l1"])} − ({_m(FA["l0"])})) = {FA["lr"]:.1f}<br>If A did nothing, D would behave like z²<br>z = √D = {FA["z"]:.2f}<br>p = area beyond z (one-sided) = 0.00008')]
for (ti, tx), px in zip(T3, PX3):
    L.append(lab(px + 18, 56, PW3 - 36, f'<b>{ti}</b>', 'lab left', 2))
    L.append(lab(px + 18, 250, PW3 - 36, tx, 'lab left small', 3, 'line-height:1.18; color:#1B2A41; font-size:20px; '))
L.append(lab(PX3[0] + 410, 150, 126, f'log L<br><b>{_m(FA["l1"])}</b>', 'lab small', 2, 'line-height:1.15; color:#1B2A41; '))
L.append(lab(PX3[1] + 410, 150, 126, f'log L<br><b>{_m(FA["l0"])}</b>', 'lab small', 2, 'line-height:1.15; color:#1B2A41; '))
L.append(lab(PX3[2] + 18, 112, 170, 'chance alone<br>(no gap)', 'lab left small', 2, 'line-height:1.15; color:#4A5568; '))
L.append(lab(PX3[0] + 76, 100, 300, 'rate, K<sub>w</sub> ÷ 1,000', 'lab left small', 2, 'color:#4A5568; '))
L.append(lab(PX3[1] + 76, 100, 300, 'rate, K<sub>w</sub> ÷ 1,000', 'lab left small', 2, 'color:#4A5568; '))
L.append(lab(int(_xz(FA['z'])) - 120, 112, 160, f'<b style="color:#6B3FB0">A: z = {FA["z"]:.2f}</b>', 'lab small', 2))
# results for all three molecules
TY, LH = 392, 34
L.append(lab(0, TY, 120, '<br>'.join(['&#160;', '<b>A</b>', '<b>C</b>', '<b>B</b>']), 'lab left small', 4, f'line-height:{LH}px; color:#1B2A41; '))
_cols = [('3. log L', [_m(_FITS[k]['l1']) for k in 'ACB']), ('4. log L', [_m(_FITS[k]['l0']) for k in 'ACB']),
         ('5. D', [f'{_FITS[k]["lr"]:.1f}' for k in 'ACB']), ('z', [f'{_FITS[k]["z"]:.2f}' for k in 'ACB']), ('p', ['0.00008', '0.041', '0.17']),
         ('odds ratio (95 % CI)', ['<b>2.4</b> (1.7–3.6)', '1.4 (0.96–2.2)', '1.6 (0.57–4.3)'])]
for ci, (h, v) in enumerate(_cols):
    L.append(lab(84 + ci * 118 if ci < 5 else 680, TY, 118 if ci < 5 else 196, '<br>'.join([f'<b>{h}</b>'] + v), 'lab small', 4, f'line-height:{LH}px; color:#1B2A41; white-space:nowrap; font-size:21px; '))
s.append(f'<rect x="0" y="{TY+LH+2}" width="870" height="2" fill="{GRID}"/>')
L.append(lab(0, TY + 4 * LH + 6, 870, 'Per-well p = (1 + controls ≥ it) ÷ (controls + 1)<br>Each A well: p = 0.2, the floor 1/5<br>'
          '<span style="color:#4A5568">B’s wild well (64) raises its r to 0.008 (A: ≈ 0), so B’s D is small</span>', 'lab left small', 4, 'line-height:1.18; color:#1B2A41; font-size:21px; '))
s.append(f'<rect x="890" y="{TY-6}" width="774" height="{630-TY+6}" rx="14" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(908, TY, 742, '<b>What the test assumes</b><br>• Wells are treated as independent (they share one estimated cutoff)<br>• Nanowells in one well are not: r measures how much they share<br>'
          '• One r serves control and molecule wells alike<br>• D ≈ z² is approximate with 4 + 4 wells (the shuffling tests check it)<br>'
          '• One-sided: only a rise counts<br>• The cutoff is fixed before testing', 'lab left small', 5, 'line-height:1.18; color:#1B2A41; font-size:21px; '))
slide('st_test', 'The statistics · step 5 · the test', 'Comparing the two fits: D, z and p',
      svg('Three panels for molecule A. Step 3: the eight wells as dots on a rate axis, four control and four A, with the fitted control mean, 1.0 percent, '
          'and A mean, 2.4 percent, as two lines; log-likelihood −20.1. Step 4: the same dots with one shared mean, 1.7 percent, dashed; log-likelihood −27.2. '
          'Step 5: the bell curve of z by chance alone, with A’s z of 3.76 far in the right tail, the tail beyond it shaded. Below, a table of the same steps for '
          'molecules A, C and B, and a box listing what the test assumes.', s, h=640), L,
      'This slide works through the test itself, steps 3 to 5, for molecule A against the four control wells; molecules C and B go through exactly the same steps, '
      'shown in the table. Step 3, the fit with a gap. The model says each well’s count K comes from its thousand nanowells, each past the cutoff with the well’s own '
      'probability π<sub>w</sub>; the wells’ rates π<sub>w</sub> scatter around their group’s mean by an amount set by r. That is the beta-binomial of slide 10. For given values of the control mean, '
      'A’s mean and r, one can compute how probable the eight observed counts are. The fit, maximum likelihood, searches for the values that make them most probable: '
      'here a control mean of 1.0 %, an A mean of 2.4 % and r close to 0. The logarithm of that best probability is the log-likelihood, −20.1; logs are used because '
      'the probabilities are tiny and multiply across wells, and logs turn the product into a sum. Step 4, the fit with no gap: the same model with one mean for all '
      'eight wells, so δ = 0. r is refitted and comes out at 0.003, because it now has to explain the difference between the groups as wells differing. One mean '
      'cannot fit both groups as well as two, so the log-likelihood is lower, −27.2. Step 5 compares the two. The likelihood-ratio statistic, D, is twice the '
      'difference, 14.2. If there were no real gap, D would behave like the square of a standard normal variable, a chi-square with one degree of freedom, so its '
      'square root, z = 3.76, is read against the bell curve of chance alone. The p-value is the chance that chance alone gives a z this large or larger, 0.00008. '
      'It is one-sided because only a rise in exceedances counts as a response. The table repeats the steps for C and B: C has a smaller gap and p = 0.042; B’s one '
      'wild well raises r to 0.008, so the fit without a gap can explain B nearly as well, D is 0.8 and p is 0.18. The box lists the assumptions: wells are '
      'independent, nanowells within a well are not and r measures how much, one r serves both groups, the chi-square reading of D is an approximation with so few '
      'wells, which the shuffling-based tests check, the test is one-sided, and the cutoff is fixed before fitting. '
      'The correction for testing several molecules, step 6, is on backup slide 24.', steps={3, 4, 5})

# ---------------------------------------------------------------- Benjamini-Hochberg (backup)
s, L = [], []
_P = [('A', _FITS['A']['p']), ('C', _FITS['C']['p']), ('B', round(_FITS['B']['p'], 2))]
_Q = [min(1.0, p * 3 / (i + 1)) for i, (_, p) in enumerate(_P)]
for i in range(1, -1, -1): _Q[i] = min(_Q[i], _Q[i + 1])
L.append(lab(0, 0, 1664, 'Testing 3 molecules that do nothing, each at p &lt; 0.05: at least one false call 14&#160;% of the time if the tests were independent and exact (1 − 0.95³ = 0.14)<br>'
          'Benjamini–Hochberg, the q of the report, keeps false calls to about 5&#160;% of the calls made', 'lab left small', 1, 'line-height:1.25; color:#1B2A41; '))
TY, LH = 110, 52
L.append(lab(0, TY, 120, '<br>'.join(['<b>molecule</b>'] + [f'<b>{m}</b>' for m, _ in _P]), 'lab left small', 2, f'line-height:{LH}px; color:#1B2A41; '))
_bc = [('p (step 5)', [fmt(p) for _, p in _P]), ('1. rank', ['1', '2', '3']), ('2. limit 0.05 × rank ÷ 3', [f'{0.05*(i+1)/3:.3f}' for i in range(3)]),
       ('3. q = p × 3 ÷ rank', [fmt(x) for x in _Q]), ('q &lt; 0.05?', ['<b>yes</b>' if x < 0.05 else 'no' for x in _Q])]
_bx = [(130, 130), (270, 100), (380, 270), (660, 220), (890, 140)]
for (x, w), (h, v) in zip(_bx, _bc):
    L.append(lab(x, TY, w, '<br>'.join([f'<b>{h}</b>'] + v), 'lab small', 2, f'line-height:{LH}px; color:#1B2A41; white-space:nowrap; '))
s.append(f'<rect x="0" y="{TY+LH-4}" width="1030" height="2" fill="{GRID}"/>')
# cartoon: p against rank on a log scale, with the limit 0.05 x rank / 3
GX0, GX1, GY0, GY1 = 1200, 1650, 130, 420
def _gx(k): return GX0 + (k - 0.5) / 3 * (GX1 - GX0)
def _gy(p): return GY1 - (math.log10(p) + 5) / 5 * (GY1 - GY0)
s.append(f'<path d="M{GX0} {GY0} V{GY1} H{GX1}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
s.append('<path d="' + ' '.join(f'{"M" if k == 1 else "L"}{_gx(k-0.5):.1f} {_gy(0.05*k/3):.1f} L{_gx(k+0.5):.1f} {_gy(0.05*k/3):.1f}' for k in (1, 2, 3))
         + f'" stroke="{OR}" stroke-width="4" fill="none"/>')
for k, (m, p) in enumerate(_P, 1):
    s.append(f'<circle cx="{_gx(k):.1f}" cy="{_gy(p):.1f}" r="12" fill="{PUR if p < 0.05*k/3 else "#C9B6E6"}" stroke="{NAVY}" stroke-width="2"/>')
for v, t_ in ((1e-5, '0.00001'), (1e-3, '0.001'), (1, '1')):
    s.append(f'<line x1="{GX0-8}" y1="{_gy(v):.1f}" x2="{GX0}" y2="{_gy(v):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(GX0 - 130, int(_gy(1e-5)) - 16, 120, '0.00001', 'lab small', 3, 'text-align:right; '))
L.append(lab(GX0 - 130, int(_gy(1e-3)) - 16, 120, '0.001', 'lab small', 3, 'text-align:right; '))
L.append(lab(GX0 - 130, int(_gy(1)) - 16, 120, 'p = 1', 'lab small', 3, 'text-align:right; '))
for _k, _m in ((1, 'A'), (2, 'C'), (3, 'B')):
    L.append(lab(int(_gx(_k)) - 70, GY1 + 8, 140, f'rank {_k}<br>({_m})', 'lab small', 3, 'line-height:1.15; '))
L.append(lab(GX0 + 10, GY0 - 6, 450, '<b style="color:#A9501A">orange steps</b>: the limit, 0.05 × rank ÷ 3<br>A p below it is called', 'lab left small', 3, 'line-height:1.15; '))
L.append(lab(0, 320, 1000, '<b>How it is done</b><br>1. Sort the p-values, smallest first: that order is the rank<br>2. A molecule needs p below 0.05 × rank ÷ 3<br>&#160;&#160;&#160;&#160;The smallest p faces the strictest limit<br>'
          '3. Equivalently, q = p × 3 ÷ rank<br>&#160;&#160;&#160;&#160;Each q is then lowered to the smallest q at its rank or above (no change here)<br>4. Called when q &lt; 0.05<br>'
          '<b>C</b>: p = 0.041 is below 0.05 but above its limit, 0.033, so q = 0.061: not called', 'lab left small', 4, 'line-height:1.22; color:#1B2A41; font-size:21px; '))
L.append(lab(0, 590, 1664, 'Assumes the molecules’ tests are independent or positively related<br>Sharing the same control wells keeps them positively related<br>The correction covers every molecule compared in one analysis: analyse one donor at a time', 'lab left small', 5, 'line-height:1.22; color:#4A5568; font-size:21px; '))
slide('st_bh', 'Backup · step 6 · Benjamini–Hochberg', 'Correcting for 3 molecules',
      svg('Left, a table for molecules A, C and B: p, rank, the limit 0.05 times rank over 3, corrected q and whether called. Right, the three p-values plotted '
          'against rank on a log scale with the limit drawn as orange steps: A falls far below its step and is called; C and B lie above theirs.', s, h=680), L,
      _aside, steps={6})

# ---------------------------------------------------------------- backup: the supporting tests (Higher Criticism, upper-tail A-D, permutation p)
import itertools as _it
s, L = [], []
_rng = np.random.default_rng(9)
_MU, _SD = -0.05, 0.36; _CUT = _MU + stats.norm.ppf(0.99) * _SD
def _well(k, resp=False):
    lo = _rng.normal(_MU, _SD, 5000); lo = lo[lo < _CUT][:1000 - k]; hi = []
    while len(hi) < k:
        v = _rng.normal(_CUT + 0.25, 0.45) if resp else _rng.normal(_MU, _SD)
        if v > _CUT: hi.append(v)
    return np.concatenate([lo, hi])
_pool = np.concatenate([_well(k) for k in C]); _N = len(_pool); _A1 = _well(A[0], True)
_x = np.sort(_A1)[::-1]; _n = len(_x)
_ps = np.array([(1 + np.sum(_pool >= v)) / (_N + 1) for v in _x]); _i = np.arange(1, _n + 1)
_hc = np.sqrt(_n) * (_i / _n - _ps) / np.sqrt(_ps * (1 - _ps)); _ok = (_ps > 1 / _n) & (_i <= 0.1 * _n)
_ist = int(_i[_ok][np.argmax(_hc[_ok])]); _hcv = float(_hc[_ok].max())
_ps0 = np.sort(_pool); _F0 = lambda v: np.searchsorted(_ps0, v, side='right') / _N
_psn = np.sort(_A1); _Fn = lambda v: np.searchsorted(_psn, v, side='right') / _n
_tail = _ps0[_ps0 > np.median(_pool)]
_d = np.maximum(_F0(_tail) - _Fn(_tail), 0); _w = 1 - _F0(_tail); _w[_w <= 0] = np.nan
_A2 = float(_n * np.nansum(_d ** 2 / _w) / _N)
_means = [np.mean([(C + A)[j] for j in c]) for c in _it.combinations(range(8), 4)]
_real = np.mean(A); _pperm = sum(m >= _real - 1e-9 for m in _means) / len(_means)
PX = [0, 562, 1124]; PW_ = 540
for px in PX:
    s.append(f'<rect x="{px}" y="0" width="{PW_}" height="298" rx="18" fill="#ECF1F8"/>')
# panel 1: HC, the well's sorted p-values against the evenly spread line
def h1x(i): return 50 + (i - 1) / 59 * 460
def h1y(p): return 250 - (math.log10(p) + 4) / 3 * 160
s.append(f'<rect x="{h1x(1)-6:.1f}" y="90" width="{h1x(_ist - 0.5) - h1x(1) + 6:.1f}" height="160" fill="#E3E7EE"/>')
s.append('<path d="M' + ' L'.join(f'{h1x(i):.1f} {h1y(i / _n):.1f}' for i in range(1, 61)) + f'" stroke="{NAVY}" stroke-width="2.5" stroke-dasharray="7 5" fill="none"/>')
for i in range(1, 61):
    p = _ps[i - 1]; col = OR if i == _ist else ('#9AA3AF' if p <= 1 / _n else PUR)
    s.append(f'<circle cx="{h1x(i):.1f}" cy="{h1y(p):.1f}" r="{7 if i == _ist else 4.5}" fill="{col}"/>')
s.append(f'<path d="M50 90 V250 H510" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
L.append(lab(18, 10, 504, '<b>Higher Criticism</b> (test B)', 'lab left', 1))
L.append(lab(18, 50, 504, 'A well 1: its nanowells ranked by p (log)', 'lab left small', 1, 'color:#4A5568; '))
L.append(lab(60, 256, 200, 'rank i: 1 → 60', 'lab left small', 1, 'color:#4A5568; '))
L.append(lab(int(h1x(26)), 176, 260, f'<b style="color:#A9501A">peak: i* = {_ist}</b><br>HC = {_hcv:.1f}', 'lab left small', 1, 'line-height:1.1; '))
# panel 2: upper tails of control pool and the well
P2 = PX[1]
def t2x(v): return P2 + 50 + (v - 0.0) / 1.6 * 460
def t2y(q): return 250 - (math.log10(max(q, 1e-4)) + 4) / 4 * 170
xs = np.linspace(0.0, 1.6, 80)
s.append('<path d="M' + ' L'.join(f'{t2x(v):.1f} {t2y(1 - _F0(v)):.1f}' for v in xs) + f'" stroke="{NAVY}" stroke-width="3" fill="none"/>')
s.append('<path d="M' + ' L'.join(f'{t2x(v):.1f} {t2y(1 - _Fn(v)):.1f}' for v in xs) + f'" stroke="{PUR}" stroke-width="3" fill="none"/>')
s.append(f'<line x1="{t2x(_CUT):.1f}" y1="80" x2="{t2x(_CUT):.1f}" y2="250" stroke="{NAVY}" stroke-width="2" stroke-dasharray="6 4"/>')
s.append(f'<path d="M{P2+50} 80 V250 H{P2+510}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
L.append(lab(P2 + 18, 10, 504, '<b>Upper-tail Anderson–Darling</b> (test C)', 'lab left', 2))
L.append(lab(P2 + 18, 50, 504, 'share of nanowells above each fold change', 'lab left small', 2, 'color:#4A5568; '))
L.append(lab(P2 + 326, 90, 200, '<b style="color:#6B3FB0">purple: A well 1</b><br><b>navy: controls</b>', 'lab left small', 2))
L.append(lab(int(t2x(_CUT)) + 6, 216, 150, 'cutoff ×2.2', 'lab left small', 2, 'color:#4A5568; '))
L.append(lab(P2 + 60, 256, 400, 'ln fold change →', 'lab left small', 2, 'color:#4A5568; '))
# panel 3: permutation of whole wells
P3 = PX[2]
vals, cnts = np.unique(np.round(_means, 1), return_counts=True)
lo_, hi_ = min(_means), max(_means)
def p3x(m): return P3 + 50 + (m - 8) / 18 * 460
for v, c in zip(vals, cnts):
    for j in range(c):
        s.append(f'<circle cx="{p3x(v):.1f}" cy="{244 - j * 11:.1f}" r="5" fill="{OR if v >= _real - 1e-9 else CTRL}" stroke="{NAVY}" stroke-width="1"/>')
s.append(f'<line x1="{P3+40}" y1="252" x2="{P3+520}" y2="252" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(P3 + 18, 10, 504, '<b>Permutation p</b> (tests B and C)', 'lab left', 3))
L.append(lab(P3 + 18, 50, 504, f'all {len(_means)} ways to call 4 of the 8 wells “A”', 'lab left small', 3, 'color:#4A5568; '))
L.append(lab(int(p3x(_real)) - 230, 176, 240, f'<b style="color:#A9501A">real labels: {_real:.0f} ↓</b>', 'lab small', 3, 'text-align:right; '))
L.append(lab(P3 + 60, 256, 440, 'mean K of the “A” wells →', 'lab left small', 3, 'color:#4A5568; '))
# formulas below, one box per panel
EY = 308
cK, cP, cB = '#A9501A', '#6B3FB0', '#2F6BDB'
def _tm(x, c): return f'<span style="color:{c}; font-weight:700">{x}</span>'
for px in PX:
    s.append(f'<rect x="{px}" y="{EY}" width="{PW_}" height="{620-EY}" rx="18" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
    s.append(f'<path d="M{px+PW_//2-14} {EY-2} l14 -16 l14 16 Z" fill="#2F6BDB"/>')
L.append(lab(18, EY + 10, 504, f'p<sub>i</sub> = (1 + # control nanowells ≥ x<sub>i</sub>) ÷ (N + 1)<br>HC<sup>+</sup> = max<sub>i</sub> √n (i/n − p<sub>(i)</sub>) ÷ √(p<sub>(i)</sub>(1 − p<sub>(i)</sub>))',
          'lab left', 4, 'font-size:21px; line-height:1.4; color:#1B2A41; '))
L.append(lab(18, EY + 84, 504, 'x<sub>i</sub>: the well’s i-th most extreme nanowell<br>p<sub>(i)</sub>: its p<br>N: control nanowells (4,000)<br>n: the well’s nanowells (1,000)<br>'
          'i/n: where p<sub>(i)</sub> sits by chance (dashed)<br>Only i ≤ 0.1 n is used<br>p<sub>(i)</sub> ≤ 1/n is skipped (grey)<br>'
          'i*: the peak = the candidate responders<br>No cutoff is chosen', 'lab left small', 4, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
L.append(lab(P2 + 18, EY + 10, 504, f'A²<sub>R</sub> = n ∫ (over x &gt; median) [max(F<sub>0</sub> − F<sub>n</sub>, 0)]² ÷ (1 − F<sub>0</sub>) dF<sub>0</sub>',
          'lab left', 5, 'font-size:21px; line-height:1.4; color:#1B2A41; '))
L.append(lab(P2 + 18, EY + 84, 504, 'F<sub>0</sub>: control nanowells’ cumulative share<br>A control well is left out of its own F<sub>0</sub><br>F<sub>n</sub>: the well’s cumulative share<br>'
          'Upper half only, where the well is higher (F<sub>0</sub> &gt; F<sub>n</sub>)<br>÷ (1 − F<sub>0</sub>): gaps far out in the tail count most<br>'
          f'Here: A²<sub>R</sub> = <b>{_A2:.2f}</b><br>A bulk shift raises it too (slide 26)', 'lab left small', 5, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
L.append(lab(P3 + 18, EY + 10, 504, f'p = (# labellings ≥ real) ÷ C(c + t, t)<br>floor = 1 ÷ C(c + t, t) = 1 ÷ C(8, 4) = 1/{len(_means)}',
          'lab left', 6, 'font-size:21px; line-height:1.4; color:#1B2A41; '))
L.append(lab(P3 + 18, EY + 84, 504, 'c, t: control and treatment wells<br>Whole wells are shuffled, never nanowells<br>Only within a plate<br>'
          f'Here (mean K): p = 1/{len(_means)} = {_pperm:.3f}, the floor<br>Fewer than 3 + 2 wells: a pooled Fisher exact test<br>It is anti-conservative: ignores between-well spread', 'lab left small', 6, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
slide('st_supp', 'Backup · the supporting tests', 'Tests B and C, and how their p-values are made',
      svg('Three panels with their formulas below. Higher Criticism: one molecule-A well’s nanowell p-values, ranked, on a log scale, against the dashed line of evenly spread p-values; '
          'the smallest are skipped, and the peak of the statistic at rank 14 marks the candidate responders. Upper-tail Anderson–Darling: the share of nanowells above each fold change for the controls and for the well, '
          'the well’s curve above the controls’ in the tail. Permutation: the mean count of the wells labelled A for all 70 ways to choose 4 of 8 wells, the real labelling the highest.', s), L,
      'Two supporting tests are reported beside the verdict, which rests on the exceedance test. Test B, Higher Criticism, needs no cutoff. Each of the well’s nanowells gets a p-value against the pooled control '
      'nanowells: one plus the number of control nanowells at least as high, over the number of control nanowells plus one. The p-values are ranked, smallest first. If nothing is going on they spread evenly, '
      'the i-th near i over n, the dashed line. HC measures, at each rank, how far the observed p-value falls below that line in standard-error units, and takes the maximum over the most extreme 10 % of '
      'nanowells, skipping p-values at or below 1 over n, where one nanowell could dominate. The rank where it peaks, here 14, gives the candidate responders. Test C, the upper-tail Anderson–Darling statistic, '
      'compares the whole upper half of the well’s distribution with the controls’, counting only where the well has more mass high up, and weighting gaps far out in the tail most. It also rises when the whole '
      'distribution shifts, so on its own it signals a shape change, not rare responders. Neither statistic is read from a textbook table: their p-values come from shuffling whole wells. Every way of labelling '
      'c + t wells as t treated is tried, here C(8, 4) = 70, the statistic is averaged over the labelled wells, and p is the share of labellings at least as extreme as the real one. Its floor is one over the '
      'number of labellings, 1/70 here, and 0.1 with 3 control and 2 treated wells, which is why these tests need more wells than the exceedance test. With fewer than 3 control and 2 treated wells, the '
      'exceedance test falls back to a pooled Fisher exact test, labelled anti-conservative because it treats nanowells as independent. The data here are simulated from slide 9’s distribution.')

# ---------------------------------------------------------------- caveat: a shift of the whole distribution
s, L = [], []
# slide 9's distribution: natural-log fold change of a control nanowell ~ N(-0.05, 0.36), cutoff at its 99th percentile (x2.2)
MU9, SD_ = -0.05, 0.36
CUT = MU9 + stats.norm.ppf(0.99) * SD_
BW = SD_ / 3; LO_ = CUT - 16 * BW; HI_ = LO_ + 24 * BW
nb = int(round((HI_ - LO_) / BW))
RESP = (math.log(3.5), 0.2)                      # responders, about x3.5
def _bins(kind, par):
    edges = [LO_ + i * BW for i in range(nb + 1)]
    out = []
    for a_, b_ in zip(edges[:-1], edges[1:]):
        if kind == 'ctl': p = stats.norm.cdf(b_, par, SD_) - stats.norm.cdf(a_, par, SD_)
        elif kind == 'rare': p = (1 - par) * (stats.norm.cdf(b_, MU9, SD_) - stats.norm.cdf(a_, MU9, SD_)) + par * (stats.norm.cdf(b_, *RESP) - stats.norm.cdf(a_, *RESP))
        else: p = stats.norm.cdf(b_, MU9 + par, SD_) - stats.norm.cdf(a_, MU9 + par, SD_)
        out.append((a_, b_, 1000 * p))
    return out
def _k(bins): return sum(int(round(c)) for a_, _, c in bins if a_ >= CUT - 1e-9)
def _tune(kind, target, lo, hi):
    for _ in range(60):
        mid = (lo + hi) / 2; k = _k(_bins(kind, mid))
        if k == target: return mid
        if k < target: lo = mid
        else: hi = mid
    raise AssertionError((kind, target))
PAR = {'ctl': _tune('ctl', 8, MU9 - 0.3, MU9 + 0.3), 'rare': _tune('rare', 24, 0.0, 0.05), 'shift': _tune('shift', 35, 0.0, 0.5)}
MED = {'ctl': math.exp(PAR['ctl']), 'rare': math.exp(MU9), 'shift': math.exp(MU9 + PAR['shift'])}
def scen(kind): return _bins(kind, PAR[kind])
print('st_shift params', PAR, MED)
PW, PH, BASE = 500, 300, 330
panels = [('ctl', 0, 'Control well 2 (slide 9)', 'The reference<br>About 1&#160;% of nanowells past the cutoff'),
          ('rare', 582, 'Molecule A well 2: rare responders', 'Bulk unchanged, a few nanowells far out<br><b>Detected</b>: what the test is for'),
          ('shift', 1164, 'Made up: everything shifted up', 'Every nanowell a little higher<br>K rises: <b>also “detected”</b>, wrongly')]
for kind, PX, title, verdict in panels:
    def xf(v): return PX + (v - LO_) / (HI_ - LO_) * PW
    bins = scen(kind)
    K = 0
    for a_, b_, cnt in bins:
        cx = xf((a_ + b_) / 2)
        if a_ >= CUT - 1e-9:
            n_or = int(round(cnt)); K += n_or
            for j in range(n_or):
                rr, cc = divmod(j, 2)
                s.append(f'<circle cx="{cx - 4.5 + cc*9:.1f}" cy="{BASE - 5 - rr*9:.1f}" r="3.8" fill="{OR}"/>')
        else:
            nd = int(round(cnt / 10))
            col = FCCOL[max(0, min(6, int((a_ - LO_) / (CUT - LO_) * 6)))]
            for j in range(nd):
                rr, cc = divmod(j, 2)
                s.append(f'<circle cx="{cx - 4.5 + cc*9:.1f}" cy="{BASE - 5 - rr*9:.1f}" r="3.8" fill="{col}"/>')
    s.append(f'<line x1="{PX}" y1="{BASE}" x2="{PX+PW}" y2="{BASE}" stroke="{NAVY}" stroke-width="3"/>')
    s.append(f'<line x1="{xf(CUT):.1f}" y1="{BASE-PH+40}" x2="{xf(CUT):.1f}" y2="{BASE+6}" stroke="{NAVY}" stroke-width="2.5" stroke-dasharray="7 5"/>')
    med = math.log(MED[kind])
    s.append(f'<path d="M{xf(med):.1f} {BASE+6} l-9 16 h18 Z" fill="{NAVY}"/>')
    s.append(f'<line x1="{xf(MU9):.1f}" y1="{BASE-PH+60}" x2="{xf(MU9):.1f}" y2="{BASE}" stroke="#9AA3AF" stroke-width="1.5" stroke-dasharray="3 4"/>')
    L.append(lab(PX, 0, PW, f'<b>{title}</b>', 'lab left', 1))
    L.append(lab(PX, BASE + 24, PW, f'<b>K = {K}</b> past the cutoff<br>▲ median ×{MED[kind]:.2f}', 'lab left small', 2, 'line-height:1.2; '))
    L.append(lab(PX, BASE + 92, PW, verdict, 'lab left small', 3, 'line-height:1.2; '))
    L.append(lab(int(xf(CUT)) + 6, 46, 150, 'cutoff', 'lab left small', 1, 'color:#4A5568; '))
s.append('<rect x="0" y="524" width="1664" height="138" rx="14" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(18, 532, 800, '<span style="color:#4A5568">Blue ●: 10 nanowells (as slide 9)<br>Orange ●: 1 nanowell past the cutoff</span><br><b>The exceedance test alone cannot tell the right two apart</b><br>'
          f'Check the bulk: a moved median (▲ ×{MED["shift"]:.2f} against ×{MED["ctl"]:.2f}–{MED["rare"]:.2f}) is a shift', 'lab left small', 4, 'line-height:1.22; color:#1B2A41; font-size:21px; '))
L.append(lab(840, 532, 806, 'A test on well medians and the tail-shape test both flag it<br>Dividing out each well’s bulk would also remove a real effect, so it is off<br>'
          '<b>Check seeding too:</b> nanowells seeded with fewer cells reach extremes more easily<br>So a seeding difference alone can look like rare responders', 'lab left small', 4, 'line-height:1.22; color:#1B2A41; font-size:21px; '))
slide('st_shift', 'Backup · a caveat', 'What if a treatment shifts the whole distribution up?',
      svg('Three dot histograms of one well’s nanowell fold changes, 1 blue dot = 10 nanowells, 1 orange dot = 1 nanowell past the '
          'same dashed cutoff. Left, control well 2 of slide 9: 8 past the cutoff. Middle, molecule-A well 2 of slide 9: the bulk unchanged, a few nanowells far '
          'out, 24 past. Right, a made-up well whose whole distribution is shifted up by a factor of 1.2 with the same shape: 35 past the cutoff, though no '
          'nanowell responds strongly. A navy marker under each shows the median.', s, h=670), L,
      'A fair question about the exceedance test: what if a treatment does not create rare responders but lifts every nanowell a '
      'little, for example through a cytokine or medium effect that reaches every nanowell? The cutoff is fixed by the control wells, '
      'at their 99th percentile. If the whole treated distribution moves up, more nanowells cross that fixed line even though none of '
      'them responds strongly. Here a made-up shift of 1.2-fold, with the shape unchanged, takes the count from about 10 to 35 per well, more '
      'than molecule A’s rare responders in the middle, and the exceedance test would call it detected. So the exceedance test alone cannot '
      'distinguish rare responders from a bulk shift. The two are told apart by the bulk: in the rare-responder case the median '
      'nanowell of each well is unchanged, in the shift case it moves, and a test on the wells’ medians, the bulk question of slide 8, '
      'detects the shift while it would miss rare responders. The upper-tail shape test reported beside the verdict also flags a '
      'shifted tail; on its own, a shape-test signal is a shift, not rare responders. Normalising each well by its own bulk would '
      'remove such a shift, but it would equally remove a real treatment effect on the bulk, so it is off by default. In practice: '
      'when the verdict is detected, look at the treated wells’ medians before calling the effect rare responders. A second look-alike is seeding: nanowells that started with fewer cells reach extreme fold changes more easily, so if the treatment wells were seeded differently from the controls, that alone can look like rare responders. The verdict is left as it is; compare the wells’ starting-cell distributions before trusting it. The distributions are slide 9’s: log fold change normal with median ×0.95 and the cutoff, the 99th percentile, at ×2.2; the left panel is control well 2 (K = 8), the middle molecule-A well 2 (K = 24), with 1.5&#160;% responders at about ×3.5; the right panel is made up.')

# ---------------------------------------------------------------- planning / conclusions
html = ('<section id="st_plan" data-transition="fade">\n<div class="head">\n<p class="eyebrow">Backup · what can be concluded</p>\n'
        '<h2>When a result counts, and how many wells it takes</h2>\n</div>\n'
        '<div class="takeaways">\n'
        '<div class="tk"><h3>≥ 3 control + ≥ 2 treated wells</h3><p>Fewer, and r cannot be estimated: there is nothing to say how much wells differ.</p></div>\n'
        '<div class="tk"><h3>At least one exceedance</h3><p>With nothing past the cutoff, there is nothing to count.<br>With no <i>control</i> nanowell past it, r cannot be estimated and p overstates the evidence.</p></div>\n'
        '<div class="tk"><h3>q &lt; 0.05</h3><p>After the correction across every molecule tested on the donor.</p></div>\n</div>\n'
        '<table class="pct">\n<tr><th style="width:24%">Wells per donor</th><th style="width:76%">What the design allows</th></tr>\n'
        '<tr><td>3 control + 2 treated</td><td>A conclusion, from the exceedance test</td></tr>\n'
        '<tr><td>5 control + 2 treated</td><td>Also a false-positive check: test control wells against each other (3 vs 2)<br>Every call there is false</td></tr>\n'
        '<tr><td>6 control + 3 treated</td><td>Lowers the shuffling tests’ smallest possible p (4 + 4: 1/70 = 0.014, 6 + 3: 1/84 = 0.012)</td></tr>\n'
        '<tr><td><b>This plate: 4 + 4</b></td><td><b>A conclusion for each donor</b><br><b>One more control well per donor would add the false-positive check</b></td></tr>\n'
        '</table>\n'
        '<p class="tnote">Define the groups per donor: each donor’s molecule wells are compared with its own control wells<br>The model has no donor term and treats all wells as one plate, so analyse one donor at a time<br>'
        'With 4 control wells r is rough and usually too small, which makes p too small<br>More control wells fix that (backup, slide 28)<br>Past ~1,000 nanowells per well, more nanowells barely add evidence: add wells instead</p>\n'
        '<aside>Three conditions together justify calling a molecule a source of rare responders. There must be at least three control '
        'wells and two treated wells, because with fewer the model cannot estimate how much wells differ, and without that a high well '
        'cannot be told apart from chance. There must be at least one nanowell past the cutoff among the wells compared. And the molecule’s '
        'q value, after the Benjamini–Hochberg correction across every molecule tested, must be below 0.05. For planning, five control '
        'wells allow a useful self-check: split the control wells into a mock treated group of two and a mock control group of three, '
        'run the test, and repeat for every split; since nothing separates them, every call is a false positive, and the share of calls '
        'measures the false-positive rate on the experiment’s own wells. Six or more control wells with three treated also let the '
        'supporting tests, which get their p-values by shuffling well labels, can reach smaller p-values. The six-donor plate gives each donor '
        'four control and four treated wells per molecule: enough for a conclusion, one control well short of the self-check. Groups are '
        'defined per donor, so each donor’s molecule wells are compared with that donor’s own control wells, because T-cell responses differ between people; the model here has no term for the donor or for the plate. If no control nanowell passes the cutoff, r cannot be estimated and the p-value overstates the evidence. With four control wells r is estimated only roughly and usually too small, which makes the p-value too small; more control wells give a better estimate.</aside>\n</section>\n')
open('project/slides/st_plan.html', 'w').write(html); print('st_plan', len(html))

# ================================================================ restructured steps 3-5 (new deck)
GB = '#2E8B57'
def dy(k): return 500 - k * 5.6
def _axis3(x0, x1):
    s.append(f'<path d="M{x0} {dy(70)} V{dy(0)} H{x1}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
    for k in range(0, 71, 10):
        s.append(f'<line x1="{x0 - 6}" y1="{dy(k)}" x2="{x0}" y2="{dy(k)}" stroke="{NAVY}" stroke-width="2"/>')
    L.append(lab(x0 - 58, dy(70) - 20, 48, '<br>'.join(str(k) for k in range(70, -1, -10)), 'lab small', 1, f'line-height:{5.6 * 10:.0f}px; text-align:right; color:#4A5568; font-size:20px; '))
def _dots3(cx, ks, col):
    for j, k in enumerate(ks):
        s.append(f'<circle cx="{cx - 36 + j * 24}" cy="{dy(k):.1f}" r="8" fill="{col}" stroke="{NAVY}" stroke-width="1.5"/>')
def _band3(cx, m, mu, rr, fill, dash=''):
    sd = math.sqrt(N * mu * (1 - mu) * (1 + (N - 1) * rr)); lo, hi = max(m - 2 * sd, 0), m + 2 * sd
    s.append(f'<rect x="{cx - 62}" y="{dy(hi):.1f}" width="124" height="{dy(lo) - dy(hi):.1f}" rx="8" fill="{fill}"{dash}/>')
def segue(y, sofar, nxt, i=9):
    s.append(f'<rect x="0" y="{y}" width="1664" height="74" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
    L.append(lab(16, y + 7, 1632, f'<b>So far:</b> {sofar}<br><b>Next:</b> {nxt}', 'lab left small', i, 'line-height:1.25; color:#1B2A41; font-size:21px; '))
S20 = 'line-height:1.22; color:#1B2A41; font-size:20px; '
G20 = 'line-height:1.22; color:#4A5568; font-size:20px; '
S19 = 'line-height:1.3; color:#1B2A41; font-size:19px; '
_kk8 = np.array(C + A)
_X2 = np.column_stack([np.ones(8), [0] * 4 + [1] * 4])
_fA1 = fit_bb(_kk8, [N] * 8, _X2); _fA0 = fit_bb(_kk8, [N] * 8, np.ones((8, 1)))
_mAll = float(np.mean(C + A)) / N
LL1 = float(np.sum(stats.binom.logpmf(C, N, np.mean(C) / N)) + np.sum(stats.binom.logpmf(A, N, np.mean(A) / N)))
LL0 = float(_fA0.loglik); R0 = float(_fA0.rho)
LL0r0 = float(np.sum(stats.binom.logpmf(_kk8, N, _mAll)))
LRA = 2 * (LL1 - LL0); ZA = math.sqrt(LRA)
def mn(v, d=1): return f'{v:.{d}f}'.replace('-', '−')
import json as _js0
_zs0 = np.load(_RES / 'null_gain.npy'); _c0 = _jr.loads((_RES / 'calibration.json').read_text())['designs']['4+4']
P05_44 = _c0['p05']
TCRIT_44 = _c0['tcrit']

# ---------------------------------------------------------------- 12: the model
s, L = [], []
s.append('<rect x="0" y="0" width="790" height="560" rx="18" fill="#ECF1F8"/>')
s.append('<rect x="830" y="0" width="834" height="560" rx="18" fill="#ECF1F8"/>')
L.append(lab(18, 10, 754, '<b>1. How the model says a count arises</b>', 'lab left', 1))
AX0, AX1, MUX = 440, 750, 560
DASH_ = ['none', '5 4']
for ri, ry in enumerate((70, 232)):
    s.append(f'<line x1="{AX0}" y1="{ry + 104}" x2="{AX1}" y2="{ry + 104}" stroke="{NAVY}" stroke-width="2.5"/>')
    s.append(f'<line x1="{MUX}" y1="{ry + 50}" x2="{MUX}" y2="{ry + 112}" stroke="{NAVY}" stroke-width="{5 if ri == 0 else 2}" stroke-dasharray="{DASH_[ri]}"/>')
s.append(f'<path d="M{MUX - 9} {70 + 128} h18 l-9 16 Z" fill="{NAVY}"/>')
_thx = {7: -62, 8: -41, 11: 26, 14: 92}
for j, k in enumerate(sorted(C)):
    sx = AX0 + 44 + j * 78
    s.append(f'<line x1="{MUX + _thx[k]}" y1="{232 + 114}" x2="{sx}" y2="{394 + 94}" stroke="#7A8699" stroke-width="2" stroke-dasharray="4 4"/>')
    s.append(f'<circle cx="{MUX + _thx[k]}" cy="{232 + 104}" r="9" fill="{CTRL}" stroke="{NAVY}" stroke-width="2"/>')
    for m in range(k):
        s.append(f'<circle cx="{sx - 20 + (m % 5) * 10}" cy="{394 + 124 - (m // 5) * 10}" r="4.2" fill="{OR}"/>')
s.append(f'<path d="M{MUX - 62} {232 + 62} v-10 H{MUX + 92} v10" stroke="{PUR}" stroke-width="3" fill="none"/>')
s.append(f'<line x1="{AX0}" y1="{394 + 132}" x2="{AX1}" y2="{394 + 132}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(18, 62, 410, '<b>A group has one rate, π</b><br>The chance that a nanowell passes the cutoff<br>The same π as on slide 10<br>Control wells share one π, A wells another', 'lab left small', 1, S20))
L.append(lab(MUX - 40, 84, 80, '<b>π</b>', 'lab small', 1))
L.append(lab(18, 218, 410, '<b>Each well gets its own rate, π<sub>w</sub></b><br>π<sub>w</sub> scatters around the group’s π<br><b style="color:#6B3FB0">r</b> sets how wide the scatter is<br>π<sub>w</sub> ~ Beta(mean π, r)<br>π<sub>w</sub> is never estimated: the model averages over it', 'lab left small', 2, S20))
L.append(lab(MUX - 60, 250, 260, '<b style="color:#6B3FB0">width set by r</b>', 'lab small', 2, 'font-size:20px; '))
L.append(lab(18, 386, 410, '<b>The well’s count, K<sub>w</sub></b><br>Each of its n = 1,000 nanowells passes with chance π<sub>w</sub><br>Counting adds counting noise<br>K<sub>w</sub> ~ Binomial(n, π<sub>w</sub>)', 'lab left small', 3, S20))
L.append(lab(AX0, 394 + 136, 310, 'control wells: K = 7, 8, 11, 14', 'lab small', 3, 'font-size:19px; color:#4A5568; '))
P2 = 830
_axis3(P2 + 110, P2 + 800)
_mc, _ma, _mb = np.mean(C), np.mean(A), np.mean(B)
_band3(P2 + 560, _mb, _mb / N, 0.0079, 'none', f' stroke="{GB}" stroke-width="2.5" stroke-dasharray="8 6"')
for cx, ks, col, m in ((P2 + 240, C, CTRL, _mc), (P2 + 400, A, PUR, _ma), (P2 + 560, B, GB, _mb)):
    _band3(cx, m, m / N, 0, '#C9D6E8')
    s.append(f'<line x1="{cx - 62}" y1="{dy(m):.1f}" x2="{cx + 62}" y2="{dy(m):.1f}" stroke="{NAVY}" stroke-width="4"/>')
    _dots3(cx, ks, col)
L.append(lab(P2 + 18, 10, 800, '<b>2. What π and r look like in the data</b>', 'lab left', 4))
L.append(lab(P2 + 18, 50, 800, 'y-axis: K per well, dot: one well, bar: the wells’ plain average K<br>Shaded: where counting noise alone (r = 0) puts 95&#160;% of wells', 'lab left small', 4, 'line-height:1.15; color:#4A5568; font-size:19px; '))
L.append(lab(P2 + 130, 226, 330, '<b>control</b> and <b style="color:#8E5BD0">A</b><br>All 8 wells inside the bands<br>→ <b>r ≈ 0</b>', 'lab left small', 5, S20))
L.append(lab(P2 + 630, 110, 200, f'<b style="color:{GB}">B</b><br>Average 24, as A<br>64 far above<br>9, 10, 13 below<br>→ r = 0.008<br><span style="color:{GB}"><b>Dashed box</b>: where<br>95&#160;% of B’s wells<br>should fall once wells<br>may differ (r = 0.008)</span>', 'lab left small', 5, S20))
for _cx, _lb in ((P2 + 240, '<b>control</b>'), (P2 + 400, '<b style="color:#8E5BD0">A</b>'), (P2 + 560, '<b style="color:#2E8B57">B</b>')):
    L.append(lab(_cx - 60, 506, 120, _lb, 'lab small', 4, 'font-size:20px; '))
L.append(lab(0, 568, 1664, '<b>Three unknowns when A is tested against control:</b> π for control, π for A, and one r shared by both groups<br>'
          'Why one r: 4 wells per group are too few to estimate a scatter for each. The values of r shown here are results of the fit (slides 14 to 17)'.replace('. The', '<br>The'), 'lab left small', 6, 'color:#1B2A41; line-height:1.22; font-size:21px; '))
segue(650, 'counts differ between wells by counting noise and by well-to-well scatter (slide 10)', 'the two π are rewritten as α and δ, so that “no effect” becomes δ = 0 (slide 13)')
slide('n_model', 'The statistics · step 3 · the model', 'A rate per group, scatter per well',
      svg('Left, three stages top to bottom: a group’s single rate π as a bar on a rate axis; each well’s own rate as four dots scattered around π, the width of the scatter set by r; '
          'and the four control wells’ counts as stacks of orange dots, drawn lowest first, 7, 8, 11 and 14, each joined to its well’s rate, so a higher rate leads to a taller stack. Right, the wells’ counts for control, A and B on a K axis from 0 to 70, each group with a bar at its average '
          'and a shaded band for counting noise alone; control and A wells lie inside their bands, B’s well at 64 far outside. A dashed box around B shows the wider range, from 0 to about 53, where 95 percent of B’s wells should fall once wells are allowed to differ with r = 0.008.', s, h=730), L,
      'Before any fitting, this slide states the model in words and in a picture. A group of replicate wells, the control wells or the wells given molecule A, has one underlying rate, π: the chance that a nanowell passes the cutoff, the same π as on slide 10. '
      'Wells are never identical, so each well gets its own rate, π<sub>w</sub>, scattered around the group’s π; the single number r says how wide that scatter is. Finally the well’s count K is what we observe: each of its thousand nanowells passes with chance π<sub>w</sub>, '
      'and that step adds counting noise. π<sub>w</sub> itself is never estimated; the beta-binomial of slide 10 is this two-stage story with π<sub>w</sub> averaged out. '
      'The right panel shows what the two kinds of unknown look like in data. π sets where the group’s wells sit on average, the bar. r sets how far wells may stray from it: the shaded band is where counting noise alone would put 95 percent of wells. '
      'All control and A wells are inside their bands, so their data ask for no extra scatter and r comes out near zero. B has the same average as A, 24, but one well at 64 and three near 10, far more scatter than counting noise, so B’s test needs r = 0.008. The dashed box is the same kind of range as the shaded band, redrawn for that r: where 95 percent of B’s wells should fall when wells differ that much. It is several times taller than the shaded band, and the well at 64 is still above it. '
      'When A is tested against control there are three unknowns: π for control, π for A, and one r shared by both; with four wells per group there is too little information to estimate a separate scatter for each. '
      'The values of r quoted here are results of the fit that the next slides build up.', steps={3})

# ---------------------------------------------------------------- 13: log-odds, beta0 and beta1
s, L = [], []
s.append('<rect x="0" y="0" width="800" height="560" rx="18" fill="#ECF1F8"/>')
s.append('<rect x="840" y="0" width="824" height="560" rx="18" fill="#ECF1F8"/>')
_pc, _pa = np.mean(C) / N, np.mean(A) / N
_lc, _la = math.log(_pc / (1 - _pc)), math.log(_pa / (1 - _pa))
def rx_(p): return 90 + p / 0.04 * 640
def lx_(v): return 90 + (v + 5.0) / 2.0 * 640
RYA, LYA = 190, 390
s.append(f'<line x1="90" y1="{RYA}" x2="730" y2="{RYA}" stroke="{NAVY}" stroke-width="3"/><line x1="90" y1="{LYA}" x2="730" y2="{LYA}" stroke="{NAVY}" stroke-width="3"/>')
for pv in (0, 0.01, 0.02, 0.03, 0.04):
    s.append(f'<line x1="{rx_(pv):.1f}" y1="{RYA}" x2="{rx_(pv):.1f}" y2="{RYA + 9}" stroke="{NAVY}" stroke-width="2.5"/>')
for v in (-5, -4.5, -4, -3.5, -3):
    s.append(f'<line x1="{lx_(v):.1f}" y1="{LYA}" x2="{lx_(v):.1f}" y2="{LYA + 9}" stroke="{NAVY}" stroke-width="2.5"/>')
for pv, lv, col, fc in ((_pc, _lc, NAVY, CTRL), (_pa, _la, PUR, PUR)):
    s.append(f'<path d="M{rx_(pv):.1f} {RYA + 12} C {rx_(pv):.1f} {RYA + 110}, {lx_(lv):.1f} {LYA - 110}, {lx_(lv):.1f} {LYA - 14}" stroke="{col}" stroke-width="2.5" stroke-dasharray="7 5" fill="none"/>')
    s.append(f'<path d="M{lx_(lv) - 8:.1f} {LYA - 26} h16 l-8 14 Z" fill="{col}"/>')
    s.append(f'<circle cx="{rx_(pv):.1f}" cy="{RYA}" r="10" fill="{fc}" stroke="{NAVY}" stroke-width="2"/><circle cx="{lx_(lv):.1f}" cy="{LYA}" r="10" fill="{fc}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<path d="M{lx_(_lc):.1f} {LYA + 52} v10 H{lx_(_la):.1f} v-10" stroke="#2F6BDB" stroke-width="3.5" fill="none"/>')
L.append(lab(18, 10, 764, '<b>1. The same two rates on two scales</b>', 'lab left', 1))
L.append(lab(18, 54, 764, 'Top axis: <b>rate π</b>, the % of nanowells past the cutoff<br>Bottom axis: <b>log-odds</b>, ln[π ÷ (1 − π)]', 'lab left small', 1, G20))
L.append(lab(18, RYA - 44, 150, '<b>Rate π (%)</b>', 'lab left small', 1, S20))
L.append(lab(int(rx_(_pc)) - 100, RYA - 78, 200, '<b>control</b><br>1.0&#160;%', 'lab small', 1, 'line-height:1.15; font-size:21px; '))
L.append(lab(int(rx_(_pa)) - 100, RYA - 78, 200, '<b style="color:#8E5BD0">A</b><br>2.4&#160;%', 'lab small', 1, 'line-height:1.15; font-size:21px; '))
L.append(lab(66, RYA + 12, 48, '0&#160;%', 'lab small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(int(rx_(0.04)) - 24, RYA + 12, 60, '4&#160;%', 'lab small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(int(rx_(0.02)) - 30, RYA + 12, 60, '2&#160;%', 'lab small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(int(lx_(-4)) - 30, LYA + 12, 60, '−4', 'lab small', 2, 'font-size:19px; color:#4A5568; '))
L.append(lab(18, LYA - 62, 200, '<b>Log-odds</b><br>ln[π ÷ (1 − π)]', 'lab left small', 2, S20))
L.append(lab(60, LYA + 12, 60, '−5', 'lab small', 2, 'font-size:19px; color:#4A5568; '))
L.append(lab(int(lx_(-3)) - 30, LYA + 12, 60, '−3', 'lab small', 2, 'font-size:19px; color:#4A5568; '))
L.append(lab(int(lx_(_lc)) - 90, LYA + 14, 180, '<b>α = −4.60</b>', 'lab small', 2, 'font-size:21px; '))
L.append(lab(int(lx_(_la)) - 110, LYA + 14, 220, '<b style="color:#6B3FB0">α + δ = −3.71</b>', 'lab small', 2, 'font-size:21px; '))
L.append(lab(int((lx_(_lc) + lx_(_la)) / 2) - 210, LYA + 66, 420, '<b style="color:#2F6BDB">δ = 0.89, the gap = ln(odds ratio 2.4)</b>', 'lab small', 2, 'font-size:21px; '))
L.append(lab(18, 478, 764, '<b>Why this scale</b><br>“No effect” is exactly δ = 0, whatever the control rate is<br>Any values of α and δ give valid rates between 0 and 1', 'lab left small', 3, S20))
TX, TY3, LH3 = 858, 62, 40
for i in range(4):
    s.append(f'<rect x="{TX - 8}" y="{TY3 + LH3 * (i + 1) + 3}" width="796" height="{LH3 - 4}" rx="6" fill="{"#DCE6F2" if i % 2 == 0 else "#E6ECF5"}"/>')
L.append(lab(858, 10, 790, '<b>2. Worked for the two groups</b>', 'lab left', 4))
L.append(lab(TX, TY3, 360, '<br>'.join(['&#160;', 'average K', 'rate π = average K ÷ n', 'odds = pass ÷ not pass = π ÷ (1 − π)', 'log-odds = ln(odds)']), 'lab left small', 4, f'line-height:{LH3}px; color:#1B2A41; font-size:21px; white-space:nowrap; '))
L.append(lab(TX + 380, TY3, 180, '<br>'.join(['<b>control</b>', '10', '1.0&#160;%', '0.0101', '<b>−4.60</b>']), 'lab small', 4, f'line-height:{LH3}px; color:#1B2A41; font-size:21px; '))
L.append(lab(TX + 580, TY3, 180, '<br>'.join(['<b style="color:#8E5BD0">A</b>', '24', '2.4&#160;%', '0.0246', '<b>−3.71</b>']), 'lab small', 4, f'line-height:{LH3}px; color:#1B2A41; font-size:21px; '))
s.append(f'<rect x="{TX - 8}" y="288" width="796" height="256" rx="14" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
L.append(lab(TX + 8, 296, 770, '<b>The model’s line for well w</b><br><span style="font-size:24px">ln[π ÷ (1 − π)] = α + δ × x<sub>w</sub></span>', 'lab left small', 5, 'line-height:1.35; color:#1B2A41; font-size:21px; '))
L.append(lab(TX + 8, 366, 770, 'π: the mean rate of the group that well w belongs to<br>x<sub>w</sub> = 0 for a control well, 1 for a well given the molecule<br>α: the control’s log-odds, the baseline (it is in both fits)<br>δ: the gap, A’s log-odds minus the control’s: −3.71 − (−4.60) = 0.89<br>'
          'δ = ln(odds ratio): A’s odds of passing are exp(0.89) = 2.4 times the control’s<br>δ above 0: A is higher than control, and below 0: lower<br><b>If A does nothing, δ = 0 (odds ratio 1): that is what the test will ask</b>', 'lab left small', 5, 'line-height:1.24; color:#1B2A41; font-size:19px; '))
segue(580, 'the model has a rate π per group and one scatter r (slide 12)', 'α, δ and r are unknown, so we need a way to score any guess of them against the 8 counts (slide 14)')
slide('n_logodds', 'The statistics · step 3 · the scale', 'Rates as log-odds: α and δ',
      svg('Left, two parallel axes. Top, the rate axis from 0 to 4 percent with the control at 1.0 percent and molecule A at 2.4 percent. Bottom, the log-odds axis from −5 to −3, '
          'where the same two groups sit at −4.60, called α, and −3.71, α plus δ; dashed arrows join each rate to its log-odds, and a bracket between the two log-odds marks δ = 0.89. '
          'Right, a table taking each group from its average count to a rate, to odds, to log-odds, and the model’s equation.', s, h=664), L,
      'The model’s two rates are not fitted directly; they are written on the log-odds scale, and this slide shows that this is only a change of scale. Take the control: on average 10 of 1,000 nanowells pass, a rate of 1.0 percent. '
      'Its odds are the rate divided by one minus the rate, 0.0101, about 1 to 99, and the natural log of that is −4.60. That number is called α. Molecule A: 24 of 1,000, 2.4 percent, odds 0.0246, log-odds −3.71. '
      'The difference between the two log-odds, 0.89, is δ, the gap. So the model line reads: the log-odds of well w’s rate equals α plus δ times x for well i, where that x is 0 for a control well and 1 for a well given the molecule, and π-w is simply the rate of the group the well belongs to. '
      'Why this scale? Because “the molecule does nothing” becomes the single statement δ = 0, whatever the control rate happens to be, and because any values of α and δ correspond to valid rates between 0 and 1, so the fitting can move them freely. '
      'A difference of log-odds is a ratio of odds: e to the 0.89 is 2.43, so A’s odds of a nanowell passing are about 2.4 times the control’s. The test will ask whether the data are compatible with δ = 0.', steps={3})

# ---------------------------------------------------------------- 14: scoring a guess, the likelihood
s, L = [], []
PXn = [0, 562, 1124]; PWn = 540; KB = 300
def kx_(px, k): return px + 60 + k / 40 * 450
def py_(p): return KB - p * 1450
_g_good = (np.mean(C) / N, np.mean(A) / N)
for pi_, px in enumerate(PXn):
    s.append(f'<rect x="{px}" y="0" width="{PWn}" height="340" rx="18" fill="#ECF1F8"/>')
    s.append(f'<path d="M{kx_(px, 0)} {py_(0.135):.1f} V{KB} H{kx_(px, 40)}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
    for k in (0, 10, 20, 30, 40):
        s.append(f'<line x1="{kx_(px, k)}" y1="{KB}" x2="{kx_(px, k)}" y2="{KB + 8}" stroke="{NAVY}" stroke-width="2"/>')
    for k, tl in ((0, '0'), (40, '40')):
        L.append(lab(int(kx_(px, k)) - 24, KB + 8, 48, tl, 'lab small', 1 + pi_, 'font-size:19px; color:#4A5568; '))
    L.append(lab(int(kx_(px, 20)) - 170, KB + 8, 340, '<b>K</b>, nanowells past the cutoff in a well', 'lab small', 1 + pi_, 'font-size:19px; color:#1B2A41; '))
    for p_ in (0.05, 0.10):
        s.append(f'<line x1="{kx_(px, 0) - 8}" y1="{py_(p_):.1f}" x2="{kx_(px, 0)}" y2="{py_(p_):.1f}" stroke="{NAVY}" stroke-width="2"/>')
    if pi_ < 2:
        s.append(f'<path d="M{px + PWn + 4} 160 h8 v-9 l12 15 l-12 15 v-9 h-8 Z" fill="{NAVY}"/>')
def _curve(px, mu, col):
    d_ = stats.binom(N, mu)
    s.append('<path d="M' + ' L'.join(f'{kx_(px, k):.1f} {py_(d_.pmf(k)):.1f}' for k in range(0, 41)) + f'" stroke="{col}" stroke-width="3.5" fill="none"/>')
    return d_
_d = _curve(PXn[0], _g_good[0], NAVY)
s.append(f'<line x1="{kx_(PXn[0], 7)}" y1="{KB}" x2="{kx_(PXn[0], 7)}" y2="{py_(_d.pmf(7)):.1f}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="4 4"/>')
s.append(f'<line x1="{kx_(PXn[0], 0)}" y1="{py_(_d.pmf(7)):.1f}" x2="{kx_(PXn[0], 7)}" y2="{py_(_d.pmf(7)):.1f}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="4 4"/>')
s.append(f'<circle cx="{kx_(PXn[0], 7)}" cy="{py_(_d.pmf(7)):.1f}" r="9" fill="{OR}" stroke="{NAVY}" stroke-width="2"/>')
for mu, ks, col, fc in ((_g_good[0], C, NAVY, CTRL), (_g_good[1], A, PUR, PUR)):
    d_ = _curve(PXn[1], mu, col)
    for k in ks:
        s.append(f'<line x1="{kx_(PXn[1], k):.1f}" y1="{KB}" x2="{kx_(PXn[1], k):.1f}" y2="{py_(d_.pmf(k)):.1f}" stroke="{col}" stroke-width="1.5" stroke-dasharray="3 4"/>')
        s.append(f'<circle cx="{kx_(PXn[1], k):.1f}" cy="{py_(d_.pmf(k)):.1f}" r="7.5" fill="{fc}" stroke="{NAVY}" stroke-width="1.5"/>')
d_ = _curve(PXn[2], _mAll, '#5A6679')
for ks, fc in ((C, CTRL), (A, PUR)):
    for k in ks:
        s.append(f'<line x1="{kx_(PXn[2], k):.1f}" y1="{KB}" x2="{kx_(PXn[2], k):.1f}" y2="{py_(d_.pmf(k)):.1f}" stroke="#5A6679" stroke-width="1.5" stroke-dasharray="3 4"/>')
        s.append(f'<circle cx="{kx_(PXn[2], k):.1f}" cy="{py_(d_.pmf(k)):.1f}" r="7.5" fill="{fc}" stroke="{NAVY}" stroke-width="1.5"/>')
L.append(lab(18, 8, 510, '<b>1. One well, one guess</b><br><span style="font-size:19px; color:#4A5568">If π = 1.0&#160;% and r = 0<br><b style="color:#1B2A41">P</b>, the chance of that K</span>', 'lab left', 1, 'line-height:1.25; '))
_d2 = stats.binom(N, _g_good[1])
s.append('<path d="M' + ' L'.join(f'{kx_(PXn[0], k):.1f} {py_(_d2.pmf(k)):.1f}' for k in range(0, 41)) + f'" stroke="{PUR}" stroke-width="2.5" fill="none" stroke-dasharray="7 5"/>')
s.append(f'<circle cx="{kx_(PXn[0], 7)}" cy="{py_(_d2.pmf(7)):.1f}" r="7" fill="#FFFFFF" stroke="{PUR}" stroke-width="3"/>')
L.append(lab(int(kx_(0, 15)), 96, 330, f'<b style="color:#A9501A">control 1 had K = 7</b><br>Guess 1.0&#160;% (solid): <b>P = {_d.pmf(7):.3f}</b><br><span style="color:#6B3FB0">Guess 2.4&#160;% (dashed): <b>P = {_d2.pmf(7):.5f}</b></span>', 'lab left small', 1, 'line-height:1.22; color:#1B2A41; font-size:18.5px; '))
L.append(lab(PXn[1] + 18, 8, 510, '<b>2. All 8 wells, a good guess</b><br><span style="font-size:19px; color:#4A5568">Guess: control 1.0&#160;%, A 2.4&#160;%, r = 0<br><b style="color:#1B2A41">P</b>, the chance of that K</span>', 'lab left', 2, 'line-height:1.25; '))
L.append(lab(PXn[1] + 330, 96, 200, f'Every dot sits high<br>on its curve<br><b>log L = {mn(LL1)}</b>', 'lab left small', 2, S20))
L.append(lab(PXn[2] + 18, 8, 510, '<b>3. The same wells, a one-rate guess</b><br><span style="font-size:19px; color:#4A5568">Guess: 1.7&#160;% for all 8 wells, r = 0<br><b style="color:#1B2A41">P</b>, the chance of that K</span>', 'lab left', 3, 'line-height:1.25; '))
L.append(lab(PXn[2] + 330, 96, 200, f'One curve for all:<br>the dots sit low<br><b>log L = {mn(LL0r0)}</b>', 'lab left small', 3, S20))
for _px in PXn:
    L.append(lab(_px + 2, int(py_(0.10) - (py_(0.05) - py_(0.10)) / 2), 46, '0.10<br>0.05<br>0', 'lab small', 1, f'font-size:19px; color:#4A5568; text-align:right; line-height:{py_(0.05) - py_(0.10):.1f}px; '))
EYn = 354
for px in PXn:
    s.append(f'<rect x="{px}" y="{EYn}" width="{PWn}" height="{246}" rx="18" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
    s.append(f'<path d="M{px + PWn // 2 - 14} {EYn - 2} l14 -14 l14 14 Z" fill="#2F6BDB"/>')
_Pw = 'P(K<sub>w</sub> | π, r)'
L.append(lab(18, EYn + 8, 506, f'<b>What a likelihood is</b><br>The chance of the count we saw, if a guess were true<br>The count stays fixed (7) and the guess changes<br>Guess 1.0&#160;%: P = {_d.pmf(7):.3f}, a plausible guess<br>'
          f'Guess 2.4&#160;%: P = {_d2.pmf(7):.5f}, a poor guess<br><b>It scores the guess, not the nanowells</b><br>With r = 0, P = C(n, K) × πᴷ × (1 − π)ⁿ⁻ᴷ<br>With r &gt; 0 the curve is wider (slide 16)', 'lab left small', 4, 'line-height:1.26; color:#1B2A41; font-size:19px; '))
_lps = stats.binom.logpmf(C, N, _g_good[0]).tolist() + stats.binom.logpmf(A, N, _g_good[1]).tolist()
# box 2 as a picture: one row per well, a bar for its chance, its log beside it; multiply the chances or add the logs
_RP = 20.5; _bx2 = PXn[1]
_rows = [f'{"control" if j < 4 else "A"} {j % 4 + 1}: K = {k}, P = {math.exp(lp):.3f}' for j, (k, lp) in enumerate(zip(C + A, _lps))]
for j, lp in enumerate(_lps):
    _yc = EYn + 6 + (j + 1.5) * _RP
    s.append(f'<rect x="{_bx2 + 262}" y="{_yc - 6:.1f}" width="{math.exp(lp) * 1400:.1f}" height="12" rx="3" fill="{CTRL if j < 4 else PUR}" stroke="{NAVY}" stroke-width="1"/>')
L.append(lab(_bx2 + 18, EYn + 6, 506, '<br>'.join(['<b>From 8 chances to one score</b>'] + _rows + [f'Multiply the 8 chances: L ≈ {math.exp(sum(_lps)):.9f}', f'<b>Add the 8 logs: log L = {mn(sum(_lps))}</b>']), 'lab left small', 5, f'line-height:{_RP}px; color:#1B2A41; font-size:16.5px; white-space:nowrap; '))
L.append(lab(_bx2 + 448, EYn + 6, 74, '<br>'.join(['<b>ln P</b>'] + [mn(x, 2) for x in _lps]), 'lab small', 5, f'line-height:{_RP}px; color:#1B2A41; font-size:16.5px; text-align:right; '))
# box 3: the two guesses' scores on one log L line
_bx3 = PXn[2]; _ax3 = EYn + 212
def _lx3(v): return _bx3 + 40 + (v + 36) / 22 * 460
s.append(f'<line x1="{_bx3 + 30}" y1="{_ax3}" x2="{_bx3 + 506}" y2="{_ax3}" stroke="{NAVY}" stroke-width="2.5"/><path d="M{_bx3 + 504} {_ax3 - 7} l12 7 l-12 7 Z" fill="{NAVY}"/>')
s.append(f'<circle cx="{_lx3(LL0r0):.1f}" cy="{_ax3}" r="9" fill="#9AA5B5" stroke="{NAVY}" stroke-width="2"/><circle cx="{_lx3(sum(_lps)):.1f}" cy="{_ax3}" r="9" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
L.append(lab(_bx3 + 18, EYn + 8, 506, f'<b>Comparing guesses</b><br>The same 8 counts, scored under two guesses<br><b style="color:#6B3FB0">Purple</b>: control 1.0&#160;%, A 2.4&#160;% → log L = {mn(LL1)}<br><b style="color:#5A6679">Grey</b>: one rate, 1.7&#160;% → log L = {mn(LL0r0)}<br>'
          '<b>log L is the score of a guess</b><br>Not the log-odds of slide 13: that is where a group sits', 'lab left small', 6, S19))
L.append(lab(_bx3 + 18, _ax3 + 8, 506, 'log L → nearer 0 means the guess explains the counts better', 'lab small', 6, 'font-size:17px; color:#4A5568; '))
segue(612, 'the unknowns are α, δ and r, and any guess of them gives each group a mean rate π (slide 13)', 'find the guess with the highest log L: maximum likelihood (slide 15)')
slide('n_lik', 'The statistics · step 3 · scoring a guess', 'Scoring a guess: the log-likelihood',
      svg('Three panels with K from 0 to 40 on the horizontal axis and the chance P on the vertical. 1: the curve of chances for one well if the rate were 1.0 percent; control well 1, K = 7, is marked on it at height 0.090. '
          '2: two curves, control at 1.0 percent and A at 2.4 percent, with the eight wells as dots high on their curves; the log-likelihood is −20.2. '
          f'3: a guess with one rate, 1.7 percent, for all eight wells: one curve between the two groups, the dots low on its flanks, and the log-likelihood falls to {LL0r0:.1f}. Below each panel, its formula.', s, h=696), L,
      'The model has three unknown numbers, α, δ and r. To find them we need a way to say how good any particular guess is, and that is the likelihood. Panel 1 takes one well and one guess. '
      'If the control rate were 1.0 percent and r were zero, the chance of each possible count for a well of 1,000 nanowells is the binomial curve drawn here. Control well 1 actually had K = 7; the height of the curve at 7 is 0.090. '
      'That height is written P of K given π and r, each for well i: the chance of exactly that count if the guess were true. Panel 2 does the same for all eight wells under a good guess, 1.0 percent for control and 2.4 percent for A. '
      'A likelihood is the chance of the data we actually saw, computed under a guess; the count stays fixed and the guess changes. The dashed curve in panel 1 is a second guess, a rate of 2.4 percent: under it a count of 7 has a chance of only about 0.00003, so that guess scores far worse for this well. A likelihood is therefore a score for a guess, not a property of the nanowells, and it is a different thing from the log-odds of slide 13. ' 'Each well is a dot on its group’s curve. The wells are independent, so the chance of all eight counts together is the product of the eight heights; that product is the likelihood of the guess. '
      'It is a tiny number, around ten to the minus nine, so we take logarithms, which turns the product into a sum in which each well’s share is visible. That sum is the log-likelihood, log L, here −20.2. '
      f'Panel 3 scores a different guess: no gap, one rate of 1.7 percent for all eight wells. One curve now has to serve both groups; the control wells sit low on its left flank and the A wells low on its right, and log L falls to {LL0r0:.1f}. '
      'So log L is a score: the higher it is, the nearer to zero, the better the guess explains the eight counts. The no-gap guess is not just an illustration: it comes back as step 4 of the algorithm.', steps={3})

# ---------------------------------------------------------------- 15: maximum likelihood
s, L = [], []
_prof = lambda b: float(LL1) if abs(b - B1) < 1e-9 else float(np.interp(b, _bs, _pl))
s.append('<rect x="0" y="0" width="820" height="560" rx="18" fill="#ECF1F8"/>')
s.append('<rect x="860" y="0" width="804" height="560" rx="18" fill="#ECF1F8"/>')
def hx_(b): return 190 + (b + 0.1) / 1.7 * 590
def hy_(v): return 470 - (v + 29.5) / 10 * 330
s.append(f'<path d="M190 {hy_(-19.5):.1f} V470 H780" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
for b in (0, 0.5, 1.0, 1.5):
    s.append(f'<line x1="{hx_(b):.1f}" y1="470" x2="{hx_(b):.1f}" y2="479" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(int(hx_(b)) - 30, 480, 60, f'{b:g}', 'lab small', 1, 'font-size:19px; color:#4A5568; '))
for v in (-28, -24, -20):
    s.append(f'<line x1="181" y1="{hy_(v):.1f}" x2="190" y2="{hy_(v):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(100, int(hy_(-20) - (hy_(-24) - hy_(-20)) / 2), 78, '<br>'.join(['−20', '−24', '−28']), 'lab small', 1, f'line-height:{(hy_(-24) - hy_(-20)):.1f}px; text-align:right; font-size:19px; color:#4A5568; '))
s.append('<path d="M' + ' L'.join(f'{hx_(b):.1f} {hy_(v):.1f}' for b, v in zip(_bs, _pl)) + f'" stroke="{NAVY}" stroke-width="4" fill="none"/>')
for b in (0.25, 0.45, 0.65):
    s.append(f'<circle cx="{hx_(b):.1f}" cy="{hy_(_prof(b)):.1f}" r="7" fill="#5A6679"/>')
    s.append(f'<path d="M{hx_(b + 0.04):.1f} {hy_(_prof(b + 0.04)) - 16:.1f} l18 -13" stroke="#5A6679" stroke-width="3"/><path d="M{hx_(b + 0.04) + 23:.1f} {hy_(_prof(b + 0.04)) - 33:.1f} l-13 3 l8 10 Z" fill="#5A6679"/>')
s.append(f'<line x1="{hx_(B1):.1f}" y1="{hy_(LL1):.1f}" x2="{hx_(B1):.1f}" y2="470" stroke="{PUR}" stroke-width="2" stroke-dasharray="5 4"/>')
s.append(f'<line x1="{hx_(-0.06):.1f}" y1="{hy_(LL1):.1f}" x2="{hx_(B1):.1f}" y2="{hy_(LL1):.1f}" stroke="#2F6BDB" stroke-width="2" stroke-dasharray="5 4"/>')
_ax = hx_(-0.06)
s.append(f'<line x1="{_ax:.1f}" y1="{hy_(LL1) + 12:.1f}" x2="{_ax:.1f}" y2="{hy_(LL0) - 12:.1f}" stroke="#2F6BDB" stroke-width="3.5"/>'
         f'<path d="M{_ax - 8:.1f} {hy_(LL1) + 14:.1f} l8 -14 l8 14 Z" fill="#2F6BDB"/><path d="M{_ax - 8:.1f} {hy_(LL0) - 14:.1f} l8 14 l8 -14 Z" fill="#2F6BDB"/>')
s.append(f'<circle cx="{hx_(B1):.1f}" cy="{hy_(LL1):.1f}" r="11" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<circle cx="{hx_(0):.1f}" cy="{hy_(LL0):.1f}" r="10" fill="#FFFFFF" stroke="{NAVY}" stroke-width="3"/>')
L.append(lab(18, 10, 790, '<b>1. Try every gap δ, keep the highest log L</b>', 'lab left', 1))
L.append(lab(18, 50, 790, 'Each point: the best log L that α and r can reach at that δ', 'lab left small', 1, G20))
L.append(lab(int(hx_(B1)) + 20, int(hy_(LL1)) - 66, 330, f'<b style="color:#6B3FB0">Step 3: the top (δ = {B1:.2f}, r ≈ 0)</b><br><b style="color:#6B3FB0">log L = {mn(LL1)}</b>', 'lab left small', 2, 'line-height:1.15; font-size:20px; '))
L.append(lab(int(hx_(0)) + 26, int(hy_(LL0)) - 16, 270, f'<b>Step 4: a separate search</b><br><b>δ fixed at 0, r = {R0:.4f}</b><br><b>log L = {mn(LL0)}</b>', 'lab left small', 2, 'line-height:1.15; font-size:20px; '))
L.append(lab(8, int((hy_(LL1) + hy_(LL0)) / 2) - 40, 86, f'<b style="color:#2F6BDB">rise in<br>log L:<br>{LL1 - LL0:.2f}</b>', 'lab small', 2, 'line-height:1.15; font-size:20px; '))
L.append(lab(int(hx_(0.95)), int(hy_(-25.0)), 200, 'the search<br>climbs uphill', 'lab left small', 2, 'line-height:1.15; font-size:20px; color:#5A6679; '))
L.append(lab(190, 512, 590, 'x-axis: <b>δ, the gap</b>, y-axis: <b>log L</b>', 'lab small', 1, 'font-size:20px; color:#1B2A41; '))
L.append(lab(878, 10, 770, '<b>2. What the search returns</b>', 'lab left', 3))
L.append(lab(878, 52, 770, '<span style="font-size:24px">best α, δ, r = arg max log L(α, δ, r)</span><br>arg max: the values that give the highest log L', 'lab left small', 3, 'line-height:1.35; color:#1B2A41; font-size:20px; '))
L.append(lab(878, 132, 770, f'<b>For A against control (step 3)</b><br>α = {mn(B0, 2)}, a control rate of 1.0&#160;% (average K 10)<br>α + δ = {mn(B0 + B1, 2)}, an A rate of 2.4&#160;% (average K 24)<br>δ = {B1:.2f}<br>r ≈ 0<br>log L at the top = {mn(LL1)}', 'lab left small', 4, 'line-height:1.3; color:#1B2A41; font-size:20px; '))
L.append(lab(878, 300, 770, '<b>Why these values</b><br>With r ≈ 0, a group’s best π is simply its average K ÷ n<br>r ≈ 0 because neither group’s K varies beyond what counting noise gives<br>Control: variance of K 10.0, counting-noise variance 9.9<br>A: variance of K 8.7, counting-noise variance 23', 'lab left small', 5, 'line-height:1.3; color:#1B2A41; font-size:20px; '))
s.append('<rect x="870" y="438" width="784" height="112" rx="14" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
L.append(lab(886, 444, 760, '<b>How the computer searches</b><br>1. Start from a guess of α, δ, r and compute log L (slide 14)<br>2. Nudge the three numbers in the direction that raises log L<br>3. Stop when no nudge raises it', 'lab left small', 6, 'line-height:1.22; color:#1B2A41; font-size:19px; '))
L.append(lab(0, 572, 1664, f'<b>Two searches, two results:</b> step 3 with δ free (log L = {mn(LL1)}) and step 4 with δ fixed at 0 (log L = {mn(LL0)})', 'lab left small', 7, 'color:#1B2A41; '))
segue(616, 'log L scores any guess of α, δ and r (slide 14)', 'the two fits side by side for every well (slide 16), and why step 4’s r is not 0 (slide 17)')
slide('n_mle', 'The statistics · steps 3–4 · the search', 'Maximum likelihood: the best guess',
      svg('Left, a hill: the log-likelihood on the vertical axis against the gap δ on the horizontal axis, from 0 to 1.5. The curve rises from −27.4 at δ = 0, an open circle labelled step 4, to a peak of −20.2 at δ = 0.89, a purple dot labelled step 3, and falls again; '
          'grey dots with arrows show a search climbing the slope, and a double arrow marks the rise of 7.12 in log L between the two heights. Right, what the search returns for A against control, why, and the search in three steps.', s, h=700), L,
      'With a score in hand, fitting is a search: find the guess of α, δ and r with the highest log-likelihood. That is all maximum likelihood means. The left panel shows the search along the number we care about, the gap δ. '
      'Each point of the curve is the best log L that the other two numbers, α and r, can reach at that value of δ; plotted against δ this is a hill. Its top is at δ = 0.89, where log L is −20.2. '
      'The computer does not try every value; it starts from a guess, computes log L, nudges the three numbers in the direction that raises it, and stops when no nudge helps. '
      'For A against control the search returns α = −4.60, a control rate of 1.0 percent, and α + δ = −3.71, an A rate of 2.4 percent. These are just the group averages, 10 and 24 of 1,000: when r is near zero, the best rate for a group is its average count over n. '
      'And r comes out near zero because neither group’s counts vary beyond what counting noise gives: the control counts have a variance of 10.0 against 9.9 expected from counting alone, about what counting noise alone gives, and A’s 8.7 against 23. '
      'Step 4 of the algorithm is a second, separate search with one restriction: δ is held at zero, so all eight wells share one rate. Its result is the open circle at the left end of the hill, log L = −27.4, and there r is no longer zero but 0.0027; slide 17 explains why. '
      'The difference in height between the two results, 7.12, is what the test will use.', steps={3, 4})

# ---------------------------------------------------------------- 17: why r rises in the one-group fit
s, L = [], []
s.append('<rect x="0" y="0" width="1030" height="548" rx="18" fill="#ECF1F8"/>')
s.append('<rect x="1066" y="0" width="598" height="548" rx="18" fill="#ECF1F8"/>')
def rkx(k): return 150 + k * 24
def _sdr(m, rr): return math.sqrt(m * (1 - m / N) * (1 + (N - 1) * rr))
_M = _mAll * N
ROWY = (176, 314, 452)
def _rowband(y, m, rr, fill, stroke=''):
    sd = _sdr(m, rr); lo, hi = max(m - 2 * sd, 0), min(m + 2 * sd, 36)
    s.append(f'<rect x="{rkx(lo):.1f}" y="{y - 26}" width="{rkx(hi) - rkx(lo):.1f}" height="52" rx="10" fill="{fill}"{stroke}/>')
    s.append(f'<line x1="{rkx(m):.1f}" y1="{y - 34}" x2="{rkx(m):.1f}" y2="{y + 34}" stroke="{NAVY}" stroke-width="4"/>')
def _rowdots(y):
    for i, k in enumerate(C + A):
        s.append(f'<circle cx="{rkx(k)}" cy="{y + (9 if k in (9, 23) else (-9 if k in (11, 24) else 0))}" r="9" fill="{CTRL if i < 4 else PUR}" stroke="{NAVY}" stroke-width="1.5"/>')
_rowband(ROWY[0], np.mean(C), 0, '#A9C1E4', ' fill-opacity="0.8" stroke="#2F6BDB" stroke-width="3"'); _rowband(ROWY[0], np.mean(A), 0, '#CBB2EE', ' fill-opacity="0.8" stroke="#8E5BD0" stroke-width="3"'); _rowdots(ROWY[0])
_rowband(ROWY[1], _M, 0, '#BFC7D3', ' stroke="#5A6679" stroke-width="3"')
for _k in C + A:
    if abs(_k - _M) > 2 * _sdr(_M, 0): s.append(f'<circle cx="{rkx(_k)}" cy="{ROWY[1]}" r="16" fill="none" stroke="{OR}" stroke-width="4"/>')
_rowdots(ROWY[1])
_rowband(ROWY[2], _M, R0, '#BFC7D3', ' stroke="#5A6679" stroke-width="3"'); _rowdots(ROWY[2])
s.append(f'<line x1="{rkx(0)}" y1="496" x2="{rkx(36)}" y2="496" stroke="{NAVY}" stroke-width="2.5"/>')
for k in (0, 10, 20, 30):
    s.append(f'<line x1="{rkx(k)}" y1="496" x2="{rkx(k)}" y2="505" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(rkx(k) - 30, 506, 60, str(k), 'lab small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(rkx(30) + 40, 506, 200, 'K per well', 'lab left small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(18, 8, 1000, '<b>The same 8 wells under three fits</b><br><span style="font-size:19px; color:#4A5568">Dark bar: the fitted average<br>Outlined box: where 95&#160;% of wells should fall under that fit</span>', 'lab left', 1, 'line-height:1.12; '))
L.append(lab(18, ROWY[0] - 62, 1000, f'<b>Step 3: two groups, r fitted and found ≈ 0</b> → log L = <b>{mn(LL1)}</b> (<b style="color:#2F6BDB">blue box</b>: control, <b style="color:#6B3FB0">purple box</b>: A)', 'lab left small', 2, 'font-size:21px; color:#1B2A41; '))
L.append(lab(18, ROWY[1] - 62, 1000, f'<b>Not a step, a what-if: one group, r forced to 0</b> → log L = <b>{mn(LL0r0)}</b> (<b style="color:#A9501A">orange rings</b>: outside the box)', 'lab left small', 3, 'font-size:21px; color:#1B2A41; '))
L.append(lab(18, ROWY[2] - 62, 1000, f'<b>Step 4: one group, r fitted and found = {R0:.4f}</b> → log L = <b>{mn(LL0)}</b> (the wider box covers every well)', 'lab left small', 4, 'font-size:21px; color:#1B2A41; '))
# right panel: δ and r as two separate questions, a 2 × 2 grid of small dot plots, then what step 4 does with A's gap
L.append(lab(1084, 8, 564, '<b>δ and r answer two separate questions</b><br><span style="font-size:18px; color:#4A5568">Each cell: 4 control wells (blue) and 4 molecule wells<br>K axis from 0 to 30, with a break to reach 64<br>Dark bar: the group’s average</span>', 'lab left', 5, 'line-height:25px; '))
_GX, _GY, _GW, _GH = (1182, 1418), (142, 272), 228, 122
L.append(lab(_GX[0], 114, _GW, '<b>Wells agree: r ≈ 0</b>', 'lab small', 5, 'font-size:19px; color:#1B2A41; '))
L.append(lab(_GX[1], 114, _GW, '<b>Wells disagree: r &gt; 0</b>', 'lab small', 5, 'font-size:19px; color:#1B2A41; '))
L.append(lab(1084, _GY[0] + 34, 94, '<b>Gap</b><br>δ &gt; 0', 'lab left small', 5, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
L.append(lab(1084, _GY[1] + 34, 94, '<b>No gap</b><br>δ = 0', 'lab left small', 5, 'font-size:19px; line-height:1.2; color:#1B2A41; '))
_CELLS = [(0, 0, A, PUR, PUR, '<b style="color:#6B3FB0">A: 21, 24, 28, 23</b>'), (1, 0, [15, 16, 64, 17], PUR, '#9AA5B5', 'made-up: 15, 16, 64, 17'),
          (0, 1, [8, 12, 9, 13], PUR, '#9AA5B5', 'made-up: 8, 12, 9, 13'), (1, 1, B, GB, GB, f'<b style="color:{GB}">B is close: 9, 10, 64, 13</b>')]
for _ci, _ri, _mol, _col, _edge, _txt in _CELLS:
    _x, _y = _GX[_ci], _GY[_ri]
    _k = lambda k: _x + 14 + (k * 5.0 if k <= 30 else 188)
    s.append(f'<rect x="{_x}" y="{_y}" width="{_GW}" height="{_GH}" rx="10" fill="#FFFFFF" stroke="{_edge}" stroke-width="{3 if _edge != "#9AA5B5" else 1.5}"/>')
    s.append(f'<path d="M{_x + 12} {_y + 108} H{_x + 170} M{_x + 186} {_y + 108} H{_x + 216} M{_x + 168} {_y + 114} l6 -12 M{_x + 182} {_y + 114} l6 -12" stroke="{NAVY}" stroke-width="2" fill="none"/>')
    for _t in (0, 10, 20, 30, 64): s.append(f'<line x1="{_k(_t):.1f}" y1="{_y + 108}" x2="{_k(_t):.1f}" y2="{_y + 114}" stroke="{NAVY}" stroke-width="2"/>')
    for _vals, _cy, _fc in ((C, _y + 52, CTRL), (_mol, _y + 84, _col)):
        s.append(f'<line x1="{_k(np.mean(_vals)):.1f}" y1="{_cy - 13}" x2="{_k(np.mean(_vals)):.1f}" y2="{_cy + 13}" stroke="{NAVY}" stroke-width="3.5"/>')
        for _j, _v in enumerate(sorted(_vals)):
            s.append(f'<circle cx="{_k(_v):.1f}" cy="{_cy + (4 if _j % 2 else -4)}" r="6" fill="{_fc}" stroke="{NAVY}" stroke-width="1.3"/>')
    L.append(lab(_x + 8, _y + 4, _GW - 12, _txt, 'lab left small', 5, 'font-size:16px; color:#4A5568; white-space:nowrap; '))
L.append(lab(1084, 402, 564, '<b>δ</b>: is one group higher than the other?<br><b>r</b>: do wells of one group disagree beyond counting noise?<br>Both are fitted in step 3 and step 4, and r is never fixed<br>'
          f'Step 4 forbids the gap, so A’s gap is re-read as scatter<br>So step 4 finds r = {R0:.4f}, and step 3 wins by {LL1 - LL0:.2f}, not {LL1 - LL0r0:.1f}<br>B has no clear gap to forbid, so its r barely moves: 0.008 → 0.009', 'lab left small', 6, 'line-height:1.2; color:#1B2A41; font-size:19px; '))
segue(566, f'two searches gave two fits, log L = {mn(LL1)} with a gap and {mn(LL0)} without (slides 15 and 16)', f'is an improvement of {LL1 - LL0:.2f} in log L more than chance alone would give? (slide 18)')
slide('n_r', 'The statistics · step 4 · the scatter', 'Why the one-group fit raises r',
      svg('Three rows on one K axis from 0 to 36, each showing the eight wells as dots, control light blue and A purple. Row 1, two groups: a narrow shaded band around each group’s own average, 10 and 24, all wells inside. '
          'Row 2, one group with r held at 0: one narrow band around 17; the wells sit at its edges, 7, 8 and 28 outside. Row 3, one group with r fitted at 0.0027: the band is wide enough to cover all eight wells.', s, h=650), L,
      'The right panel separates two questions the model keeps apart. δ asks whether one group is higher than the other. r asks whether wells of the same group disagree with one another by more than counting noise. The four small plots show every combination: a gap with wells that agree, which is molecule A; a gap with one wild well; no gap with wells that agree; and no gap with one wild well, which is close to molecule B. A gap does not put anything into r, because r is measured around each group’s own average. Both numbers are fitted in step 3 and in step 4; r is never fixed. ' 'This slide explains a number that could look like a contradiction. Slide 12 said the control and A wells scatter about as much as counting noise alone gives, so r is about zero; yet step 4 reports r = 0.0027 for the same eight wells. '
      'The top row is step 3: each group has its own mean, and the shaded bands, where 95 percent of wells should fall under counting noise alone, comfortably hold every well. '
      f'The middle row forces all eight wells to share one mean, 17, while keeping r at zero. The band is just as narrow, but now the control wells sit at or beyond its lower edge and the A wells at or beyond its upper edge; under that fit the counts are very improbable, log L = {LL0r0:.1f}. '
      'The only way the one-group model can account for wells this far from their shared mean is to say that wells differ a lot from one another, that is, to raise r. The bottom row is the actual step 4: r is refitted to 0.0027, the band widens to cover the wells, and log L recovers to −27.4. '
      f'This matters for the test. Had r been held at zero, step 3 would beat the one-group fit by {LL1 - LL0r0:.1f} in log L; with r refitted it is {LL1 - LL0:.2f}. '
      'By letting scatter between wells explain part of the difference, the test becomes more cautious about calling a gap real. That is intended, because wells genuinely do differ.', steps={4})

# ---------------------------------------------------------------- 18: the test for A
s, L = [], []
s.append('<rect x="0" y="0" width="690" height="300" rx="18" fill="#ECF1F8"/>')
s.append('<rect x="726" y="0" width="938" height="300" rx="18" fill="#ECF1F8"/>')
def ly_(v): return 88 + (-19.5 - v) / 9 * 190
# the gain in log L that chance alone gives: a sideways histogram of the 1,500 no-effect plates' gains, drawn on the same
# log L scale and rising from the one-group level, so each bar's height is a gain and its length is how many plates had it
_gn = np.load(_RES / 'null_gain.npy') ** 2 / 2
_g95 = float(np.percentile(_gn, 95)); _gA = int((_gn >= LL1 - LL0).sum())
_bins = np.arange(0, 7.5, 0.5); _hc, _ = np.histogram(_gn, _bins); _hmax = max(_hc.max(), 1)
for _lo, _c in zip(_bins[:-1], _hc):
    if _c / _hmax * 330 >= 3:
        s.append(f'<rect x="40" y="{ly_(LL0 + _lo + 0.5):.1f}" width="{_c / _hmax * 330:.1f}" height="{ly_(LL0 + _lo) - ly_(LL0 + _lo + 0.5) - 1.5:.1f}" fill="#F3C9A5" stroke="{OR}" stroke-width="1"/>')
s.append(f'<line x1="40" y1="{ly_(LL0 + _g95):.1f}" x2="400" y2="{ly_(LL0 + _g95):.1f}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="6 4"/>')
L.append(lab(250, int(ly_(LL0 + _g95)) - 24, 160, f'<span style="color:#A9501A">95&#160;% below {_g95:.1f}</span>', 'lab left small', 1, 'font-size:16px; white-space:nowrap; '))
for v, col, dash in ((LL1, PUR, ''), (LL0, '#5A6679', ' stroke-dasharray="10 6"')):
    s.append(f'<line x1="40" y1="{ly_(v):.1f}" x2="400" y2="{ly_(v):.1f}" stroke="{col}" stroke-width="5"{dash}/>')
L.append(lab(40, int(ly_(LL1)) + 10, 372, f'<b style="color:#A9501A">Bars: gains in 1,500 no-effect plates</b><br>Longer bar: more plates gained that much<br>{"None" if _gA == 0 else _gA} reached A’s {LL1 - LL0:.2f}', 'lab left small', 1, 'font-size:18px; line-height:1.22; color:#1B2A41; '))
s.append(f'<line x1="440" y1="{ly_(LL1) + 12:.1f}" x2="440" y2="{ly_(LL0) - 12:.1f}" stroke="#2F6BDB" stroke-width="4"/>'
         f'<path d="M431 {ly_(LL1) + 14:.1f} l9 -15 l9 15 Z" fill="#2F6BDB"/><path d="M431 {ly_(LL0) - 14:.1f} l9 15 l9 -15 Z" fill="#2F6BDB"/>')
L.append(lab(18, 8, 660, '<b>1. A gap always raises log L, but by how much?</b>', 'lab left', 1))
L.append(lab(40, int(ly_(LL1)) - 38, 380, f'<b style="color:#6B3FB0">two groups: log L = {mn(LL1, 2)}</b>', 'lab left small', 1, 'font-size:21px; '))
L.append(lab(40, int(ly_(LL0)) + 8, 380, f'<b style="color:#5A6679">one group: log L = {mn(LL0, 2)}</b>', 'lab left small', 1, 'font-size:21px; '))
L.append(lab(462, int((ly_(LL1) + ly_(LL0)) / 2) - 52, 220, f'<b style="color:#2F6BDB">improvement</b><br><b style="color:#2F6BDB">{LL1 - LL0:.2f}</b><br><b>D = 2 × {LL1 - LL0:.2f}</b><br><b>= {LRA:.2f}</b>', 'lab left small', 1, 'line-height:1.2; font-size:21px; '))
def zx_(z): return 790 + (z + 3.5) / 9 * 820
ZB = 236
zs_ = np.linspace(-3.5, 5.5, 110)
_zsim = np.load(_RES / 'null_gain.npy'); _hc, _he = np.histogram(_zsim, bins=np.arange(-3.5, 5.01, 0.5), density=True)
for _c, _e in zip(_hc, _he[:-1]):
    s.append(f'<rect x="{zx_(_e) + 1:.1f}" y="{ZB - _c * 380:.1f}" width="{zx_(_e + 0.5) - zx_(_e) - 2:.1f}" height="{_c * 380:.1f}" fill="#C9D6E8"/>')
s.append('<path d="M' + ' L'.join(f'{zx_(z):.1f} {ZB - stats.norm.pdf(z) * 380:.1f}' for z in zs_) + f'" stroke="{NAVY}" stroke-width="3.5" fill="none"/>')
IX0, IX1, IY0, IY1 = 752, 944, 112, 204
s.append(f'<rect x="{IX0}" y="{IY0}" width="{IX1 - IX0}" height="{IY1 - IY0}" rx="8" fill="#FFFFFF" stroke="#9FB4D0" stroke-width="2"/>')
def _ix(z): return IX0 + 8 + (z - 3.5) / 1.5 * (IX1 - IX0 - 16)
def _iy(p): return IY1 - 8 - p * 80000
s.append(f'<path d="M{_ix(ZA):.1f} {IY1 - 8} ' + ' '.join(f'L{_ix(z):.1f} {_iy(stats.norm.pdf(z)):.1f}' for z in np.linspace(ZA, 5, 20)) + f' L{_ix(5):.1f} {IY1 - 8} Z" fill="{OR}"/>')
s.append('<path d="M' + ' L'.join(f'{_ix(z):.1f} {_iy(stats.norm.pdf(z)):.1f}' for z in np.linspace(3.5, 5, 30)) + f'" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
s.append(f'<line x1="{_ix(ZA):.1f}" y1="{IY0 + 8}" x2="{_ix(ZA):.1f}" y2="{IY1 - 8}" stroke="{PUR}" stroke-width="3"/>')
L.append(lab(IX0 - 4, IY0 - 30, 240, 'right tail (z 3.5 to 5), magnified', 'lab left small', 2, 'font-size:18px; color:#4A5568; white-space:nowrap; '))
tz_ = np.linspace(ZA, 5.5, 20)
s.append(f'<path d="M{zx_(ZA):.1f} {ZB} ' + ' '.join(f'L{zx_(z):.1f} {ZB - stats.norm.pdf(z) * 380 - 3:.1f}' for z in tz_) + f' L{zx_(5.5):.1f} {ZB} Z" fill="{OR}"/>')
s.append(f'<line x1="{zx_(-3.5)}" y1="{ZB}" x2="{zx_(5.5)}" y2="{ZB}" stroke="{NAVY}" stroke-width="2.5"/>')
for z in (-2, 0, 2, 4):
    s.append(f'<line x1="{zx_(z):.1f}" y1="{ZB}" x2="{zx_(z):.1f}" y2="{ZB + 9}" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(int(zx_(z)) - 30, ZB + 10, 60, mn(z, 0), 'lab small', 2, 'font-size:19px; color:#4A5568; '))
s.append(f'<line x1="{zx_(ZA):.1f}" y1="110" x2="{zx_(ZA):.1f}" y2="{ZB}" stroke="{PUR}" stroke-width="4"/>')
L.append(lab(744, 8, 900, '<b>2. z is compared with the standard normal distribution</b>', 'lab left', 2))
L.append(lab(744, 48, 900, 'If A did nothing, z would be a random draw from this curve: Normal(mean 0, standard deviation 1)', 'lab left small', 2, 'color:#4A5568; font-size:19px; '))
L.append(lab(int(zx_(5.5)) - 20, ZB + 10, 60, '<b>z</b>', 'lab small', 2, 'font-size:20px; '))
L.append(lab(int(zx_(ZA)) - 250, 70, 240, f'<b style="color:#6B3FB0">A: z = {ZA:.2f}</b>', 'lab small', 2, 'text-align:right; font-size:21px; '))
L.append(lab(int(zx_(ZA)) + 14, 150, 190, '<b style="color:#A9501A">area beyond z</b><br><b style="color:#A9501A">p = 0.00008</b>', 'lab left small', 2, 'line-height:1.15; font-size:20px; '))
BY = 316
for px in PXn:
    s.append(f'<rect x="{px}" y="{BY}" width="{PWn}" height="232" rx="18" fill="#FFFFFF" stroke="#2F6BDB" stroke-width="2.5"/>')
for px in (PXn[0], PXn[1]):
    s.append(f'<path d="M{px + PWn + 4} {BY + 116} h8 v-9 l12 15 l-12 15 v-9 h-8 Z" fill="{NAVY}"/>')
L.append(lab(18, BY + 8, 506, f'<b>The likelihood-ratio statistic, D</b><br>D = 2 × (log L two groups − log L one group)<br>The same as 2 × ln(L two groups ÷ L one group)<br>Here: 2 × ({mn(LL1, 2)} − ({mn(LL0, 2)})) = <b>{LRA:.2f}</b><br>'
          'The larger D is, the more the gap helps to explain the counts', 'lab left small', 3, S19))
L.append(lab(PXn[1] + 18, BY + 8, 506, '<b>Why a higher log L is not enough</b><br>The fit with a gap never scores lower: it may set δ = 0<br>So with no effect at all it still gains a little (orange)<br>The test asks whether A’s gain is more than that<br>If A did nothing, D is chi-square with 1 degree of freedom<br>That is the distribution of z², with z standard normal<br>'
          f'Bars: z from 1,500 simulated plates where A does nothing<br>About {100 * float((_zs0 > 1.645).mean()):.0f}&#160;% of them pass z = 1.64 (the curve: 5&#160;%)', 'lab left small', 4, 'line-height:1.26; color:#1B2A41; font-size:19px; '))
L.append(lab(PXn[2] + 18, BY + 8, 506, f'<b>From D to p</b><br>D has no direction: a fall would give the same D<br>So z = √D takes the sign of δ: here z = +{ZA:.2f}<br>z is compared with the standard normal distribution<br>p = P(Z ≥ z), for Z drawn from Normal(0, 1)<br>'
          'One-sided: only a rise counts as a response<br>A negative z can never be significant<br><b>p = 0.00008</b><br>With 4 + 4 wells, read p as approximate (slide 31)', 'lab left small', 5, 'line-height:1.26; color:#1B2A41; font-size:19px; '))
s.append('<rect x="0" y="566" width="1664" height="128" rx="12" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(16, 573, 1632, '<b>In words:</b> if molecule A did nothing, a gap this large between its wells and the control wells would arise by chance about once in 12,000 experiments, by the formula<br>'
          'So chance is a poor explanation: the A wells really do hold more nanowells past the cutoff than the control wells<br>'
          f'<b>So far:</b> the fit with a gap beats the one without by {LL1 - LL0:.2f} in log L (slides 16 and 17)<br><b>Next:</b> B has the same average as A and gets a different verdict: why (slide 19)', 'lab left small', 9, 'line-height:1.25; color:#1B2A41; font-size:21px; '))
slide('n_test', 'The statistics · step 5 · the test', 'The test: is the gap more than chance?',
      svg(f'Top left, two horizontal levels on a log-likelihood scale: two groups at {LL1:.2f} and one group at {LL0:.2f}, with a double arrow between them marked {LL1 - LL0:.2f}, and D = {LRA:.2f}. '
          f'Top right, a standard normal curve on a z axis from −3 to 5, the z values expected if A did nothing; a purple line at z = {ZA:.2f} far in the right tail, the tiny area beyond it marked p = 0.00008. '
          'Below, three boxes: the likelihood ratio, why it is compared with z squared, and from D to p.', s, h=704), L,
      f'Now the test itself. The two fits gave log-likelihoods of {LL1:.2f} with a gap and {LL0:.2f} without, a difference of {LL1 - LL0:.2f}. The likelihood-ratio statistic, D, is twice that difference, {LRA:.2f}; '
      'it is called a ratio because a difference of logs is the log of a ratio of the two likelihoods. The larger it is, the more the gap helps to explain the counts. But how large is large? '
      'A fit with a gap can never score lower than one without, because setting the gap to zero is one of its options; so a higher log-likelihood alone proves nothing. The orange bars in panel 1 are a sideways histogram, on the same log-likelihood scale, of how much the fit with a gap gains when the molecule does nothing: in 1,500 simulated no-effect plates the gain is usually a few tenths, 95 percent of them gain less than 2.6, and none gained as much as A’s 7.12, in either direction. The test asks whether A’s gain is more than that. ' 'Giving a model an extra free number always improves its fit a little, even when it is not needed. Statistical theory says how much: if the molecule truly did nothing, D would behave like the square of z, '
      'where z is a value drawn from the standard normal distribution, mean 0 and standard deviation 1; that is the chi-square distribution with one degree of freedom; the factor of two is what makes that hold. So under chance alone D is usually below about 4. '
      f'To use this, take the square root of D and give it the sign of δ: z = +{ZA:.2f}, positive because A sits above the control. The curve on the right is what z would look like if A did nothing; '
      f'{ZA:.2f} is far out in its tail. The test is one-sided, because only a rise counts as a response, so p is the area of the curve beyond z: 0.00008. The grey bars are a check on that theory: z values from 1,500 simulated plates of four and four wells in which the molecule does nothing, with the scatter of step 4. They follow the curve, but a little wider: across this and a second batch of simulations about 8 percent pass z = 1.64 where the curve says 5 percent, and the largest of these 1,500 was 3.3, the most extreme in either direction −3.7. So with four and four wells p is approximate and somewhat too small; A’s z of 3.77 is still beyond every simulated no-effect plate.', steps={5})

# ---------------------------------------------------------------- 19: A and B, same average, different verdict
s, L = [], []
_fB1 = fit_bb(np.array(C + B), [N] * 8, _X2); _fB0 = fit_bb(np.array(C + B), [N] * 8, np.ones((8, 1)))
_bmc, _bmb = [N / (1 + math.exp(-e)) for e in (_fB1.coef[0], _fB1.coef[0] + _fB1.coef[1])]
LRB = 2 * (_fB1.loglik - _fB0.loglik); ZB_ = math.sqrt(max(LRB, 0)); PB_ = float(stats.norm.sf(ZB_))
for pi_, (px, nm, col, ks, m0, m1, rr, st) in enumerate((
        (0, 'A', PUR, A, np.mean(C), np.mean(A), 0.0, [f'average K: <b>24</b>', 'r ≈ 0', f'log L two groups: {mn(LL1, 2)}', f'log L one group: {mn(LL0, 2)}', f'D = {LRA:.2f}', f'z = {ZA:.2f}', 'p = 0.00008', '<b style="color:#6B3FB0">A: a real gap</b>']),
        (852, 'B', GB, B, _bmc, _bmb, float(_fB1.rho), [f'average K: <b>24</b>', f'r = {_fB1.rho:.3f}', f'log L two groups: {mn(_fB1.loglik, 2)}', f'log L one group: {mn(_fB0.loglik, 2)}', f'D = {LRB:.2f}', f'z = {ZB_:.2f}', f'p = {PB_:.2f}', f'<b style="color:{GB}">B: could be chance</b>']))):
    s.append(f'<rect x="{px}" y="0" width="812" height="540" rx="18" fill="#ECF1F8"/>')
    _axis3(px + 100, px + 470)
    for cx, kk, cc, m in ((px + 200, C, CTRL, m0), (px + 370, ks, col, m1)):
        _band3(cx, m, m / N, rr, '#C9D6E8')
        s.append(f'<line x1="{cx - 62}" y1="{dy(m):.1f}" x2="{cx + 62}" y2="{dy(m):.1f}" stroke="{NAVY}" stroke-width="4"/>')
        _dots3(cx, kk, cc)
    L.append(lab(px + 18, 10, 780, f'<b>{nm} against control</b>', 'lab left', 1 + pi_))
    L.append(lab(px + 18, 50, 780, 'Bar: the fitted average, shaded: where 95&#160;% of wells should fall at this fit’s r', 'lab left small', 1 + pi_, 'color:#4A5568; font-size:19px; '))
    L.append(lab(px + 140, 506, 120, '<b>control</b>', 'lab small', 1 + pi_, 'font-size:20px; '))
    L.append(lab(px + 310, 506, 120, f'<b style="color:{col}">{nm}</b>', 'lab small', 1 + pi_, 'font-size:20px; '))
    L.append(lab(px + 500, 120, 300, '<br>'.join(st), 'lab left small', 3 + pi_, 'line-height:1.6; color:#1B2A41; font-size:21px; '))
L.append(lab(852 + 500, 400, 304, f'<b>Fitted averages: {_bmc:.1f} and {_bmb:.1f}</b><br>With r this large the curve has a long right tail<br>Its average sits above where most wells fall', 'lab left small', 2, 'color:#1B2A41; font-size:19px; line-height:1.2; '))
L.append(lab(18, 546, 780, '<b>What the left panel says</b><br>All four A wells sit above all four control wells<br>The two shaded ranges barely overlap (14 to 16)<br>'
          'By the formula, chance gives this about once in 12,000 (optimistic: slide 31)<br><b style="color:#6B3FB0">Verdict: A raises the count</b>', 'lab left small', 5, 'color:#1B2A41; line-height:1.28; font-size:20px; '))
L.append(lab(870, 546, 780, f'<b>What the right panel says</b><br>The same average, but it comes from one well at 64<br>Three of the four B wells look like control wells, and the ranges overlap<br>'
          f'Chance alone would give this about once in {1 / PB_:.0f} experiments<br><b style="color:{GB}">Verdict: no evidence that B raises the count</b>', 'lab left small', 5, 'color:#1B2A41; line-height:1.28; font-size:20px; '))
L.append(lab(0, 686, 1664, '<b>The difference is r:</b> A’s wells agree with each other and B’s do not, and the more wells scatter, the larger a gap chance alone can produce', 'lab left small', 6, 'color:#1B2A41; font-size:21px; '))
segue(724, 'for one molecule, D becomes z and then p (slide 18)', 'the whole algorithm on one page, for A and for B (slide 20), then the correction for testing several molecules (slide 24)')
slide('n_ab', 'The statistics · step 5 · two molecules', 'Same average, different verdict',
      svg('Two panels on the same K axis from 0 to 70. Left, A against control: narrow shaded bands around averages of 10 and 24, all eight wells inside; r is about 0, D 14.24, z 3.77, p 0.00008. '
          f'Right, B against control: B’s wells are 9, 10, 64 and 13, so r is {_fB1.rho:.3f} and the shaded bands are several times wider and overlap; D {LRB:.2f}, z {ZB_:.2f}, p {PB_:.2f}.', s, h=806), L,
      'Molecules A and B make the role of r concrete. Both have an average count of 24 against the control’s 10, so judged by averages alone they look the same. '
      'On the left, A’s four wells are all close to 24 and the control wells close to 10. Neither group scatters beyond what counting noise alone gives, so r is about zero, the bands of expected counts are narrow and overlap only between about 14 and 16, with every A well above every control well, and the gap between the groups cannot be put down to chance: z = 3.77, p = 0.00008. '
      f'On the right, B’s average of 24 comes from one well at 64 and three near 10. That is far more scatter than counting noise, so the fit sets r to {_fB1.rho:.3f}. With that much scatter between wells, the range of counts expected by chance is several times wider, '
      f'the two groups’ ranges overlap almost completely, and a gap of this size is unsurprising: z = {ZB_:.2f}, p = {PB_:.2f}. The fitted averages are {_bmc:.1f} for control and {_bmb:.1f} for B rather than the plain 10 and 24: with this much scatter the model’s curve has a long right tail, so its average sits above where most wells fall, and the single well at 64 pulls less than it does in a plain average. '
      'So r is never the thing being tested, but it decides the test: the more wells scatter, the larger a gap chance alone can produce. One well with many responders is not evidence; four wells that agree are.', steps={5})

# ---------------------------------------------------------------- 20: the whole algorithm
s, L = [], []
RH, RY0 = 92, 44
ROWS = [('1. Set the cutoff', 'slide 9', 'cutoff = the 99th percentile of the control', 'nanowells’ fold change', 'cutoff = ×2.2', '&#160;', 'the same cutoff, ×2.2', '&#160;'),
        ('2. Count K per well', 'slide 9', 'K<sub>w</sub> = the number of well w’s nanowells', 'with fold change above the cutoff', 'control: 7, 8, 14, 11', 'A: 21, 24, 28, 23', 'control: 7, 8, 14, 11', 'B: 9, 10, 64, 13'),
        ('3. Fit as two groups', 'slides 12 to 15', 'Find the α, δ, r with the highest log L', 'log L = Σ<sub>w</sub> ln P(K<sub>w</sub> | π, r)', 'δ = 0.89, r ≈ 0', f'log L = {mn(LL1, 2)}', f'δ = {_fB1.coef[1]:.2f}, r = {_fB1.rho:.3f}', f'log L = {mn(_fB1.loglik, 2)}'),
        ('4. Fit as one group', 'slides 15 to 17', 'The same search with δ held at 0', 'r is refitted', f'r = {R0:.4f}', f'log L = {mn(LL0, 2)}', f'r = {_fB0.rho:.3f}', f'log L = {mn(_fB0.loglik, 2)}'),
        ('5. Compare the fits', 'slides 18 and 19', 'D = 2 × (log L of step 3 − log L of step 4)', 'z = √D, and p = the area beyond z', f'D = {LRA:.2f}, z = {ZA:.2f}', 'p = 0.00008', f'D = {LRB:.2f}, z = {ZB_:.2f}', f'p = {PB_:.2f}'),
        ('6. Correct across molecules', 'slide 24', 'q = p × (number of molecules) ÷ (rank of p)', '&#160;', 'q = 0.00024, below 0.05', '<b style="color:#6B3FB0">A: rare responders detected</b>', 'q = 0.17, above 0.05', f'<b style="color:{GB}">B: not detected</b>')]
for i in range(6):
    y = RY0 + i * RH
    s.append(f'<rect x="0" y="{y}" width="1664" height="{RH - 8}" rx="14" fill="{"#ECF1F8" if i % 2 == 0 else "#E3EAF3"}"/>')
    if i < 5:
        s.append(f'<path d="M150 {y + RH - 10} h20 l-10 12 Z" fill="{NAVY}"/>')
_SP = '<br style="display:none"><span style="display:block; height:24px"></span>'
def _colr(x, w, head, cells, i, extra=''):
    L.append(lab(x, 4, w, f'<b>{head}</b>', 'lab left small', 1))
    L.append(lab(x, RY0 + 8, w, _SP.join(f'{c1}<br>{c2}' for c1, c2 in cells), 'lab left small', i, 'line-height:34px; color:#1B2A41; font-size:21px; white-space:nowrap; ' + extra))
_colr(18, 272, 'Step', [(f'<b>{r_[0]}</b>', f'<span style="font-size:19px; color:#4A5568">{r_[1]}</span>') for r_ in ROWS], 2)
_colr(576, 410, 'What is computed', [(r_[2], r_[3]) for r_ in ROWS], 3)
_colr(990, 306, 'Molecule A against control', [(r_[4], r_[5]) for r_ in ROWS], 4)
_colr(1316, 344, 'Molecule B against control', [(r_[6], r_[7]) for r_ in ROWS], 5)
s.append(f'<line x1="1302" y1="{RY0}" x2="1302" y2="{RY0 + 6 * RH - 8}" stroke="#FFFFFF" stroke-width="4"/>')
# one cartoon per step, in a white card at x 298 to 444, with a caption to its right
def _rc_bell(cx, yb, sg, ht, x0, x1, n=28):
    return [(x0 + (x1 - x0) * j / n, yb - ht * math.exp(-0.5 * ((x0 + (x1 - x0) * j / n - cx) / sg) ** 2)) for j in range(n + 1)]
def _rc_pl(pts): return 'M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in pts)
_BLU = '#2F6BDB'
for i in range(6):
    y = RY0 + i * RH; yb = y + 66
    s.append(f'<rect x="298" y="{y + 6}" width="146" height="72" rx="8" fill="#FFFFFF"/>')
    if i == 0:      # control nanowells' fold change, cutoff c, orange beyond it
        pts = _rc_bell(346, yb, 27, 46, 306, 436)
        tail = [p for p in pts if p[0] >= 386]
        s.append(f'<path d="{_rc_pl(tail)} L436 {yb} L{tail[0][0]:.1f} {yb} Z" fill="{OR}"/>')
        s.append(f'<path d="{_rc_pl(pts)}" stroke="{NAVY}" stroke-width="2.5" fill="none"/><line x1="304" y1="{yb}" x2="438" y2="{yb}" stroke="{NAVY}" stroke-width="2"/>')
        s.append(f'<line x1="{tail[0][0]:.1f}" y1="{y + 12}" x2="{tail[0][0]:.1f}" y2="{yb}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="5 3"/>')
    elif i == 1:    # a well (circle) of nanowells (squares); the orange ones are counted
        cx, cy = 336, y + 42
        s.append(f'<circle cx="{cx}" cy="{cy}" r="31" fill="#F7F5F0" stroke="{_BLU}" stroke-width="2.5"/>')
        _sh = ['#C6D6EA', '#9FB4D0', '#5B7BA8', '#2C4770']; _or = {(1, 3), (3, 1), (2, 4)}
        for a in range(5):
            for b in range(5):
                x_, y_ = cx - 22.5 + a * 9.5, cy - 22.5 + b * 9.5
                if (x_ + 3.5 - cx) ** 2 + (y_ + 3.5 - cy) ** 2 < 26 ** 2:
                    s.append(f'<rect x="{x_:.1f}" y="{y_:.1f}" width="7" height="7" rx="1.5" fill="{OR if (a, b) in _or else _sh[(a * 3 + b * 2) % 4]}"/>')
        s.append(f'<path d="M374 {cy} h12" stroke="{NAVY}" stroke-width="2.5"/><path d="M385 {cy - 5} l7 5 l-7 5 Z" fill="{NAVY}"/>')
        for a in range(3): s.append(f'<rect x="{398 + a * 13}" y="{cy - 5}" width="10" height="10" rx="2" fill="{OR}"/>')
    elif i in (2, 3):   # the 8 wells on a K axis under two curves (step 3) or one (step 4)
        s.append(f'<line x1="304" y1="{yb}" x2="438" y2="{yb}" stroke="{NAVY}" stroke-width="2"/>')
        if i == 2:
            s.append(f'<path d="{_rc_pl(_rc_bell(336, yb, 11, 44, 306, 372))}" stroke="{NAVY}" stroke-width="2.5" fill="none"/><path d="{_rc_pl(_rc_bell(404, yb, 13, 38, 366, 438))}" stroke="{PUR}" stroke-width="2.5" fill="none"/>')
        else:
            s.append(f'<path d="{_rc_pl(_rc_bell(370, yb, 27, 26, 306, 438))}" stroke="#5A6679" stroke-width="2.5" fill="none"/>')
        for x_ in (326, 333, 339, 347): s.append(f'<circle cx="{x_}" cy="{yb}" r="4" fill="{CTRL}" stroke="{NAVY}" stroke-width="1.2"/>')
        for x_ in (394, 401, 406, 414): s.append(f'<circle cx="{x_}" cy="{yb}" r="4" fill="{PUR}" stroke="{NAVY}" stroke-width="1.2"/>')
    elif i == 4:    # the rise in log L between the two fits, then the area beyond z
        s.append(f'<line x1="306" y1="{y + 20}" x2="346" y2="{y + 20}" stroke="{PUR}" stroke-width="3"/><line x1="306" y1="{y + 62}" x2="346" y2="{y + 62}" stroke="#5A6679" stroke-width="3" stroke-dasharray="5 3"/>')
        s.append(f'<line x1="326" y1="{y + 30}" x2="326" y2="{y + 52}" stroke="{_BLU}" stroke-width="2.5"/><path d="M321 {y + 31} l5 -8 l5 8 Z M321 {y + 51} l5 8 l5 -8 Z" fill="{_BLU}"/>')
        s.append(f'<path d="M352 {y + 42} h8" stroke="{NAVY}" stroke-width="2.5"/><path d="M359 {y + 37} l7 5 l-7 5 Z" fill="{NAVY}"/>')
        pts = _rc_bell(394, yb, 13, 44, 370, 438); tail = [p for p in pts if p[0] >= 412]
        s.append(f'<path d="{_rc_pl(tail)} L438 {yb} L{tail[0][0]:.1f} {yb} Z" fill="{OR}"/><path d="{_rc_pl(pts)}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
        s.append(f'<line x1="370" y1="{yb}" x2="438" y2="{yb}" stroke="{NAVY}" stroke-width="2"/><line x1="{tail[0][0]:.1f}" y1="{y + 14}" x2="{tail[0][0]:.1f}" y2="{yb}" stroke="{PUR}" stroke-width="2.5"/>')
    else:           # the molecules' p in rank order against a threshold that rises with rank
        s.append(f'<path d="M308 {y + 12} V{yb + 4} H438" stroke="{NAVY}" stroke-width="2" fill="none"/><line x1="312" y1="{y + 54}" x2="434" y2="{y + 20}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="5 3"/>')
        for x_, y_, c_ in ((330, y + 62, PUR), (372, y + 30, TEAL), (414, y + 15, GB)):
            s.append(f'<circle cx="{x_}" cy="{y_}" r="6" fill="{c_}" stroke="{NAVY}" stroke-width="1.5"/>')
L.append(lab(298, 4, 250, '<b>Picture</b>', 'lab left small', 1))
for i, c_ in enumerate(['<b style="color:#A9501A">orange</b>: past<br>the cutoff', 'K = <b style="color:#A9501A">orange</b><br>squares', 'one curve<br>per group', 'one curve<br>for all 8', 'rise in log L<br>→ area<br>beyond z', 'p by rank<br>against a<br>rising line']):
    L.append(lab(452, RY0 + i * RH + (42 - 10 * (c_.count('<br>') + 1)), 110, c_, 'lab left small', 2, 'font-size:17px; line-height:20px; color:#4A5568; '))
L.append(lab(0, RY0 + 6 * RH + 6, 1664, 'Every molecule goes through steps 3 to 5 against the same control wells, and step 6 is applied once to all their p-values<br>A and B have the same average K (24): what separates them is r (slide 19)<br>Backup: what the test assumes (slide 22), the correction (slide 24)', 'lab left small', 8, 'color:#4A5568; font-size:21px; line-height:1.3; '))
slide('n_recap', 'The statistics · steps 1–6 · recap', 'The whole algorithm, for A and for B',
      svg('A table of the six steps, one row each: the step, what is computed, the value for molecule A against control, and the value for molecule B against control (D 0.89, z 0.95, p 0.17, q 0.17, not detected). Cutoff ×2.2; counts 7, 8, 14, 11 and 21, 24, 28, 23; '
          'fit as two groups, log-likelihood −20.2; fit as one group, −27.4; likelihood ratio 14.24, z 3.77, p 0.00008; corrected q 0.00024, detected.', s, h=700), L,
      'This slide collects the whole algorithm on one page, with molecule A’s numbers. Step 1: the cutoff is the 99th percentile of the control nanowells’ fold change, 2.2-fold here. Step 2: for each well, count the nanowells above the cutoff; '
      'that count K is all the test uses. Step 3: fit the model with a gap, by finding the α, δ and r that give the eight counts the highest log-likelihood; for A that is −20.2, with δ = 0.89. '
      'Step 4: fit again with δ held at zero, one rate for all eight wells; r is refitted and rises to 0.0027, and the log-likelihood drops to −27.4. Step 5: twice the difference is the likelihood ratio, 14.24; its square root is z, 3.77, and p is the area of a standard normal curve beyond z, 0.00008. '
      'Step 6: because several molecules were tested, each p is corrected by Benjamini–Hochberg into q; A’s q is 0.00024, below 0.05, so A is called a source of rare responders. '
      'Every molecule goes through steps 3 to 5 against the same control wells, and step 6 is applied once to all their p-values together. The last column runs molecule B through the same six steps. Its wells average 24, like A’s, but one well at 64 makes the fit set r to 0.008; the two fits then differ by only 0.4 in log L, D is 0.89, z 0.95 and p 0.17, and after correction q is 0.17, so B is not called.', steps={1, 2, 3, 4, 5, 6})

# ---------------------------------------------------------------- 22 (backup): assumptions and the numbers beside the verdict
s, L = [], []
TYa, LHa = 56, 40
L.append(lab(0, 0, 900, '<b>All three molecules, each against the same 4 control wells</b>', 'lab left', 1))
L.append(lab(0, TYa, 110, '<br>'.join(['&#160;', '<b>A</b>', '<b>C</b>', '<b>B</b>']), 'lab left small', 1, f'line-height:{LHa}px; color:#1B2A41; '))
_ca = [('wells’ K', ['21, 24, 28, 23', '13, 15, 15, 14', '9, 10, 64, 13'], 60, 190), ('log L two', [mn(LL1, 2)] + [mn(_FITS[k]['l1'], 2) for k in 'CB'], 250, 110), ('log L one', [mn(LL0, 2)] + [mn(_FITS[k]['l0'], 2) for k in 'CB'], 360, 110),
       ('D', [f'{LRA:.2f}'] + [f'{_FITS[k]["lr"]:.2f}' for k in 'CB'], 470, 90), ('z', [f'{_FITS[k]["z"]:.2f}' for k in 'ACB'], 560, 80), ('p', ['0.00008', '0.041', '0.17'], 640, 110),
       ('odds ratio (95&#160;% CI)', ['<b>2.4</b> (1.7–3.6)', '1.4 (0.96–2.2)', '1.6 (0.57–4.3)'], 750, 210)]
for h, v, x, w in _ca:
    L.append(lab(x, TYa, w, '<br>'.join([f'<b>{h}</b>'] + v), 'lab small', 2, f'line-height:{LHa}px; color:#1B2A41; white-space:nowrap; font-size:21px; '))
s.append(f'<rect x="0" y="{TYa + LHa + 2}" width="960" height="2" fill="{GRID}"/>')
L.append(lab(0, 236, 960, '<b>The numbers beside the verdict</b>', 'lab left', 3))
L.append(lab(0, 280, 960, 'Odds ratio: exp(δ), how many times higher the odds of a nanowell passing are<br>95&#160;% CI: the range of odds ratios the data are compatible with<br>'
          'Per-well p: where one well ranks among the control wells<br>Per-well p = (1 + number of control wells at or above it) ÷ (control wells + 1)<br>Each A well: p = 0.2, the lowest possible with 4 control wells<br>'
          'Shuffling tests: two further tests that relabel whole wells (slide 25)', 'lab left small', 3, 'line-height:1.3; color:#1B2A41; font-size:20px; '))
s.append('<rect x="1000" y="0" width="664" height="336" rx="14" fill="#FBF1E7" stroke="#E07A2F" stroke-width="2"/>')
L.append(lab(1018, 10, 630, '<b>What the test assumes</b>', 'lab left', 4))
L.append(lab(1018, 54, 630, '• Wells are treated as independent (they share one estimated cutoff)<br>• Nanowells in one well are not: r measures how much they share<br>• One r serves control and molecule wells alike<br>'
          '• “D behaves like z²” is approximate with 4 + 4 wells (slide 18)<br>• The shuffling tests check that approximation<br>• One-sided: only a rise counts<br>• The percentile (99th) is fixed in advance<br>• The cutoff itself is estimated from the control wells<br>• With r fitted at 0, nanowells count as independent: p is least reliable<br>'
          '• All wells are treated as one plate: no plate offset is fitted', 'lab left small', 4, 'line-height:1.24; color:#1B2A41; font-size:19.5px; '))
L.append(lab(0, 490, 1664, 'C is the third molecule on the plate (slide 9). Its z of 1.74 gives p = 0.041, below 0.05 before correction and not after (slide 24)'.replace('. Its', '<br>Its'), 'lab left small', 5, 'line-height:1.3; color:#4A5568; font-size:21px; '))
slide('n_assume', 'Backup · the test', 'What the test assumes, and the numbers beside it',
      svg('Left, a table for molecules A, C and B: their wells’ counts, the two log-likelihoods, D, z, p and the odds ratio with its 95 percent interval; below it, what the odds ratio, its interval and the per-well p mean. '
          'Right, a box listing what the test assumes.', s, h=580), L,
      'This backup slide holds what was left off the main test slides. The table gives all three molecules, each tested against the same four control wells: A, with z = 3.77 and p = 0.00008; C, a third molecule on the plate, with p = 0.041; and B, with p = 0.17. '
      'The odds ratio is e to the δ, how many times higher the odds of a nanowell passing the cutoff are with the molecule; its 95 percent interval, from the profile of the likelihood, is the range of odds ratios the data are compatible with. '
      'The per-well p is a separate, simpler number reported for each well: one plus the number of control wells at or above it, over the number of control wells plus one. With four control wells it cannot go below 0.2, so it describes a well but cannot carry a verdict. '
      'The assumptions: wells are independent of one another; nanowells within a well are not, and r measures how much they share; one r serves control and molecule wells alike; the statement that D behaves like z squared is an approximation with four and four wells, which the shuffling tests check; '
      'the test is one-sided; the percentile is fixed in advance while the cutoff itself is estimated from the control wells, which all wells then share; when r is fitted at zero, as for A and C, the test reduces to treating nanowells as independent, which is where its p-value is least reliable; and all wells are treated as one plate, with no plate offset.')


# ================================================================ additional backup slides
import json as _js, os as _os
_EXP = _jr.loads((_RES / 'example_plate.json').read_text())
_Q = {m: dict(t_p=_EXP[m]['welch_p'], t=_EXP[m]['welch_t'], mw_p=_EXP[m]['rank_p'], fisher_p=_EXP[m]['fisher_p'], sep_z=_EXP[m]['sep_z'], sep_p=_EXP[m]['sep_p'],
              sep_rc=_EXP[m]['sep_r'][0], sep_rt=_EXP[m]['sep_r'][1], sep_b1=_EXP[m]['sep_b1'], sep_mu0=_EXP[m]['sep_mu0'], sep_mu1=_EXP[m]['sep_mu1']) for m in 'ACB'}
_Q['loo'] = _jr.loads((_RES / 'loo_variance.json').read_text())
_CAL = _jr.loads((_RES / 'calibration.json').read_text())['designs']
_E2E = _jr.loads((_RES / 'end_to_end_null.json').read_text())
_POWJ = _jr.loads((_RES / 'power.json').read_text())['cells']; _POW = {k: v['power'] for k, v in _POWJ.items()}; _POWN = next(iter(_POWJ.values()))['plates']
def pfmt(p): return f'{p:.6f}'.rstrip('0') if p < 0.0001 else (f'{p:.4f}' if p < 0.001 else (f'{p:.3f}' if p < 0.1 else f'{p:.2f}'))
def boxp(x, y, w, h, fill='#ECF1F8'): s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}"/>')

# ---------------------------------------------------------------- B1: why not a simpler test
s, L = [], []
TYb, LHb = 60, 46
for i in range(3):
    s.append(f'<rect x="0" y="{TYb + LHb * (i + 1) + 3}" width="1664" height="{LHb - 6}" rx="8" fill="{"#ECF1F8" if i % 2 == 0 else "#E3EAF3"}"/>')
s.append(f'<rect x="1294" y="{TYb - 6}" width="370" height="{LHb * 4 + 12}" rx="12" fill="none" stroke="#2F6BDB" stroke-width="3"/>')
L.append(lab(0, 0, 1664, '<b>The same three molecules under four tests</b> (one-sided p, each against the 4 control wells)', 'lab left', 1))
_cb = [('&#160;', ['<b>A</b>', '<b>C</b>', '<b>B</b>'], 0, 60), ('wells’ K', ['21, 24, 28, 23', '13, 15, 15, 14', '9, 10, 64, 13'], 60, 220),
       ('Fisher, nanowells pooled', [pfmt(_Q[k]['fisher_p']) for k in 'ACB'], 300, 330), ('Welch t-test on the 4 + 4 counts', [pfmt(_Q[k]['t_p']) for k in 'ACB'], 640, 320),
       ('rank test on the counts', [pfmt(_Q[k]['mw_p']) for k in 'ACB'], 970, 310), ('this test (slide 18)', ['<b>0.00008</b>', '<b>0.041</b>', '<b>0.17</b>'], 1300, 350)]
for h, v, x, w in _cb:
    L.append(lab(x, TYb, w, '<br>'.join([f'<b>{h}</b>'] + v), 'lab small', 2, f'line-height:{LHb}px; color:#1B2A41; white-space:nowrap; font-size:22px; '))
BXY = 280
for i, px in enumerate((0, 562, 1124)):
    boxp(px, BXY, 540, 300)
L.append(lab(18, BXY + 10, 504, '<b>Fisher on pooled nanowells</b><br>Adds up each group’s nanowells: 96 of 4,000 against 40 of 4,000<br>Treats 4,000 nanowells as independent<br>'
          'A and B have the same total, so they get the same p<br><b style="color:#A9501A">It calls B as strongly as A</b><br>One well at 64 is counted as 64 separate pieces of evidence', 'lab left small', 3, S20))
L.append(lab(562 + 18, BXY + 10, 504, '<b>t-test and rank test on the counts</b><br>Use one number per well, so B’s 64 is one well<br>They agree with this test on A and on B<br>'
          'Welch’s t-test assumes K is roughly normal<br>The rank test cannot go below 1/70 = 0.014 with 4 + 4 wells<br>Neither uses how many nanowells each well has', 'lab left small', 4, S20))
L.append(lab(1124 + 18, BXY + 10, 504, '<b>This test</b><br>One number per well, like the t-test<br>Knows K is a count out of n, with counting noise<br>'
          'Estimates the well-to-well scatter r from the data<br>Handles wells with different n<br>Gives an effect size, the odds ratio', 'lab left small', 5, S20))
L.append(lab(0, 596, 1664, '<b>The lesson:</b> wells are the unit of evidence, not nanowells (slide 29)', 'lab left small', 6, 'color:#1B2A41; '))
slide('b_naive', 'Backup · simpler tests', 'Why not a simpler test?',
      svg('A table of one-sided p-values for molecules A, C and B under four tests: Fisher on pooled nanowells, a t-test on the wells’ counts, a rank test on the counts, and the test of this deck. '
          'Pooled Fisher gives A and B the same tiny p; the other three agree that A is strong and B is not. Below, three boxes describing each approach.', s, h=640), L,
      f'A natural question is why this model is needed at all. The table runs the three molecules through three simpler tests. Fisher’s exact test on pooled nanowells adds up each group’s nanowells, 96 of 4,000 for A and for B against 40 of 4,000 for control, '
      f'and treats all 4,000 as independent. Because A and B have the same total it gives them the same p-value, about one in a million, so it calls B as strongly as A, although B’s total comes from a single well. '
      f'A t-test on the eight counts uses one number per well and so is not fooled: it gives {pfmt(_Q["A"]["t_p"])} for A and {pfmt(_Q["B"]["t_p"])} for B. A rank test agrees, but with four and four wells its p-value cannot go below 1 in 70. '
      'These well-level tests reach the same verdicts here. The model of this deck does what they do, one number per well, and adds what they lack: it knows the count is out of n nanowells with counting noise, it estimates the well-to-well scatter, '
      'it copes with wells of different sizes, and it reports an effect size. The lesson is that wells, not nanowells, are the unit of evidence.')

# ---------------------------------------------------------------- B2: how trustworthy is p with few wells
s, L = [], []
boxp(0, 0, 900, 560); boxp(940, 0, 724, 560)
_CAL['4+4']['p05'] = P05_44
_des = [d for d in ('4+4', '6+4', '8+4', '8+8') if d in _CAL]
def cy_(v): return 470 - v / 0.12 * 340
s.append(f'<path d="M150 {cy_(0.12):.1f} V470 H860" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
for v in (0, 0.05, 0.10):
    s.append(f'<line x1="141" y1="{cy_(v):.1f}" x2="150" y2="{cy_(v):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(60, int(cy_(0.10) - (cy_(0.05) - cy_(0.10)) / 2), 78, '10&#160;%<br>5&#160;%<br>0&#160;%', 'lab small', 1, f'line-height:{cy_(0.05) - cy_(0.10):.1f}px; text-align:right; font-size:20px; color:#4A5568; '))
s.append(f'<line x1="150" y1="{cy_(0.05):.1f}" x2="860" y2="{cy_(0.05):.1f}" stroke="{OR}" stroke-width="3" stroke-dasharray="9 6"/>')
for i, d in enumerate(_des):
    v = _CAL[d]['p05']; x = 220 + i * 160
    s.append(f'<rect x="{x}" y="{cy_(v):.1f}" width="100" height="{470 - cy_(v):.1f}" rx="6" fill="{PUR if d == "4+4" else CTRL}"/>')
    L.append(lab(x - 30, int(cy_(v)) - 34, 160, f'<b>{100 * v:.0f}&#160;%</b>', 'lab small', 2, 'font-size:22px; '))
    L.append(lab(x - 30, 478, 160, d.replace('+', ' + '), 'lab small', 2, 'font-size:21px; '))
L.append(lab(18, 10, 860, '<b>False calls when the molecule does nothing</b>', 'lab left', 1))
L.append(lab(18, 50, 860, f'Share of simulated no-effect plates with p &lt; 0.05 ({_CAL["4+4"]["plates"]:,} plates per design)', 'lab left small', 1, G20))
L.append(lab(440, 92, 430, '<b style="color:#A9501A">dashed: what p &lt; 0.05 promises, 5&#160;%</b>', 'lab small', 1, 'font-size:20px; text-align:right; '))
L.append(lab(150, 512, 710, 'control wells + molecule wells', 'lab small', 1, 'font-size:20px; color:#4A5568; '))
_c44 = dict(_CAL['4+4'], tcrit=TCRIT_44)
L.append(lab(958, 10, 690, '<b>What this means</b>', 'lab left', 3))
L.append(lab(958, 54, 690, f'With 4 + 4 wells, about {100 * _c44["p05"]:.1f}&#160;% of no-effect plates pass p &lt; 0.05<br>So p is too small, and more so the smaller it is<br>'
          f'Why: r is estimated from very few wells, and usually too small<br>More wells help only slowly: still about {100 * _CAL["8+8"]["p05"]:.1f}&#160;% with 8 + 8', 'lab left small', 3, S20))
L.append(lab(958, 220, 690, '<b>For this plate</b>', 'lab left', 4))
L.append(lab(958, 264, 690, f'In these simulations, z had to pass {_c44["tcrit"]:.2f} (not 1.64) for a true 5&#160;%<br>A: z = 3.77, far beyond it either way<br>'
          f'C: z = 1.74, below it: treat C as not shown<br>B: z = 0.95, not close', 'lab left small', 4, S20))
L.append(lab(958, 420, 690, '<b>In practice</b><br>Trust a p far below 0.05, and be wary of one just below it<br>The shuffling tests (slide 25) do not rely on this approximation', 'lab left small', 5, S20))
L.append(lab(0, 572, 1664, (f'Bars: counts drawn from the fit without a gap (rate {100 * _EXP["A"]["mu0"]:.1f}&#160;%, r = {_EXP["A"]["r0"]:.4f}), {_CAL["4+4"]["plates"]:,} plates per design<br>'
   f'Simulated end to end, from nanowells through the pooled cutoff (4 + 4, {_E2E["plates"]:,} plates): '
   f'{100 * _E2E["rates"]["0.05"]["share"]:.1f}&#160;% pass p &lt; 0.05, {100 * _E2E["rates"]["0.01"]["share"]:.1f}&#160;% pass 0.01, {100 * _E2E["rates"]["0.001"]["share"]:.2f}&#160;% pass 0.001'), 'lab left small', 6, 'color:#4A5568; font-size:20px; line-height:1.3; '))
slide('b_calib', 'Backup · how far to trust p', 'How trustworthy is p with few wells?',
      svg('A bar chart: the share of simulated no-effect plates that pass p below 0.05, for designs of 4 + 4, 6 + 4, 8 + 4 and 8 + 8 wells, against a dashed line at the promised 5 percent. '
          'The 4 + 4 bar is clearly above the line; the bars come down only a little as wells are added. Right, what that means for molecules A, C and B.', s, h=650), L,
      f'The p-value of this test rests on a large-sample approximation, so it is fair to ask how well it holds with four and four wells. We simulated plates in which the molecule does nothing, with 1,000 nanowells per well, a rate of {100 * _EXP["A"]["mu0"]:.1f} percent and r = {_EXP["A"]["r0"]:.4f}, the fit without a gap for A, '
      f'and counted how often the test gave p below 0.05. It should be 5 percent. With four control and four molecule wells it was about {100 * _c44["p05"]:.1f} percent, so the p-value is somewhat too small. '
      'The reason is that r, the well-to-well scatter, is estimated from very few wells and usually comes out too small. ' f'Adding wells helps only slowly: with eight and eight wells it was still about {100 * _CAL["8+8"]["p05"]:.1f} percent. ' f'The same holds when the plates are simulated from the nanowells up, with the cutoff estimated from each plate’s own control wells: {100 * _E2E["rates"]["0.05"]["share"]:.1f} percent of no-effect plates passed p below 0.05, {100 * _E2E["rates"]["0.01"]["share"]:.1f} percent passed 0.01 and {100 * _E2E["rates"]["0.001"]["share"]:.2f} percent passed 0.001, so the smaller the p-value, the more it overstates the evidence: about {_E2E["rates"]["0.05"]["ratio_to_nominal"]:.1f}, {_E2E["rates"]["0.01"]["ratio_to_nominal"]:.1f} and {_E2E["rates"]["0.001"]["ratio_to_nominal"]:.1f} times the promised rate. '
      f'For this plate the practical reading is simple. In the simulations, z had to exceed about {_c44["tcrit"]:.2f}, not 1.64, to keep false calls at 5 percent. A’s z of 3.77 is far beyond that. C’s z of 1.74 is below it, so C should be treated as not shown, '
      'which is also what the correction across molecules concluded. B is not close. In general, trust a p-value far below 0.05 and be wary of one just below it; the shuffling tests of slide 25 do not rely on this approximation.')

# ---------------------------------------------------------------- B3: B's well at 64, noise or biology
s, L = [], []
boxp(0, 0, 812, 330); boxp(852, 0, 812, 330)
for j, k in enumerate(B):
    s += well_icon(110 + j * 150, 170, 52, k // 2, 700 + j, sw=5)
    s += well_icon(962 + j * 150, 170, 52, k // 2, 700 + j, sw=5)
    L.append(lab(110 + j * 150 - 60, 240, 120, f'<b>K = {k}</b>', 'lab small', 1, 'font-size:21px; '))
    L.append(lab(962 + j * 150 - 60, 240, 120, f'<b>K = {k}</b>', 'lab small', 2, 'font-size:21px; '))
s.append(f'<circle cx="{110 + 2 * 150}" cy="170" r="66" fill="none" stroke="#7A8699" stroke-width="4" stroke-dasharray="8 6"/>')
s.append(f'<circle cx="{962 + 2 * 150}" cy="170" r="66" fill="none" stroke="{OR}" stroke-width="4"/>')
L.append(lab(18, 10, 780, '<b>Reading 1: the well is an accident</b>', 'lab left', 1))
L.append(lab(18, 50, 780, 'Something about that one well: seeding, DCs, position, imaging (cartoon, not to scale)', 'lab left small', 1, G20))
L.append(lab(18, 284, 780, 'B does nothing: the 64 would have happened with any molecule', 'lab left small', 1, S20))
L.append(lab(870, 10, 780, '<b>Reading 2: the well holds a real response</b>', 'lab left', 2))
L.append(lab(870, 50, 780, 'A rare T cell that recognises B landed in that well and not in the others', 'lab left small', 2, G20))
L.append(lab(870, 284, 780, 'B works, but responders are so rare that most wells have none', 'lab left small', 2, S20))
boxp(0, 350, 812, 250, '#FFFFFF'); boxp(852, 350, 812, 250, '#FFFFFF')
s.append('<rect x="0" y="350" width="812" height="250" rx="18" fill="none" stroke="#2F6BDB" stroke-width="2.5"/><rect x="852" y="350" width="812" height="250" rx="18" fill="none" stroke="#2F6BDB" stroke-width="2.5"/>')
L.append(lab(18, 360, 780, '<b>What the test can and cannot say</b><br>It asks: does B raise the rate across wells?<br>Answer: no evidence for that (p = 0.17)<br>'
          'It cannot tell reading 1 from reading 2<br>Both give one high well and three ordinary ones<br>“Not detected” is not “B does nothing”', 'lab left small', 3, S20))
L.append(lab(870, 360, 780, '<b>What would tell them apart</b><br>Repeat B with more wells: reading 2 predicts more high wells, at a low rate<br>Look inside the well: are the 64 nanowells real expansions?<br>'
          'Compare with the same donor’s other molecules: does one well keep standing out?<br>If responders are that rare, count responding wells, not nanowells', 'lab left small', 4, S20))
slide('b_jackpot', 'Backup · one extreme well', 'B’s well at 64: noise or biology?',
      svg('Two panels, each showing molecule B’s four wells as cartoons with K = 9, 10, 64 and 13, the third well circled. Left, reading 1: the well is an accident of seeding, dendritic cells, position or imaging. '
          'Right, reading 2: a rare T cell that recognises B landed in that one well. Below, what the test can and cannot say, and what would tell the two readings apart.', s, h=620), L,
      'Molecule B deserves a careful reading, because “not detected” is not the same as “does nothing”. One of B’s four wells has 64 nanowells past the cutoff; the other three look like control wells. There are two readings. '
      'In the first, the well is an accident: something about its seeding, its dendritic cells, its position on the plate or its imaging, which would have happened whatever molecule was in it. '
      'In the second, the well holds a real response: a rare T cell that recognises B happened to land in that well and not in the others, and it expanded. If responding T cells are rare enough, most wells would contain none, and one well in four lighting up is exactly what a real response would look like. '
      'The test cannot tell these apart. It asks whether B raises the rate across wells, and with one high well among four the honest answer is that there is no evidence for that. Both readings produce the same four counts. '
      'What would separate them is more data: repeating B with more wells, where a real but rare response predicts further high wells at a low rate; looking inside the well to check that the 64 nanowells are genuine expansions; '
      'and checking whether the same well position stands out for other molecules. If responses are expected to be that rare, the better design counts responding wells rather than nanowells.')

# ---------------------------------------------------------------- B4: what the effect means
s, L = [], []
boxp(0, 0, 980, 420); boxp(1016, 0, 648, 420)
_o0 = _pc / (1 - _pc)
def _mu_or(orr): o = _o0 * orr; return o / (1 + o)
_ex = [N * (_mu_or(v) - _pc) for v in (1.6926, math.exp(B1), 3.5672)]
def gx_(i): return 60 + (i % 40) * 22
def gy_(i): return 150 + (i // 40) * 22
for gi, (gy0, kk) in enumerate(((0, 10), (150, 24))):
    for i in range(120):
        col = OR if i < kk else '#C9D6E8'
        if gi == 1 and 10 <= i < 24: col = PUR
        s.append(f'<rect x="{gx_(i)}" y="{gy_(i) + gy0 - 60}" width="16" height="16" rx="3" fill="{col}"/>')
L.append(lab(18, 10, 940, '<b>Out of every 1,000 nanowells in a well</b>', 'lab left', 1))
L.append(lab(18, 52, 940, '<b>Control</b>: about 10 pass the cutoff (first 120 nanowells drawn)', 'lab left small', 1, S20))
L.append(lab(18, 202, 940, '<b style="color:#6B3FB0">With A</b>: about 24 pass, that is about <b style="color:#6B3FB0">14 more</b> (purple)', 'lab left small', 2, S20))
L.append(lab(18, 352, 940, 'The count says nothing about the other 976 nanowells of an A well', 'lab left small', 2, G20))
L.append(lab(1034, 10, 612, '<b>The same effect, three ways</b>', 'lab left', 3))
L.append(lab(1034, 56, 612, f'<b>Odds ratio</b>: {math.exp(B1):.1f} (95&#160;% interval 1.7 to 3.6)<br><b>Rate</b>: 1.0&#160;% → 2.4&#160;% of nanowells<br>'
          f'<b>Extra nanowells per 1,000</b>: about {_ex[1]:.0f} (roughly {_ex[0]:.0f} to {_ex[2]:.0f})<br>Interval for the extra count: from the odds ratio’s interval, at the control rate of 1.0&#160;%', 'lab left small', 3, 'line-height:1.4; color:#1B2A41; font-size:21px; '))
L.append(lab(1034, 262, 612, '<b>What it does not say</b><br>Not “14 responding T cells”: a nanowell past the cutoff is a nanowell whose cells expanded<br>Not the size of each expansion: only how many nanowells pass', 'lab left small', 4, S20))
L.append(lab(0, 436, 1664, 'With about 1 control nanowell in 100 passing by chance, most of the 24 in an A well are still real: 14 of 24, about 58&#160;%', 'lab left small', 5, 'color:#1B2A41; '))
slide('b_effect', 'Backup · the size of the effect', 'What the effect means',
      svg('Two grids of small squares, each the first 120 of a well’s 1,000 nanowells. Top, control: 10 orange squares, the nanowells past the cutoff. Bottom, molecule A: 24 coloured squares, the first 10 orange and 14 more in purple. '
          'Right, the same effect stated as an odds ratio, as rates and as extra nanowells per 1,000.', s, h=490), L,
      f'A p-value says how sure one can be that a difference is not chance; this slide says how large it is. In a control well about 10 of every 1,000 nanowells pass the cutoff. In a well given molecule A about 24 do. So A adds about 14 nanowells per 1,000 that expand beyond what untreated wells show. '
      f'The same effect can be stated three ways: as an odds ratio of {math.exp(B1):.1f}, with a 95 percent interval of 1.7 to 3.6; as a rise in rate from 1.0 to 2.4 percent; or as about {_ex[1]:.0f} extra nanowells per 1,000, roughly {_ex[0]:.0f} to {_ex[2]:.0f} when the odds ratio’s interval is applied to the control rate. '
      'Two cautions. A nanowell past the cutoff is a nanowell whose cells expanded strongly, not a count of responding T cells. And the test counts how many nanowells pass, not how far each one went. '
      'It also follows that in an A well, where 24 pass and about 10 would pass anyway, a little over half of the passing nanowells are attributable to the molecule.')

# ---------------------------------------------------------------- B5: the beta-binomial itself
s, L = [], []
boxp(0, 0, 812, 400); boxp(852, 0, 812, 400)
def bx_(th): return 80 + th / 0.06 * 690
def by_(d): return 340 - d / 130 * 250
ths = np.linspace(0.0005, 0.06, 160)
for (mu, rr, col, dash) in ((_mAll, R0, NAVY, ''), (_bmb / N, float(_fB1.rho), GB, '')):
    aa, bb = mu * (1 - rr) / rr, (1 - mu) * (1 - rr) / rr
    s.append('<path d="M' + ' L'.join(f'{bx_(th):.1f} {by_(min(stats.beta.pdf(th, aa, bb), 130)):.1f}' for th in ths) + f'" stroke="{col}" stroke-width="3.5" fill="none"/>')
s.append(f'<line x1="80" y1="340" x2="770" y2="340" stroke="{NAVY}" stroke-width="2.5"/>')
for th in (0, 0.02, 0.04, 0.06):
    s.append(f'<line x1="{bx_(th):.1f}" y1="340" x2="{bx_(th):.1f}" y2="349" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(int(bx_(th)) - 40, 350, 80, f'{100 * th:.0f}&#160;%', 'lab small', 1, 'font-size:19px; color:#4A5568; '))
L.append(lab(18, 10, 780, '<b>1. The Beta: which rates π<sub>w</sub> a well may have</b>', 'lab left', 1))
L.append(lab(18, 50, 780, 'x-axis: a well’s own rate π<sub>w</sub>, height: how likely that rate is', 'lab left small', 1, G20))
L.append(lab(300, 96, 480, f'<b>navy</b>: π = 1.7&#160;%, r = {R0:.4f} (step 4 for A)<br><b style="color:{GB}">green</b>: π = {_bmb / 10:.1f}&#160;%, r = {_fB1.rho:.3f} (B’s fit)', 'lab left small', 1, S20))
P2 = 852
def kx2(k): return P2 + 80 + k / 70 * 690
def ky2(p): return 340 - p / 0.105 * 250
for (mu, rr, col, dash) in ((_mAll, 0.0, '#7A8699', ' stroke-dasharray="7 5"'), (_mAll, R0, NAVY, ''), (_bmb / N, float(_fB1.rho), GB, '')):
    d_ = stats.binom(N, mu) if rr == 0 else stats.betabinom(N, mu * (1 - rr) / rr, (1 - mu) * (1 - rr) / rr)
    s.append('<path d="M' + ' L'.join(f'{kx2(k):.1f} {ky2(d_.pmf(k)):.1f}' for k in range(0, 71)) + f'" stroke="{col}" stroke-width="3.5" fill="none"{dash}/>')
s.append(f'<line x1="{kx2(0)}" y1="340" x2="{kx2(70)}" y2="340" stroke="{NAVY}" stroke-width="2.5"/>')
for k in (0, 20, 40, 60):
    s.append(f'<line x1="{kx2(k):.1f}" y1="340" x2="{kx2(k):.1f}" y2="349" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(int(kx2(k)) - 40, 350, 80, str(k), 'lab small', 2, 'font-size:19px; color:#4A5568; '))
s.append(f'<line x1="{kx2(64):.1f}" y1="250" x2="{kx2(64):.1f}" y2="340" stroke="{OR}" stroke-width="3" stroke-dasharray="5 4"/>')
L.append(lab(P2 + 18, 10, 780, '<b>2. The beta-binomial: which counts K<sub>w</sub> follow</b>', 'lab left', 2))
L.append(lab(P2 + 18, 50, 780, 'x-axis: K<sub>w</sub>, height: the chance P of that count', 'lab left small', 2, G20))
L.append(lab(P2 + 330, 96, 460, '<b style="color:#5A6679">dashed</b>: r = 0, the binomial<br><b>navy</b> and <b style="color:#2E8B57">green</b>: the two Betas on the left', 'lab left small', 2, S20))
L.append(lab(int(kx2(64)) - 150, 214, 140, '<b style="color:#A9501A">K = 64</b>', 'lab small', 2, 'font-size:20px; text-align:right; '))
L.append(lab(0, 416, 1664, f'π<sub>w</sub> ~ Beta(a, b), with a = π(1 − r) ÷ r and b = (1 − π)(1 − r) ÷ r<br>'
          f'Step 4 for A: a = {_mAll * (1 - R0) / R0:.1f}, b = {(1 - _mAll) * (1 - R0) / R0:.0f}<br>'
          'P(K<sub>w</sub> | π, r) = C(n, K<sub>w</sub>) × B(K<sub>w</sub> + a, n − K<sub>w</sub> + b) ÷ B(a, b)<br>B(·, ·): the beta function, which does the averaging over π<sub>w</sub><br>'
          'As r → 0 the Beta narrows to a spike at π, and P becomes the binomial', 'lab left small', 3, 'line-height:1.35; color:#1B2A41; font-size:21px; '))
slide('b_bb', 'Backup · the distribution', 'The beta-binomial, drawn',
      svg('Left, two Beta curves over a well’s own rate from 0 to 6 percent: a narrow one centred near 1.7 percent for r = 0.0027, and a wide, right-skewed one for B’s fit with r = 0.008. '
          'Right, the resulting curves of chances for the count K from 0 to 70: the binomial for r = 0, dashed, a wider curve for r = 0.0027, and a much wider one with a long right tail for B’s fit, under which a count of 64 is unlikely but not impossible.', s, h=600), L,
      'The model’s two stages can be drawn. The left panel is the first stage: the Beta distribution says which rates a single well may have. For step 4 of A’s test, with a mean of 1.7 percent and r = 0.0027, wells’ rates mostly lie between about 1 and 2.6 percent. '
      'For B’s fit, with r = 0.008, the curve is much wider and skewed to the right: the typical well has a rate near 1.8 percent, and 95 percent of wells lie between about 0.4 and 5.2 percent. '
      'The right panel is the second stage, after counting: the chance of each count K. With r = 0 it is the binomial, dashed. With r = 0.0027 it is wider. With B’s r it has a long right tail, which is how the model accommodates a well at 64 without believing the whole group is high. '
      'The formulas: the Beta has two shape numbers, a and b, set by the mean and r; for step 4 they are about 6.4 and 367. The chance of a count is the binomial averaged over every rate the Beta allows, which works out to a ratio of beta functions. '
      'As r goes to zero the Beta narrows to a spike at the mean and the formula becomes the plain binomial.')

# ---------------------------------------------------------------- B6: power
s, L = [], []
boxp(0, 0, 812, 520); boxp(852, 0, 812, 520)
def px_(o, px): return px + 120 + (o - 1.5) / 1.5 * 600
def pwy(v): return 440 - v * 320
for pi_, (px, des) in enumerate(((0, '4+4'), (852, '8+4'))):
    s.append(f'<path d="M{px + 120} {pwy(1.05):.1f} V440 H{px + 740}" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
    for v in (0, 0.5, 0.8, 1.0):
        s.append(f'<line x1="{px + 111}" y1="{pwy(v):.1f}" x2="{px + 120}" y2="{pwy(v):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
        L.append(lab(px + 30, int(pwy(v)) - 14, 78, f'{100 * v:.0f}&#160;%', 'lab small', 1 + pi_, 'text-align:right; font-size:19px; color:#4A5568; '))
    s.append(f'<line x1="{px + 120}" y1="{pwy(0.8):.1f}" x2="{px + 740}" y2="{pwy(0.8):.1f}" stroke="{OR}" stroke-width="2.5" stroke-dasharray="8 6"/>')
    for o in (1.5, 2.0, 2.5, 3.0):
        s.append(f'<line x1="{px_(o, px):.1f}" y1="440" x2="{px_(o, px):.1f}" y2="449" stroke="{NAVY}" stroke-width="2.5"/>')
        L.append(lab(int(px_(o, px)) - 40, 450, 80, f'{o:g}', 'lab small', 1 + pi_, 'font-size:19px; color:#4A5568; '))
    for rr, col in ((0.0, NAVY), (round(_EXP['A']['r0'], 4), '#2F6BDB'), (0.008, GB)):
        pts = [(o, _POW.get(f'{des}|{rr}|{o}')) for o in (1.5, 2.0, 2.5, 3.0)]
        pts = [(o, v) for o, v in pts if v is not None]
        if pts:
            s.append('<path d="M' + ' L'.join(f'{px_(o, px):.1f} {pwy(v):.1f}' for o, v in pts) + f'" stroke="{col}" stroke-width="3.5" fill="none"/>')
            for o, v in pts:
                s.append(f'<circle cx="{px_(o, px):.1f}" cy="{pwy(v):.1f}" r="7" fill="{col}"/>')
    L.append(lab(px + 18, 10, 780, f'<b>{des.replace("+", " control + ")} molecule wells</b>', 'lab left', 1 + pi_))
    L.append(lab(px + 120, 482, 620, 'true odds ratio (2.4 = molecule A)', 'lab small', 1 + pi_, 'font-size:19px; color:#4A5568; '))
L.append(lab(18, 50, 780, 'y-axis: share of simulated plates in which the molecule is detected (p &lt; 0.05)', 'lab left small', 1, G20))
L.append(lab(852 + 430, 300, 370, f'<b>navy</b>: r = 0<br><b style="color:#2F6BDB">blue</b>: r = {round(_EXP["A"]["r0"], 4)}<br><b style="color:{GB}">green</b>: r = 0.008', 'lab left small', 2, S20))
L.append(lab(430, 300, 370, '<b style="color:#A9501A">dashed: 80&#160;%</b>', 'lab left small', 1, 'font-size:20px; '))
L.append(lab(0, 532, 1664, 'With wells as alike as this donor’s (r near 0), 4 + 4 wells detect an effect of A’s size almost always<br>If wells scatter as B’s do (r = 0.008), the same effect is often missed, and more wells are the remedy<br>'
          f'Simulated: 1,000 nanowells per well, control rate 1.0&#160;%, {_POWN:,} plates per point, before any small-sample caution (slide 31)', 'lab left small', 3, 'line-height:1.3; color:#1B2A41; font-size:21px; '))
slide('b_power', 'Backup · power', 'How large an effect can be detected?',
      svg('Two line charts of detection rate against the true odds ratio, from 1.5 to 3. Left, 4 control and 4 molecule wells; right, 8 control and 4 molecule wells. Three lines each for r = 0, ' f'{round(_EXP["A"]["r0"], 4)}' ' and 0.008, with a dashed line at 80 percent. '
          'With r = 0 detection is high even at small effects; as r grows the lines drop, and they recover with more control wells.', s, h=640), L,
      'This slide asks the reverse question: if a molecule truly raises the rate, how often would the test find it? We simulated plates with a control rate of 1.0 percent and a true odds ratio between 1.5 and 3; molecule A’s estimate is 2.4. '
      'The vertical axis is the share of simulated plates on which the molecule was detected at p below 0.05; 80 percent is the usual target. Three lines show three amounts of well-to-well scatter. '
      'When wells are as alike as this donor’s, with r near zero, four and four wells detect an effect of A’s size almost every time, and even an odds ratio of 1.5 more often than not. '
      'When wells scatter as molecule B’s do, with r = 0.008, the same true effect is often missed with four and four wells. The right panel doubles the control wells to eight, which recovers part of that loss. '
      'So the number of wells needed depends mainly on how much wells differ, which is not known in advance; the scatter seen in the control wells of earlier plates is the best guide. '
      'These rates use the plain p below 0.05 rule; slide 31 explains why that rule is a little generous with few wells.')

# ---------------------------------------------------------------- B7: separate r per group
s, L = [], []
boxp(0, 0, 700, 520)
_axis3(100, 660)
_q = _Q['B']
for cx, ks, col, m, rr in ((240, C, CTRL, N * _q['sep_mu0'], _q['sep_rc']), (470, B, GB, N * _q['sep_mu1'], _q['sep_rt'])):
    _band3(cx, m, m / N, rr, '#C9D6E8')
    s.append(f'<line x1="{cx - 62}" y1="{dy(m):.1f}" x2="{cx + 62}" y2="{dy(m):.1f}" stroke="{NAVY}" stroke-width="4"/>')
    _dots3(cx, ks, col)
L.append(lab(18, 10, 670, '<b>B, with a scatter for each group</b>', 'lab left', 1))
L.append(lab(18, 50, 670, f'Control keeps a narrow band (r ≈ 0), B gets a wide one (r = {_q["sep_rt"]:.3f})', 'lab left small', 1, G20))
L.append(lab(180, 506, 120, '<b>control</b>', 'lab small', 1, 'font-size:20px; ')); L.append(lab(410, 506, 120, f'<b style="color:{GB}">B</b>', 'lab small', 1, 'font-size:20px; '))
TYs, LHs = 60, 44
for i in range(3):
    s.append(f'<rect x="740" y="{TYs + LHs * (i + 2) + 3}" width="924" height="{LHs - 6}" rx="8" fill="{"#ECF1F8" if i % 2 == 0 else "#E3EAF3"}"/>')
L.append(lab(740, 0, 924, '<b>One shared r, or one per group?</b>', 'lab left', 2))
L.append(lab(900, TYs, 340, '<b>one shared r (the test)</b>', 'lab small', 2, 'font-size:21px; color:#2F6BDB; '))
L.append(lab(1260, TYs, 400, '<b>one r per group (a check)</b>', 'lab small', 2, 'font-size:21px; color:#2F6BDB; '))
_cs = [('&#160;', ['<b>A</b>', '<b>C</b>', '<b>B</b>'], 750, 110), ('z', ['3.77', '1.74', '0.95'], 900, 160), ('p', ['0.00008', '0.041', '0.17'], 1060, 180),
       ('z', [f'{_Q[k]["sep_z"]:.2f}' for k in 'ACB'], 1260, 180), ('p', [pfmt(_Q[k]['sep_p']) for k in 'ACB'], 1440, 200)]
for h, v, x, w in _cs:
    L.append(lab(x, TYs + LHs, w, '<br>'.join([f'<b>{h}</b>'] + v), 'lab small', 3, f'line-height:{LHs}px; color:#1B2A41; font-size:22px; '))
L.append(lab(740, 290, 924, f'<b>What changes</b><br>A stays clearly detected, though less extreme<br>C hardly changes<br>'
          f'B moves from p = 0.17 to p = {pfmt(_q["sep_p"])}: its scatter no longer widens the control’s band', 'lab left small', 4, S20))
L.append(lab(740, 420, 924, '<b>Why the test still shares one r</b><br>A scatter estimated from 4 wells is very rough<br>Two of them, one per group, are rougher still, and p becomes less reliable<br>'
          'A shared r is the cautious choice when wells are few', 'lab left small', 5, S20))
L.append(lab(0, 536, 1664, 'So B’s verdict depends on this assumption: a reason to repeat B with more wells, not to call it either way (slide 32)', 'lab left small', 6, 'color:#1B2A41; '))
slide('b_sepr', 'Backup · the shared scatter', 'What if each group had its own r?',
      svg('Left, molecule B’s test redrawn with a separate scatter for each group: the control wells in a narrow band around 10, B’s wells in a very wide band around 24. '
          f'Right, a table of z and p for A, C and B under one shared r and under one r per group: A 3.77 and {_Q["A"]["sep_z"]:.2f}, C 1.74 and {_Q["C"]["sep_z"]:.2f}, B 0.95 and {_Q["B"]["sep_z"]:.2f}.', s, h=590), L,
      'The test assumes one r for control and molecule wells. Molecule B strains that assumption: its wells scatter far more than the control wells do. This slide shows what happens if each group is given its own r. '
      f'For A the result stays clearly significant but is less extreme, z = {_Q["A"]["sep_z"]:.2f} instead of 3.77, because the fit without a gap can now give each group its own scatter. C hardly changes, 1.74 against 1.71. '
      f'B changes most: p moves from 0.17 to {pfmt(_q["sep_p"])}. With its own r, B’s scatter no longer widens the band around the control wells, so the control wells pin their mean down tightly and B’s higher mean stands out more. '
      'Why not use this version? Because a scatter estimated from four wells is very rough, and estimating two of them makes the p-value less reliable still; the shared r is the cautious choice when wells are few. '
      'The practical conclusion is that B’s verdict depends on an assumption that cannot be checked with four wells. That is a reason to repeat B with more wells, not a reason to call it either way.')

# ---------------------------------------------------------------- B8: the control wells' own cutoff
s, L = [], []
boxp(0, 0, 812, 300); boxp(852, 0, 812, 300)
_lo = _Q['loo']
for pi_, (px, key, ttl, sub) in enumerate(((0, 'own', 'Cutoff from all 4 control wells', 'Each well’s own nanowells help set the line it is judged by'),
                                             (852, 'loo', 'Cutoff from the other 3 control wells', 'No control well helps set the bar it is measured against'))):
    def ox(k, px=px): return px + 60 + k / 40 * 700
    vals, cnts = np.unique(np.clip(_lo[key], 0, 40), return_counts=True)
    for v, c in zip(vals, cnts):
        for j in range(int(c)):
            s.append(f'<circle cx="{ox(v):.1f}" cy="{236 - j * 7.2:.1f}" r="3.4" fill="{CTRL}" stroke="{NAVY}" stroke-width="0.8"/>')
    s.append(f'<line x1="{ox(0)}" y1="244" x2="{ox(40)}" y2="244" stroke="{NAVY}" stroke-width="2.5"/>')
    for k in (0, 10, 20, 30, 40):
        s.append(f'<line x1="{ox(k)}" y1="244" x2="{ox(k)}" y2="252" stroke="{NAVY}" stroke-width="2"/>')
        L.append(lab(int(ox(k)) - 30, 252, 60, str(k), 'lab small', 1 + pi_, 'font-size:19px; color:#4A5568; '))
    L.append(lab(px + 18, 8, 780, f'<b>{ttl}</b>', 'lab left', 1 + pi_))
    L.append(lab(px + 18, 46, 780, sub, 'lab left small', 1 + pi_, G20))
    L.append(lab(px + 470, 86, 320, f'variance of K: <b>{_lo[key + "_var"]:.0f}</b>', 'lab left small', 1 + pi_, 'font-size:21px; '))
L.append(lab(0, 316, 1664, 'Each dot: one simulated control well’s K (200 of 1,600 shown), x-axis: K', 'lab left small', 3, 'color:#4A5568; font-size:20px; '))
boxp(0, 356, 1664, 200, '#FFFFFF'); s.append('<rect x="0" y="356" width="1664" height="200" rx="18" fill="none" stroke="#2F6BDB" stroke-width="2.5"/>')
L.append(lab(18, 366, 800, '<b>Why it matters</b><br>A well with slightly high fold changes pulls the pooled cutoff up<br>That hides part of its own excess, so its K moves toward 10<br>'
          'The control wells then look more alike than they are', 'lab left small', 4, S20))
L.append(lab(852, 366, 790, f'<b>The consequence</b><br>Left: variance of K {_lo["own_var"]:.0f}, right: {_lo["loo_var"]:.0f}<br>A scatter r that is too small makes p too small<br>'
          'A possible refinement: count each control well against the other three<br>'
          'On this plate it would give control counts 7, 8, 14, 13, not 7, 8, 14, 11', 'lab left small', 5, S20))
L.append(lab(0, 568, 1664, 'Simulated: 4 control wells of 1,000 nanowells, wells differing slightly in their average fold change<br>The test counts every well, control or molecule, against the one pooled cutoff', 'lab left small', 6, 'color:#4A5568; font-size:20px; line-height:1.3; '))
slide('b_loo', 'Backup · the cutoff for control wells', 'Each control well is judged by the others',
      svg('Two dot histograms of simulated control wells’ counts K from 0 to 40. Left, the cutoff is set from all four control wells including the well itself: the counts cluster tightly around 10. '
          'Right, each well’s cutoff is set from the other three: the counts spread more widely. Below, why that matters.', s, h=640), L,
      'A possible refinement of step 1. The test counts every well, control or molecule, against one pooled cutoff, the 99th percentile of all 4,000 control nanowells, so exactly 40 of those nanowells pass it. '
      'A molecule well is compared with a cutoff it had no part in setting, but a control well helps set its own. The refinement would treat control wells as molecule wells are treated: each control well counted against a cutoff from the other three control wells only. '
      'On this plate’s nanowells it would change the control counts from 7, 8, 14 and 11 to 7, 8, 14 and 13. '
      'The simulation shows why. On the left the cutoff is set from all four control wells, each well included in its own cutoff. A well whose fold changes run slightly high pulls the pooled cutoff up, which hides part of its own excess, so its count is drawn back toward 10. '
      f'The control wells then look more alike than they really are: the variance of their counts is about {_lo["own_var"]:.0f}. On the right each well is judged by the other three, and the variance is about {_lo["loo_var"]:.0f}. Neither is the true figure: with a cutoff known in advance the variance would be about {_lo["known_var"]:.0f}. The shared cutoff understates it and leave-one-out overstates it, so leave-one-out errs on the cautious side. '
      'This matters because r is estimated from how much wells of the same group differ, control wells included. If they are made to look too similar, r comes out too small, and a scatter that is too small makes every p-value too small. '
      'The simulated wells differ slightly in their average fold change, as real wells do.')

# ---------------------------------------------------------------- B9: the odds ratio's interval
s, L = [], []
boxp(0, 0, 940, 520); boxp(980, 0, 684, 520)
def qx(b): return 150 + (b + 0.1) / 1.7 * 740
def qy(v): return 440 - (v + 24.5) / 5 * 330
_bb2 = [b for b in _bs if _prof(b) > -24.4]
s.append(f'<path d="M150 {qy(-19.6):.1f} V440 H890" stroke="{NAVY}" stroke-width="2.5" fill="none"/>')
s.append('<path d="M' + ' L'.join(f'{qx(b):.1f} {qy(_prof(b)):.1f}' for b in _bb2) + f'" stroke="{NAVY}" stroke-width="4" fill="none"/>')
_thr = LL1 - 1.92; _lo_b, _hi_b = math.log(1.6926), math.log(3.5672)
s.append(f'<line x1="150" y1="{qy(_thr):.1f}" x2="890" y2="{qy(_thr):.1f}" stroke="{OR}" stroke-width="3" stroke-dasharray="9 6"/>')
for bq in (_lo_b, _hi_b):
    s.append(f'<line x1="{qx(bq):.1f}" y1="{qy(_thr):.1f}" x2="{qx(bq):.1f}" y2="440" stroke="{OR}" stroke-width="2.5" stroke-dasharray="5 4"/>')
    s.append(f'<circle cx="{qx(bq):.1f}" cy="{qy(_thr):.1f}" r="8" fill="{OR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<circle cx="{qx(B1):.1f}" cy="{qy(LL1):.1f}" r="11" fill="{PUR}" stroke="{NAVY}" stroke-width="2"/>')
s.append(f'<path d="M{qx(_lo_b):.1f} 466 H{qx(_hi_b):.1f}" stroke="{OR}" stroke-width="6"/>')
for b in (0.5, 1.0, 1.5):
    s.append(f'<line x1="{qx(b):.1f}" y1="440" x2="{qx(b):.1f}" y2="449" stroke="{NAVY}" stroke-width="2.5"/>')
    L.append(lab(int(qx(b)) - 110, 474, 220, f'δ = {b:g} (odds ×{math.exp(b):.1f})', 'lab small', 1, 'font-size:18px; color:#4A5568; '))
for v in (-20, -22, -24):
    s.append(f'<line x1="141" y1="{qy(v):.1f}" x2="150" y2="{qy(v):.1f}" stroke="{NAVY}" stroke-width="2.5"/>')
L.append(lab(60, int(qy(-20) - (qy(-22) - qy(-20)) / 2), 78, '−20<br>−22<br>−24', 'lab small', 1, f'line-height:{qy(-22) - qy(-20):.1f}px; text-align:right; font-size:19px; color:#4A5568; '))
L.append(lab(18, 10, 900, '<b>The hill of slide 15, read sideways</b>', 'lab left', 1))
L.append(lab(18, 50, 900, 'y-axis: the best log L at each δ, x-axis: δ', 'lab left small', 1, G20))
L.append(lab(int(qx(B1)) - 130, int(qy(LL1)) - 44, 260, f'<b style="color:#6B3FB0">top: δ = {B1:.2f}</b>', 'lab small', 2, 'font-size:20px; '))
L.append(lab(160, int(qy(_thr)) - 34, 260, '<b style="color:#A9501A">1.92 below the top</b>', 'lab left small', 2, 'font-size:20px; '))
L.append(lab(998, 10, 650, '<b>How the 95&#160;% interval is found</b>', 'lab left', 3))
L.append(lab(998, 56, 650, '1. Start at the top of the hill, the best δ<br>2. Draw a line 1.92 below the top<br>3. The interval is every δ whose log L is above that line<br>'
          f'Here: δ from {_lo_b:.2f} to {_hi_b:.2f}<br>As odds ratios, exp(δ): <b>1.7 to 3.6</b>', 'lab left small', 3, 'line-height:1.4; color:#1B2A41; font-size:21px; '))
L.append(lab(998, 270, 650, '<b>Why 1.92</b><br>It is the test of slide 18 run backwards<br>A value of δ is kept if testing it gives D below 3.84<br>3.84 = 1.96², and D = 2 × the drop in log L<br>So the allowed drop is 3.84 ÷ 2 = 1.92', 'lab left small', 4, S20))
L.append(lab(998, 440, 650, 'The interval is not symmetric: the hill is steeper on one side', 'lab left small', 5, G20))
L.append(lab(0, 532, 1664, 'An interval that excludes δ = 0 (odds ratio 1) goes with a two-sided p below 0.05. C’s interval, 0.96 to 2.2, includes 1'.replace('. C’s', '<br>C’s'), 'lab left small', 6, 'line-height:1.3; color:#1B2A41; font-size:21px; '))
slide('b_ci', 'Backup · the odds ratio’s interval', 'Where the 95 % interval comes from',
      svg('The log-likelihood hill of slide 15 against δ, zoomed near its top at δ = 0.89. A dashed horizontal line sits 1.92 below the top; it crosses the hill at δ = 0.53 and 1.27, and the segment between is marked as the interval. '
          'Right, the three steps, and why 1.92.', s, h=610), L,
      'The odds ratio comes with a 95 percent interval, 1.7 to 3.6 for A. It is read off the same hill as slide 15. Start at the top, the best estimate of δ, 0.89. Draw a horizontal line 1.92 units of log-likelihood below the top. '
      f'Every value of δ whose log-likelihood is above that line belongs to the interval; here that is from {_lo_b:.2f} to {_hi_b:.2f}. Taking e to those powers gives odds ratios from 1.7 to 3.6. '
      'Why 1.92? Because the interval is the test of slide 18 run backwards: a candidate value of δ is kept if a test of that value would not reject it at the 5 percent level, two-sided. '
      'That test rejects when D exceeds 3.84, the square of 1.96, and D is twice the drop in log-likelihood, so the allowed drop is 1.92. '
      'The interval is not symmetric because the hill is steeper on one side than the other. An interval that excludes zero, an odds ratio of 1, goes with a two-sided p below 0.05; molecule C’s interval, 0.96 to 2.2, includes 1.')

# ---------------------------------------------------------------- B10: donors and plates
s, L = [], []
for di in range(3):
    px = di * 562
    boxp(px, 0, 540, 300)
    for j in range(4):
        s.append(f'<circle cx="{px + 70 + j * 44}" cy="130" r="17" fill="#DCE6F2" stroke="#2F6BDB" stroke-width="3"/>')
        for mi, (mc, my) in enumerate(((PUR, 130), (OR, 190), (GB, 250))):
            if mi > 0 or True:
                s.append(f'<circle cx="{px + 310 + j * 44}" cy="{my}" r="17" fill="#FFFFFF" stroke="{mc}" stroke-width="3"/>')
    s.append(f'<path d="M{px + 244} 130 h36" stroke="{NAVY}" stroke-width="3"/><path d="M{px + 244} 130 C {px + 262} 130, {px + 262} 190, {px + 280} 190 M{px + 244} 130 C {px + 262} 130, {px + 262} 250, {px + 280} 250" stroke="{NAVY}" stroke-width="3" fill="none"/>')
    L.append(lab(px + 18, 10, 504, f'<b>Donor {di + 1}</b>', 'lab left', 1 + di))
    L.append(lab(px + 30, 62, 200, 'control wells', 'lab small', 1 + di, 'font-size:19px; color:#4A5568; '))
    L.append(lab(px + 280, 62, 240, 'wells with A, C, B', 'lab small', 1 + di, 'font-size:19px; color:#4A5568; '))
boxp(0, 320, 812, 240, '#FFFFFF'); boxp(852, 320, 812, 240, '#FFFFFF')
s.append('<rect x="0" y="320" width="812" height="240" rx="18" fill="none" stroke="#2F6BDB" stroke-width="2.5"/><rect x="852" y="320" width="812" height="240" rx="18" fill="none" stroke="#2F6BDB" stroke-width="2.5"/>')
L.append(lab(18, 330, 780, '<b>Within a donor</b><br>Each molecule’s wells are compared with that donor’s own control wells<br>T-cell responses differ between people, so donors are not mixed<br>'
          'The correction covers every molecule compared in one analysis (slide 24)<br>So analyse one donor at a time: one verdict per molecule, per donor', 'lab left small', 4, S20))
L.append(lab(870, 330, 780, '<b>Across plates and donors</b><br>A donor’s wells on several plates are treated as one set<br>No plate offset is fitted, so plate-wide differences add to the scatter r<br>'
          'Donors are not combined into one p-value<br>A molecule detected in several donors is stronger evidence than any one p', 'lab left small', 5, S20))
L.append(lab(0, 574, 1664, 'Reading across donors: say in how many donors a molecule was detected, and give each donor’s odds ratio with its interval', 'lab left small', 6, 'color:#1B2A41; '))
slide('b_donors', 'Backup · donors and plates', 'Several donors, several plates',
      svg('Three panels, one per donor. In each, four control wells are linked to three rows of four molecule wells, for molecules A, C and B: every molecule is compared with that donor’s own control wells. '
          'Below, two boxes: what happens within a donor, and what happens across plates and donors.', s, h=620), L,
      'The worked example used one donor. A real plate holds several. The comparison is always made within a donor: each molecule’s wells against that donor’s own control wells, because T-cell responses differ between people and mixing donors would add their differences to the scatter. '
      'The correction for testing several molecules covers every molecule compared in one analysis, so analysing one donor at a time gives each molecule one verdict per donor. '
      'When one donor’s wells are spread over several plates, they are treated as one set: no offset is fitted for the plate, so plate-wide differences such as the day of imaging add to the scatter between wells. There is no term for the donor either, which is why each donor is analysed alone. '
      'Donors are not combined into a single p-value. To read across donors, report in how many donors a molecule was detected and give each donor’s odds ratio with its interval. '
      'A molecule detected independently in several donors is stronger evidence than any single p-value.')

# ---------------------------------------------------------------- B11: glossary
s, L = [], []
_GL = [('n', 'nanowells in a well', '1,000'), ('cutoff', 'the control nanowells’ 99th-percentile fold change', '×2.2'),
       ('K<sub>w</sub>', 'well w’s count of nanowells above the cutoff', '7 to 28'), ('π', 'a group’s mean rate: the chance that a nanowell passes', '1.0&#160;%, 2.4&#160;%'),
       ('π<sub>w</sub>', 'well w’s own rate, scattered around its group’s π (never estimated)', '—'),
       ('r', 'how much wells differ beyond counting noise', '≈ 0, 0.0027'), ('x<sub>w</sub>', '0 for a control well, 1 for a molecule well', '0 or 1'),
       ('α', 'the baseline: the control’s log-odds (fitted in both fits)', '−4.60'), ('δ', 'the gap: the molecule’s log-odds minus the control’s', '0.89'),
       ('odds ratio', 'exp(δ): how many times higher the molecule’s odds of passing are', '2.4'),
       ('P(K<sub>w</sub> | π, r)', 'the chance of exactly that count under a guess', '0.090'), ('log L', 'the score of one guess of α, δ and r: the sum of ln P over the wells', '−20.25, −27.37'),
       ('D', '2 × (log L with a gap − log L without): never negative', '14.24'), ('z', '√D with the sign of δ: positive when the molecule is higher', '3.77'),
       ('p', 'the chance of a z this large if the molecule did nothing', '0.00008'), ('q', 'p corrected for the number of molecules tested', '0.00024')]
LHg = 33
for i in range(len(_GL)):
    if i % 2 == 0:
        s.append(f'<rect x="0" y="{44 + i * LHg + 2}" width="1664" height="{LHg - 2}" rx="6" fill="#ECF1F8"/>')
L.append(lab(18, 4, 300, '<b>Symbol</b>', 'lab left small', 1)); L.append(lab(340, 4, 900, '<b>Meaning</b>', 'lab left small', 1)); L.append(lab(1320, 4, 330, '<b>Molecule A’s value</b>', 'lab left small', 1))
_gs = 'line-height:%dpx; color:#1B2A41; font-size:21px; white-space:nowrap; ' % LHg
L.append(lab(18, 44, 310, '<br>'.join(f'<b>{a}</b>' for a, _, _ in _GL), 'lab left small', 2, _gs))
L.append(lab(340, 44, 960, '<br>'.join(b for _, b, _ in _GL), 'lab left small', 2, _gs))
L.append(lab(1320, 44, 330, '<br>'.join(c for _, _, c in _GL), 'lab left small', 2, _gs))
slide('b_gloss', 'Backup · symbols', 'Every symbol, in one place',
      svg('A three-column table: each symbol used in the statistics slides, its meaning in a phrase, and its value for molecule A against control.', s, h=620), L,
      'A reference slide. Every symbol used in the statistics section, what it means, and the value it takes for molecule A against control: n, the cutoff, the count K, the group rate π and its per-well label, the well’s own rate π<sub>w</sub>, the scatter r, '
      'the well indicator x, α and δ, the chance P of a count, the log-likelihood, the likelihood ratio, z, p and q.')

# ---- subscripts and superscripts as Unicode characters: the phone viewer drops <sub>/<sup>, which turned K-sub-w into "Kw"
import re as _re3, glob as _gl3
_SUBM = {'w': 'ᵢ', 'i': 'ᵢ', 'j': 'ⱼ', 'n': 'ₙ', 'R': 'ᵣ', 't': 'ₜ', '(j)': '₍ⱼ₎', **{str(d): '₀₁₂₃₄₅₆₇₈₉'[d] for d in range(10)}}
_SUPM = {c: u for c, u in zip('0123456789+−-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻')}
def _uni(sid, t):
    if sid == 'st_supp':   # there i indexes nanowells; wells are i everywhere else, so nanowells become j
        t = t.replace('>(i)</sub>', '>(j)</sub>').replace('>i</sub>', '>j</sub>').replace('i-th', 'j-th').replace('i/n', 'j/n').replace('Only i ≤', 'Only j ≤').replace('near i over n', 'near j over n').replace('i*', 'j*')
    def sb(m):
        assert m.group(1) in _SUBM, (sid, m.group(1)); return _SUBM[m.group(1)]
    t = _re3.sub(r'<sub[^>]*>([^<]*)</sub>', sb, t)
    t = _re3.sub(r'<sup[^>]*>([^<]*)</sup>', lambda m: ''.join(_SUPM[c] for c in m.group(1)), t)
    t = _re3.sub(r'\bwell w\b', 'well i', t).replace('K-w', 'K for well i').replace('π-w', 'π for well i')
    assert '<sub' not in t and '<sup' not in t, sid
    if _re3.search(r'(?<![A-Za-z<&/"=#-])[KNLPxπβ][a-z](?![A-Za-z"=])', _re3.sub(r'<[^>]+>', '\x00', t).replace('No', '').replace('Na', '')): BAD.append((sid, 'two-letter variable after conversion'))
    return t
# ---- every slide title is a full declarative sentence (the cover and the Backup divider keep their names)
_TITLES = {
 'dct': 'A DC–T cell assay asks whether a drug wakes up T cells',
 'why': 'A few responders get lost in a bulk culture',
 'divide': 'Dividing one big well into many small ones isolates responders',
 'platemap': 'Each plate holds six donors with 4 wells per condition',
 'cells': 'DCs and T cells fill the nanowells of a well',
 'nanowell': 'One nanowell is followed over time',
 'fcdots': 'Two wells are shown at the start and end of imaging',
 'exceedance': 'One cutoff turns each well into one count',
 'st_vary': 'Wells’ counts K differ through counting noise and well-to-well scatter',
 'st_road': 'Six steps lead from images to one corrected p-value',
 'n_model': 'Each group has one rate, and wells scatter around it',
 'n_logodds': 'Log-odds give a baseline α and a gap δ',
 'n_lik': 'The log-likelihood scores any guess of α, δ and r',
 'n_mle': 'Maximum likelihood finds the best-scoring guess',
 'st_ll': 'The two fits explain each well differently',
 'n_r': 'With no gap allowed, the one-group fit raises r',
 'n_test': 'The test asks whether the gap is more than chance',
 'n_ab': 'A and B share an average but get different verdicts',
 'n_recap': 'The same six steps give A and B different verdicts',
 'n_assume': 'The test makes assumptions and reports numbers beside the verdict',
 'st_plan': 'A result counts only when three conditions hold',
 'st_bh': 'Testing 3 molecules means correcting their p-values',
 'st_supp': 'Tests B and C each make a p-value in their own way',
 'st_shift': 'A shift of the whole distribution also raises the count',
 'cutoff': 'The top 1&#160;% is a trade-off between extremeness and numbers',
 'st_r': 'r is measured from replicate wells, and only roughly',
 'st_wells': 'Replicate wells are the evidence, not nanowells',
 'b_naive': 'A simpler test that pools nanowells would call B on one well',
 'b_calib': 'With few wells, p comes out somewhat too small',
 'b_jackpot': 'B’s well at 64 may be noise or biology, and one plate cannot tell',
 'b_effect': 'The effect is an odds ratio between molecule and control',
 'b_bb': 'The beta-binomial is a binomial widened by r',
 'b_power': 'Larger effects and steadier wells are easier to detect',
 'b_sepr': 'With its own r per group, B’s p would fall from 0.17 to 0.03',
 'b_loo': 'Judging each control well by the other three is a cautious refinement',
 'b_ci': 'The 95&#160;% interval holds every gap the test would not reject',
 'b_donors': 'Each donor is tested on its own, and plates are not modelled',
 'b_gloss': 'Every symbol is defined in one place',
}
assert set(_ORDER) - set(_TITLES) == {'cover', 'backup'}, set(_ORDER) - set(_TITLES)
for _f in _gl3.glob('project/slides/*.html'):
    _sid = _f.split('/')[-1][:-5]
    if _sid in _ORDER:
        _t = open(_f).read(); _u = _uni(_sid, _t)
        if _sid in _TITLES:
            _u, _n = _re3.subn(r'<h2>.*?</h2>', lambda m: '<h2>' + _TITLES[_sid] + '</h2>', _u, count=1, flags=_re3.S); assert _n == 1, _sid
        if _u != _t: open(_f, 'w').write(_u)

# slide 27 is hand-laid-out; its table and notes are filled from results/cutoff_tradeoff.json
import slide_cutoff as _sc
_sc.main('project/slides/cutoff.html')
import sys
for _b in BAD: print('BAD', *_b)
if BAD: sys.exit(1)
