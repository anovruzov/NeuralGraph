"""H2 shared numpy code: L2 logistic regression (IRLS), AUC with ties, expected precision@N under random tie-breaking,
Elkan-Noto prior estimation, binned mixture-proportion estimate. No sklearn."""
import numpy as np

def fit_logistic(X, y, lam=1.0, iters=50, w=None):
    """Weighted L2 logistic; X standardized by caller, intercept added here. Returns beta (d+1)."""
    n, d = X.shape
    A = np.hstack([np.ones((n, 1)), X]); b = np.zeros(d + 1)
    if w is None: w = np.ones(n)
    reg = np.full(d + 1, lam); reg[0] = 0.0
    for _ in range(iters):
        z = A @ b; p = 1 / (1 + np.exp(-z)); W = w * p * (1 - p)
        g = A.T @ (w * (p - y)) + reg * b
        H = (A * W[:, None]).T @ A + np.diag(reg)
        step = np.linalg.solve(H, g); b -= step
        if np.max(np.abs(step)) < 1e-8: break
    return b

def predict(beta, X):
    return 1 / (1 + np.exp(-(beta[0] + X @ beta[1:])))

class Scaler:
    def fit(self, X):
        self.m = X.mean(0); self.s = X.std(0); self.s[self.s == 0] = 1.0; return self
    def t(self, X): return (X - self.m) / self.s

def auc(score, label):
    """Mann-Whitney AUC with average ranks for ties; label in {0,1}; nan if a class is empty."""
    score = np.asarray(score, float); label = np.asarray(label, bool)
    n1 = label.sum(); n0 = (~label).sum()
    if n1 == 0 or n0 == 0: return float("nan")
    order = np.argsort(score, kind="mergesort"); ranks = np.empty(len(score))
    s = score[order]; i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]: j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return float((ranks[label].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

def prec_at(score, label, N):
    """Expected number of label==1 among the top N under uniformly random tie-breaking (returns the COUNT, not the rate)."""
    score = np.asarray(score, float); label = np.asarray(label, float)
    if len(score) == 0: return 0.0
    N = min(N, len(score)); order = np.argsort(-score, kind="mergesort"); s = score[order]; l = label[order]
    cut = s[N - 1]
    above = s > cut; tie = s == cut
    n_above = above.sum(); need = N - n_above
    return float(l[above].sum() + need * (l[tie].sum() / tie.sum()))

def cv_scores(X, y, lam=1.0, k=5, seed=0, w=None):
    """Out-of-fold g(x) for every row (stratified-ish random folds)."""
    rng = np.random.default_rng(seed); n = len(y); folds = np.zeros(n, int)
    for c in (0, 1):
        idx = np.where(y == c)[0]; rng.shuffle(idx); folds[idx] = np.arange(len(idx)) % k
    out = np.zeros(n)
    for f in range(k):
        tr = folds != f; sc = Scaler().fit(X[tr])
        beta = fit_logistic(sc.t(X[tr]), y[tr], lam=lam, w=None if w is None else w[tr])
        out[~tr] = predict(beta, sc.t(X[~tr]))
    return out

def elkan_noto(g, s):
    """g: P(s=1|x) estimates; s: labeled-positive indicator. Returns c, prior-in-U (global), prior-in-U (per-example sum)."""
    g = np.asarray(g, float); s = np.asarray(s, bool)
    c = float(g[s].mean())
    nP = s.sum(); nU = (~s).sum()
    prior_global = nP * (1 - c) / (c * nU)
    gu = np.clip(g[~s], 1e-9, 1 - 1e-9)
    wts = (1 - c) / c * gu / (1 - gu)
    prior_sum = float(np.clip(wts, 0, 1).sum() / nU)
    return c, float(prior_global), prior_sum, np.clip(wts, 0, 1)

def mpe_binned(xP, xU, bins, min_p=5):
    """Mixture-proportion estimate: U = pi*P + (1-pi)*N  =>  pi <= p_U(b)/p_P(b) for every bin b; take the min over bins
    holding >= min_p known positives. Returns pi_hat and the per-bin table."""
    hP = np.histogram(xP, bins)[0].astype(float); hU = np.histogram(xU, bins)[0].astype(float)
    pP = hP / hP.sum(); pU = hU / hU.sum()
    ok = hP >= min_p
    ratio = np.where(ok, pU / np.where(pP > 0, pP, np.nan), np.nan)
    return float(np.nanmin(ratio)), list(zip(bins[:-1], hP, hU, ratio))
