"""Statistics.

Three decisions here matter more than the arithmetic:

1. **Resampling is clustered, not IID.** 300 synthetic documents drawn from 10
   templates do not carry 300 documents' worth of independent information about
   layout-sensitive failure. Two documents from the same template fail in
   correlated ways, so an IID bootstrap over documents reports an interval that
   is too narrow -- often by a factor of two -- and then invites a "winner"
   claim the data cannot support. The resampling unit is the cluster (template
   for synthetic, source document for degraded variants).

2. **Field instances are not independent either.** Fields within one document
   share the same image, the same skew, the same lighting. So every interval is
   computed by resampling *documents* (within clusters) and recomputing the
   field-level statistic on the resampled set -- never by treating N documents x
   M fields as N*M independent Bernoulli trials, which is the standard way this
   gets done and the standard way intervals end up three times too tight.

3. **Comparisons are paired.** The same documents go to every model, so
   McNemar's test on the discordant pairs is the correct test and the
   two-proportion z-test is simply the wrong one -- it discards the pairing and
   loses most of the power. For small discordant counts the exact binomial
   version is used rather than the chi-square approximation.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np
from scipy import stats as sps

DEFAULT_B = 10000


class Interval(NamedTuple):
    point: float
    lo: float
    hi: float
    n: int
    n_clusters: int
    method: str

    def __str__(self):
        return "%.1f%% [%.1f, %.1f]" % (100 * self.point, 100 * self.lo, 100 * self.hi)


def cluster_bootstrap(units: Sequence, statistic: Callable[[Sequence], float],
                      cluster_of: Callable[[object], object],
                      B: int = DEFAULT_B, alpha: float = 0.05,
                      seed: int = 0) -> Interval:
    """Percentile bootstrap resampling whole clusters with replacement.

    `units` are documents. `statistic` maps a list of documents to a number.
    `cluster_of` maps a document to its cluster key.
    """
    if not units:
        return Interval(float("nan"), float("nan"), float("nan"), 0, 0, "cluster_bootstrap")
    by_cluster: Dict[object, List] = {}
    for u in units:
        by_cluster.setdefault(cluster_of(u), []).append(u)
    keys = list(by_cluster)
    rng = random.Random(seed)

    point = statistic(list(units))
    draws = []
    for _ in range(B):
        sample = []
        for _ in range(len(keys)):
            sample.extend(by_cluster[keys[rng.randrange(len(keys))]])
        v = statistic(sample)
        if not (isinstance(v, float) and math.isnan(v)):
            draws.append(v)
    if not draws:
        return Interval(point, float("nan"), float("nan"), len(units), len(keys),
                        "cluster_bootstrap")
    lo, hi = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return Interval(point, float(lo), float(hi), len(units), len(keys),
                    "cluster_bootstrap(B=%d)" % B)


class McNemar(NamedTuple):
    n01: int          # A wrong, B right
    n10: int          # A right, B wrong
    p_value: float
    method: str
    a_better: Optional[bool]

    def verdict(self, alpha: float = 0.05) -> str:
        if self.p_value >= alpha:
            return ("statistically indistinguishable (p=%.3f, discordant %d/%d)"
                    % (self.p_value, self.n10, self.n01))
        return ("%s better (p=%.4f, discordant %d/%d)"
                % ("A" if self.a_better else "B", self.p_value, self.n10, self.n01))


def mcnemar(a_correct: Sequence[bool], b_correct: Sequence[bool],
            exact_threshold: int = 25) -> McNemar:
    """Paired test on the same documents.

    Only the discordant pairs carry information: documents both models get
    right, or both get wrong, say nothing about which is better."""
    if len(a_correct) != len(b_correct):
        raise ValueError("paired test needs equal-length sequences")
    n01 = sum(1 for a, b in zip(a_correct, b_correct) if (not a) and b)
    n10 = sum(1 for a, b in zip(a_correct, b_correct) if a and (not b))
    n = n01 + n10
    if n == 0:
        return McNemar(0, 0, 1.0, "no discordant pairs", None)
    if n < exact_threshold:
        p = float(sps.binomtest(min(n01, n10), n, 0.5).pvalue)
        method = "exact binomial"
    else:
        chi2 = (abs(n01 - n10) - 1) ** 2 / n      # continuity-corrected
        p = float(sps.chi2.sf(chi2, 1))
        method = "chi-square with continuity correction"
    return McNemar(n01, n10, p, method, n10 > n01)


def paired_delta_ci(a_vals: Sequence[float], b_vals: Sequence[float],
                    clusters: Sequence, B: int = DEFAULT_B, seed: int = 0,
                    alpha: float = 0.05) -> Interval:
    """Bootstrap CI for the mean paired difference (A - B), clustered.

    Reported alongside McNemar because a p-value says whether a difference
    exists and this says how large it plausibly is. A benchmark that publishes
    only the former invites readers to treat a 0.4-point gap as meaningful."""
    trip = list(zip(a_vals, b_vals, clusters))
    return cluster_bootstrap(
        trip,
        lambda rows: float(np.mean([a - b for a, b, _ in rows])) if rows else float("nan"),
        lambda row: row[2], B=B, alpha=alpha, seed=seed)


def holm_bonferroni(pvalues: Dict[str, float], alpha: float = 0.05) -> Dict[str, bool]:
    """With 6 models there are 15 pairwise comparisons; at alpha=0.05 you expect
    roughly one spurious 'winner' by chance alone. Holm-Bonferroni controls the
    family-wise error rate without the conservatism of plain Bonferroni."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: Dict[str, bool] = {}
    reject = True
    for i, (k, p) in enumerate(items):
        if reject and p <= alpha / (m - i):
            out[k] = True
        else:
            reject = False
            out[k] = False
    return out


def min_detectable_difference(n: int, p: float = 0.9, rho: float = 0.35,
                              alpha: float = 0.05, power: float = 0.8) -> float:
    """Roughly the smallest accuracy gap this corpus size can resolve.

    Worth computing *before* the sweep, not after: it tells you whether the
    planned n can answer the question at all, and if it cannot, the honest
    move is to plan for "indistinguishable" as a legitimate headline result
    rather than to squint at overlapping intervals afterwards.

    `rho` is the assumed within-cluster correlation of per-document correctness.
    """
    z_a = sps.norm.ppf(1 - alpha / 2)
    z_b = sps.norm.ppf(power)
    n_eff = n / (1 + rho)          # paired design already removes much of the
                                   # between-document variance; this is the
                                   # residual clustering penalty
    se = math.sqrt(2 * p * (1 - p) / n_eff)
    return (z_a + z_b) * se
