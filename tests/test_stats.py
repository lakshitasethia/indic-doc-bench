import random

from idb.stats import (cluster_bootstrap, holm_bonferroni, mcnemar,
                       min_detectable_difference)


def test_mcnemar_ignores_concordant_pairs():
    a = [True] * 90 + [True] * 5 + [False] * 5
    b = [True] * 90 + [False] * 5 + [True] * 5
    m = mcnemar(a, b)
    assert m.n01 == 5 and m.n10 == 5
    assert m.p_value > 0.5


def test_mcnemar_uses_the_exact_test_when_discordant_counts_are_small():
    a = [True] * 98 + [True, False]
    b = [True] * 98 + [False, True]
    assert "exact" in mcnemar(a, b).method


def test_mcnemar_detects_a_one_sided_difference():
    a = [True] * 80 + [True] * 18 + [False] * 2
    b = [True] * 80 + [False] * 18 + [True] * 2
    m = mcnemar(a, b)
    assert m.p_value < 0.001 and m.a_better


def test_clustered_bootstrap_is_wider_when_clusters_are_real():
    """The whole reason for clustering: correlated documents carry less
    information than their count suggests, and an IID interval hides that."""
    rng = random.Random(0)
    docs = []
    for t in range(10):
        p = 0.6 if t < 5 else 0.95          # templates differ systematically
        for _ in range(40):
            docs.append((t, rng.random() < p))
    stat = lambda rows: sum(1 for _, ok in rows if ok) / len(rows)
    clustered = cluster_bootstrap(docs, stat, lambda d: d[0], B=1500)
    iid = cluster_bootstrap(docs, stat, lambda d: id(d), B=1500)
    # On this fixture the IID interval comes out roughly 3x too narrow. That
    # factor is the size of the mistake an unclustered bootstrap makes, and it
    # is easily enough to turn "indistinguishable" into a spurious winner.
    assert (clustered.hi - clustered.lo) > 2.5 * (iid.hi - iid.lo)


def test_holm_stops_at_the_first_failure():
    out = holm_bonferroni({"a": 0.001, "b": 0.30, "c": 0.04})
    assert out["a"] and not out["b"] and not out["c"]


def test_minimum_detectable_difference_shrinks_with_n():
    assert min_detectable_difference(100) > min_detectable_difference(1000)
