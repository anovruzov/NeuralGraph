"""H8 / NULL-ADVERSARY shared loader + metrics.  Read-only on every repo; writes nothing.

Pool  : raw/plateau/candidate_pool.csv, detector_state == rule_only AND rule_weeks >= 7  (wave-0 filter, 1,765 keys)
Target: gold (1) vs decoy (0).  'other' keys are kept for the FULL-pool precision numbers only.
Dumps : scratchpad/dumps/seed-{0..5}.json -> candidates[key] = {rule_weeks[], candidate_weeks[], channels[], ...}
"""
import csv, json, hashlib, math, os
import numpy as np

R = "/home/user/ng-recovery/research/emergence_two_track_20261024"
POOL = f"{R}/raw/plateau/candidate_pool.csv"
DUMPS = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/dumps"
OUT = "/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H8"
SEEDS = range(6)
MIN_RULE_WEEKS = 7


def _wk(w):  # '2024-W13' -> 13
    return int(w.split("-W")[1])


def load_pool():
    """Returns list of dict rows for the filtered pool with exported-field features attached."""
    dumps = {s: json.load(open(f"{DUMPS}/seed-{s}.json")) for s in SEEDS}
    rows = []
    for r in csv.DictReader(open(POOL)):
        if r["detector_state"] != "rule_only" or int(r["rule_weeks"]) < MIN_RULE_WEEKS:
            continue
        s = int(r["seed"])
        c = dumps[s]["candidates"][r["key"]]
        rw = sorted(_wk(w) for w in c["rule_weeks"])
        gaps = sum(1 for a, b in zip(rw, rw[1:]) if b - a > 1)
        run, best = 1, 1
        for a, b in zip(rw, rw[1:]):
            run = run + 1 if b - a == 1 else 1
            best = max(best, run)
        _, ent, chain = r["key"].split(":")
        rows.append(dict(
            seed=s, key=r["key"], cls=r["class"], entity=ent, chain=chain,
            gold=1 if r["class"] == "gold" else 0, decoy=1 if r["class"] == "decoy" else 0,
            # exported-contract fields (dumps)
            rw_count=len(rw), cw_count=len(c["candidate_weeks"]), ch_count=len(c["channels"]),
            rw_first=rw[0], rw_last=rw[-1], rw_span=rw[-1] - rw[0] + 1, rw_gaps=gaps, rw_longest=best,
            key_len=len(r["key"]),
            # label-side columns of candidate_pool.csv, carried ONLY for the N3 leakage demo
            csv_pattern=r["pattern"], csv_rare=r["rare"], csv_found=r["found"], csv_feasible=r["F_TB_v4_feasible"],
        ))
    # sibling count: how many filtered-pool keys share this key's entity in the same seed (exported: entity id)
    from collections import Counter
    sib = Counter((x["seed"], x["entity"]) for x in rows)
    for x in rows:
        x["siblings"] = sib[(x["seed"], x["entity"])]
    return rows, dumps


def hash_u01(*parts):
    """Deterministic pseudo-random U(0,1) from a string tuple (sha256)."""
    h = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def hash_bucket(s, nb, salt="ent"):
    return int.from_bytes(hashlib.sha256(f"{salt}:{s}".encode()).digest()[:4], "big") % nb


def rankdata(a):
    """Average ranks with ties, numpy only."""
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    s = a[order]
    _, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    cum = np.cumsum(counts); start = cum - counts
    avg = (start + cum - 1) / 2.0 + 1.0
    ranks = np.empty(len(a)); ranks[order] = avg[inv]
    return ranks


def auc(scores, y):
    """Mann-Whitney AUC with average ranks for ties.  y in {0,1}.  Returns nan if one class is empty."""
    y = np.asarray(y, int)
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    ranks = rankdata(scores)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def auc_matrix(S, y):
    """AUC for every column of S (n x m) against y. Ties broken by tiny deterministic jitter is NOT applied: exact average ranks via scipy-free rankdata."""
    S = np.asarray(S, float); y = np.asarray(y, int)
    n1 = int(y.sum()); n0 = len(y) - n1
    out = np.empty(S.shape[1])
    for j in range(S.shape[1]):
        out[j] = auc(S[:, j], y)
    return out


def rank_auc_fast(S, y):
    """Vectorised AUC for S (m x n) rows of scores with NO ties (random continuous scores)."""
    y = np.asarray(y, int)
    n1 = int(y.sum()); n0 = len(y) - n1
    ranks = np.argsort(np.argsort(S, axis=1), axis=1) + 1
    return (ranks[:, y == 1].sum(axis=1) - n1 * (n1 + 1) / 2) / (n1 * n0)


def prec_at_k(scores, y, k, tiebreak_seed=0):
    """Fraction of the top-k (by score, random tie-break) that are positive."""
    scores = np.asarray(scores, float); y = np.asarray(y, int)
    rng = np.random.default_rng(tiebreak_seed)
    jitter = rng.random(len(scores)) * 1e-9
    order = np.argsort(-(scores + jitter), kind="mergesort")
    k = min(k, len(scores))
    return y[order[:k]].sum() / k


# ---------- exact hypergeometric (no scipy in the venv) ----------
def _lchoose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hyper_pmf(N, K, n, x):
    if x < max(0, n + K - N) or x > min(K, n):
        return 0.0
    return math.exp(_lchoose(K, x) + _lchoose(N - K, n - x) - _lchoose(N, n))


def hyper_cdf(N, K, n, x):  # P(X <= x)
    return sum(hyper_pmf(N, K, n, i) for i in range(0, int(x) + 1))


def hyper_sf(N, K, n, x):  # P(X >= x)
    return 1.0 - hyper_cdf(N, K, n, x - 1) if x > 0 else 1.0


def hyper_crit(N, K, n, alpha=0.05):
    """Smallest x such that P(X >= x) <= alpha  (one-sided).  Returns (x, P(X>=x))."""
    for x in range(0, n + 1):
        p = hyper_sf(N, K, n, x)
        if p <= alpha:
            return x, p
    return n + 1, 0.0


def z_quantile(p):
    # Acklam rational approximation; adequate to 1e-9 for our use
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def auc_null_sd(n1, n0):
    """SD of AUC under H0 (Mann-Whitney, no ties): sqrt((n1+n0+1)/(12 n1 n0))."""
    return math.sqrt((n1 + n0 + 1) / (12.0 * n1 * n0))


def q(a, ps=(0, 1, 5, 25, 50, 75, 95, 99, 100)):
    a = np.asarray(a, float)
    return {p: float(np.percentile(a, p)) for p in ps}


def fmt_q(a, nd=3):
    d = q(a)
    return " | ".join(f"{d[p]:.{nd}f}" for p in (0, 5, 25, 50, 75, 95, 99, 100))


def hist_txt(a, lo, hi, nb=20, width=40):
    a = np.asarray(a, float)
    edges = np.linspace(lo, hi, nb + 1)
    cnt, _ = np.histogram(a, bins=edges)
    m = max(1, cnt.max())
    lines = []
    for i in range(nb):
        lines.append(f"  {edges[i]:.3f}-{edges[i+1]:.3f} {'#' * int(round(width * cnt[i] / m)):<{width}} {cnt[i]}")
    return "\n".join(lines)


def subsets(rows):
    """index arrays: GD = gold+decoy only; FULL = whole filtered pool."""
    gd = np.array([i for i, x in enumerate(rows) if x["cls"] in ("gold", "decoy")])
    full = np.arange(len(rows))
    return gd, full
