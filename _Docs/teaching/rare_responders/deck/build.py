"""Build the deck: generate slides 10 to 40, drop retired slides, then move any label that overflows its slide.

    python build.py

Steps: gen_stats2.py writes project/slides/*.html and fills slide 27 from results/cutoff_tradeoff.json; slides the
generator still writes but the deck no longer shows are deleted; chk.js measures every label in a headless browser
(Playwright) and fixhost.py moves labels that overflow their box; chk.js runs again and must report nothing.
Slides 1 to 9 are drawn by hand in project/slides/ and are not generated.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RETIRED = ['st_sources', 'st_inflate', 'st_model', 'st_effect', 'st_bh_old', 'st_ml', 'st_dials', 'st_gap', 'st_test', 'exctest']


def sh(cmd):
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{" ".join(cmd)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}')
    return r.stdout


if __name__ == '__main__':
    sh([sys.executable, 'gen_stats2.py'])
    for sid in RETIRED:
        (HERE / 'project' / 'slides' / f'{sid}.html').unlink(missing_ok=True)
    sh(['node', 'chk.js']); sh([sys.executable, 'fixhost.py']); sh(['node', 'chk.js'])
    over = json.loads((HERE / 'over.json').read_text()); (HERE / 'over.json').unlink()
    if over:
        sys.exit(f'labels still overflow: {over}')
    titles = sh(['node', 'h2chk.js'])
    if titles.strip() != 'done':
        sys.exit(f'titles wrap:\n{titles}')
    print('deck built')
