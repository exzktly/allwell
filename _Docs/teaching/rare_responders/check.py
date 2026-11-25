"""Check that the teaching material still agrees with the code and the simulations behind it.

    python check.py            # quick: about a minute
    python check.py --sims     # also rerun every simulation at its stored seed and size (about 20 minutes on 4 cores)

What it checks:
1. The example plate's derived values: sims/example_plate.py is rerun and must reproduce results/example_plate.json.
   This also catches a change in the app's fitting code, which every value here goes through.
2. The deck: deck/build.py is rerun on a copy of deck/ and every slide must come out byte for byte as stored.
3. The chapter, which is edited by hand: every quoted value listed in CLAIMS below must appear in
   chapter/detecting-rare-responders.html, formatted from the current results.
4. With --sims, each simulation is rerun and its results file must be reproduced exactly.
Exit status 0 means everything agreed; the first disagreement is printed.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / 'results'
PY = sys.executable
SIMS = ['example_plate.py', 'null_gain.py', 'loo_variance.py', 'cutoff_tradeoff.py', 'end_to_end_null.py', 'calibration.py', 'power.py']
fails = []


def m(x, d=2):
    """A number as the documents print it, with a true minus sign."""
    return f'{x:.{d}f}'.replace('-', '−')


def claims():
    ex = json.loads((RES / 'example_plate.json').read_text())['A']
    ng = json.loads((RES / 'null_gain.json').read_text())
    return [m(ex['l1']), m(ex['l0']), f"{ex['D']:.2f}", f"{ex['z']:.2f}", f"r = {ex['r0']:.4f}", m(ex['alpha']), f"{ex['delta']:.2f}",
            f"{ex['ci'][0]:.1f} to {ex['ci'][1]:.1f}", f"{ng['gain_p95']:.2f}", f"{ng['example_gain']:.2f}", '7, 8, 14', '21, 24, 28']


def run(args, cwd):
    r = subprocess.run([PY] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        fails.append(f'{" ".join(args)} failed:\n{r.stderr[-2000:]}')
    return r


def same_json(a, b, keys_to_skip=('seconds',)):
    A, B = json.loads(Path(a).read_text()), json.loads(Path(b).read_text())
    for k in keys_to_skip:
        A.pop(k, None); B.pop(k, None)
    return A == B


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1 and 4: rerun simulations on a copy, so the stored results stay untouched
        shutil.copytree(HERE, tmp / 'pkg', ignore=shutil.ignore_patterns('__pycache__', '*.png'))
        # the copy must sit at the same depth below the repository root for the scripts' imports
        root = HERE.parents[2]
        work = root / '_Docs' / 'teaching' / ('.check_' + tmp.name)
        shutil.copytree(tmp / 'pkg', work)
        try:
            for script in (SIMS if '--sims' in sys.argv else SIMS[:1]):
                run([script], work / 'sims')
                out = {'null_gain.py': 'null_gain.json'}.get(script, script.replace('.py', '.json'))
                if not fails and not same_json(work / 'results' / out, RES / out):
                    fails.append(f'{script} did not reproduce results/{out}')
                if script == 'null_gain.py' and not fails:
                    if (work / 'results' / 'null_gain.npy').read_bytes() != (RES / 'null_gain.npy').read_bytes():
                        fails.append('null_gain.py did not reproduce results/null_gain.npy')
                if fails:
                    break
            # 2: rebuild the deck on the copy and compare every slide
            if not fails:
                run(['build.py'], work / 'deck')
                for f in (work / 'deck' / 'project' / 'slides').glob('*.html'):
                    ref = HERE / 'deck' / 'project' / 'slides' / f.name
                    if ref.exists() and f.read_bytes() != ref.read_bytes():
                        fails.append(f'deck: slide {f.name} differs from the stored one')
        finally:
            shutil.rmtree(work, ignore_errors=True)
    # 3: the chapter's quoted values
    text = (HERE / 'chapter' / 'detecting-rare-responders.html').read_text()
    for c in claims():
        if c not in text:
            fails.append(f'chapter: expected to find "{c}"')
    if fails:
        print('\n'.join(fails)); sys.exit(1)
    print('All checks agree.')


if __name__ == '__main__':
    main()
