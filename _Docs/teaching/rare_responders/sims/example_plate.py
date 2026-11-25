"""Every derived value of the example plate: the fits with and without a gap, the test, the odds ratio and its interval,
the naive tests (pooled Fisher, Welch t, exact rank test, relabelling on the mean count), the fit with a separate r per
group, and the spread of the wells. Control wells 7, 8, 14, 11, as counted against the pooled 99th-percentile cutoff.
Writes ../results/example_plate.json.  Usage: python example_plate.py
"""
import json, math, itertools, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from scipy import stats, optimize
from review.features.nanowell import rare_exceedance_compute as ex
N = 1000
C = [7, 8, 14, 11]; MOL = {'A': [21, 24, 28, 23], 'B': [9, 10, 64, 13], 'C': [13, 15, 15, 14]}
def fits(c, t):
    k = np.array(c + t, float); n = np.full(len(k), float(N)); X = np.column_stack([np.ones(len(k)), [0] * len(c) + [1] * len(t)])
    return ex.fit_bb(k, n, X), ex.fit_bb(k, n, X, fixed={1: 0.0})
def bbpmf(k, mu, r):
    if r < 1e-7: return stats.binom.pmf(k, N, mu)
    a = mu * (1 - r) / r; b = (1 - mu) * (1 - r) / r; return stats.betabinom.pmf(k, N, a, b)
inv = lambda x: 1 / (1 + math.exp(-x))
out = {'control': C}
for m, T in MOL.items():
    f1, f0 = fits(C, T); D = 2 * (f1.loglik - f0.loglik); z = math.copysign(math.sqrt(max(D, 0)), f1.coef[1]); p = float(stats.norm.sf(z))
    res = ex.beta_binomial_test([ex.WellCount(f"c{i}", "P", "control", False, k, N, {}, {}) for i, k in enumerate(C)] + [ex.WellCount(f"t{i}", "P", "drug", True, k, N, {}, {}) for i, k in enumerate(T)], 99.0)
    mc, mt, m0 = inv(f1.coef[0]), inv(f1.coef[0] + f1.coef[1]), inv(f0.coef[0])
    d = dict(T=T, alpha=f1.coef[0], delta=f1.coef[1], r1=f1.rho, l1=f1.loglik, mu_c=mc, mu_t=mt, mu0=m0, r0=f0.rho, l0=f0.loglik, D=D, z=z, p=p, OR=math.exp(f1.coef[1]), ci=list(res.ci),
             p1=[float(bbpmf(k, mc, f1.rho)) for k in C] + [float(bbpmf(k, mt, f1.rho)) for k in T], p0=[float(bbpmf(k, m0, f0.rho)) for k in C + T])
    # what-if: one rate, r forced to 0
    pooled = (sum(C) + sum(T)) / (N * 8); d['l0_r0'] = float(sum(stats.binom.logpmf(k, N, pooled) for k in C + T))
    # naive tests
    d['fisher_p'] = float(stats.fisher_exact([[sum(T), N * 4 - sum(T)], [sum(C), N * 4 - sum(C)]], alternative='greater')[1])
    d['welch_p'] = float(stats.ttest_ind(T, C, equal_var=False, alternative='greater').pvalue)
    v = np.array(C + T, float); rk = stats.rankdata(v); obs = rk[4:].sum()
    d['rank_p'] = sum(1 for c in itertools.combinations(range(8), 4) if rk[list(c)].sum() >= obs - 1e-9) / 70
    d['perm_mean_p'] = sum(1 for c in itertools.combinations(range(8), 4) if v[list(c)].sum() >= sum(T) - 1e-9) / 70
    # separate r per group: profile over gap with own r per group
    def nll(par, gap_free):
        a, dl, lr1, lr2 = par; dl = dl if gap_free else 0.0
        r1, r2 = 1 / (1 + math.exp(-lr1)) * 0.2, 1 / (1 + math.exp(-lr2)) * 0.2
        s = 0
        for k in C: s -= math.log(max(bbpmf(k, inv(a), r1), 1e-300))
        for k in T: s -= math.log(max(bbpmf(k, inv(a + dl), r2), 1e-300))
        return s
    best1 = min((optimize.minimize(nll, [f1.coef[0], f1.coef[1], g1, g2], args=(True,), method='Nelder-Mead', options={'xatol': 1e-7, 'fatol': 1e-9, 'maxiter': 20000}) for g1 in (-12, -5, -2) for g2 in (-12, -5, -2)), key=lambda r: r.fun)
    best0 = min((optimize.minimize(nll, [f0.coef[0], 0, g1, g2], args=(False,), method='Nelder-Mead', options={'xatol': 1e-7, 'fatol': 1e-9, 'maxiter': 20000}) for g1 in (-12, -5, -2) for g2 in (-12, -5, -2)), key=lambda r: r.fun)
    Ds = 2 * (best0.fun - best1.fun); d['sep_z'] = math.copysign(math.sqrt(max(Ds, 0)), best1.x[1]); d['sep_p'] = float(stats.norm.sf(d['sep_z']))
    d['sep_r'] = [1 / (1 + math.exp(-best1.x[2])) * 0.2, 1 / (1 + math.exp(-best1.x[3])) * 0.2]
    d['sep_b1'] = float(best1.x[1]); d['sep_mu0'] = inv(best1.x[0]); d['sep_mu1'] = inv(best1.x[0] + best1.x[1])
    d['fisher_p'] = float(stats.fisher_exact([[sum(T), N * 4 - sum(T)], [sum(C), N * 4 - sum(C)]], alternative='greater')[1])
    d['welch_t'] = float(stats.ttest_ind(T, C, equal_var=False).statistic)
    out[m] = d
ps = sorted([(out[m]['p'], m) for m in MOL]); q = {}
prev = 1.0
for i in range(len(ps) - 1, -1, -1):
    pv, m = ps[i]; prev = min(prev, pv * len(ps) / (i + 1)); q[m] = prev
for m in MOL: out[m]['q'] = q[m]; out[m]['rank'] = [mm for _, mm in ps].index(m) + 1
out['ctrl_mean'] = float(np.mean(C)); out['ctrl_var'] = float(np.var(C, ddof=1)); out['ctrl_shot'] = float(np.mean(C) * (1 - np.mean(C) / N))
out['A_var'] = float(np.var(MOL['A'], ddof=1)); out['A_shot'] = float(np.mean(MOL['A']) * (1 - np.mean(MOL['A']) / N))
out['all8_var'] = float(np.var(C + MOL['A'], ddof=1)); out['all8_shot'] = out['A']['mu0'] * N * (1 - out['A']['mu0'])
json.dump(out, open(Path(__file__).resolve().parents[1] / 'results' / 'example_plate.json', 'w'), indent=1)
for m in 'ACB':
    d = out[m]; print(m, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in d.items() if k not in ('p1', 'p0', 'T')})
print({k: round(v, 3) for k, v in out.items() if isinstance(v, float)})
