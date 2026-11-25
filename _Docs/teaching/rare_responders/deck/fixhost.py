"""Grow each host div so every pinned child lies inside it: margin -d / padding +d on the overflowing sides,
and shift the host's direct pinned children by the left/top amounts. Visual result unchanged in a browser."""
import json, re
import os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'project', 'slides') + '/'
over = json.load(open('over.json'))
for rec in over:
    f = D + rec['id'] + '.html'; t = open(f).read()
    starts = [m.start() for m in re.finditer(r'<div\b', t)]
    s = starts[rec['hi']]; e = t.index('>', s)
    tag = t[s:e]
    l, tp, r, b = rec['l'] + 2 * (rec['l'] > 0), rec['t'] + 2 * (rec['t'] > 0), rec['r'] + 2 * (rec['r'] > 0), rec['b'] + 2 * (rec['b'] > 0)
    add = ''.join(f'margin-{side}:-{v}px; padding-{side}:{v}px; ' for side, v in (('left', l), ('top', tp), ('right', r), ('bottom', b)) if v)
    if 'style="' in tag:
        newtag = tag.replace('style="', 'style="' + add, 1)
    else:
        newtag = tag + f' style="{add.strip()}"'
    # find end of host: walk div depth
    i, depth = e + 1, 1
    for m in re.finditer(r'<div\b|</div>', t[e + 1:]):
        depth += 1 if m.group() != '</div>' else -1
        if depth == 0:
            end = e + 1 + m.start(); break
    body = t[e + 1:end]
    # shift direct children (depth 0 inside body) that carry left/top px
    out, pos, d = [], 0, 0
    for m in re.finditer(r'<div\b|</div>|<p\b[^>]*>', body):
        tok = m.group()
        if tok.startswith('</div'):
            d -= 1; continue
        if d == 0 and 'position:absolute' in tok or (d == 0 and tok.startswith('<p') and 'class="lab' in tok) or (d == 0 and tok.startswith('<p') and 'class="mm' in tok):
            nt = tok
            if l: nt = re.sub(r'left:(-?[\d.]+)px', lambda q: f'left:{float(q.group(1)) + l:g}px', nt, 1)
            if tp: nt = re.sub(r'top:(-?[\d.]+)px', lambda q: f'top:{float(q.group(1)) + tp:g}px', nt, 1)
            out.append(body[pos:m.start()]); out.append(nt); pos = m.end()
        if tok.startswith('<div'):
            d += 1
    out.append(body[pos:])
    t = t[:s] + newtag + t[e:e + 1] + ''.join(out) + t[end:]
    open(f, 'w').write(t)
    print('fixed', rec['id'], rec['hi'], l, tp, r, b)
