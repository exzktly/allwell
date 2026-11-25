"""The slide-9 well cartoon, used by the deck generator for small well icons."""
import math, random
OR, FCCOL, FCW = '#E07A2F', ['#E3EBF5', '#B1C4DC', '#98B0D0', '#7F9CC4', '#4D6382', '#1B2A41', '#1B2A41'], [3, 9, 10, 8, 4, 1, 1]   # the deck generator's colours
def icon(k, seed, px):
    """Slide-9 well cartoon (blue ring, fold-change squares, K orange), drawn at r=58 with pitch 10 and shown at px."""
    rnd = random.Random(seed); r, pitch, sw = 58, 10.0, 4; sq = pitch * 0.79; cx = cy = 60
    n = int(r // pitch) + 1
    cells = [(cx + i * pitch, cy + j * pitch) for i in range(-n, n + 1) for j in range(-n, n + 1)
             if math.hypot(i * pitch, j * pitch) + pitch * 0.72 <= r - sw]
    assert len(cells) >= k, len(cells)
    hot = set(rnd.sample(range(len(cells)), k))
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#F7F5F0" stroke="#2F6BDB" stroke-width="{sw}"/>']
    for idx, (x, y) in enumerate(cells):
        col = OR if idx in hot else rnd.choices(FCCOL, FCW)[0]
        out.append(f'<rect x="{x-sq/2:.0f}" y="{y-sq/2:.0f}" width="{sq:.0f}" height="{sq:.0f}" rx="2" fill="{col}"/>')
    return (f'<svg aria-label="a well with K = {k} nanowells past the cutoff" width="{px}" height="{px}" viewBox="0 0 120 120" '
            f'style="width:{px}px; height:{px}px; vertical-align:middle; display:inline-block">' + ''.join(out) + '</svg>')
