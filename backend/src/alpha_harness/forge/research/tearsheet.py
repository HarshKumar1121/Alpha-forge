"""Tear-sheet analytics over one alpha's real pnl/turnover/yearly-stats recordsets.

Why each one exists, beyond ``forge.analytics``'s pool-level primitives:

* ``underwater``          — how painful this alpha's own ride was, not just its end Sharpe.
* ``rolling_sharpe/vol``  — a single full-sample Sharpe hides a signal that decayed or got
                            noisier over time; a trailing window surfaces that.
* ``bootstrap_sharpe_ci`` — the Sharpe you measured is one draw from a random process; a block
                            bootstrap (resampling whole runs of days, not single days, so the
                            autocorrelation a real PnL series has survives the resample) asks
                            how much of it would survive another draw.
* ``permutation_test``    — randomly flips the sign of each day and asks how often that beats
                            the real Sharpe. A low p-value means the *direction* of these
                            returns, not just their size, is unlikely to be noise.
* ``prob_sharpe_above``   — the question a desk actually asks ("what's the chance this is
                            really above 1.25"), via the same skew/kurtosis-adjusted normal
                            approximation ``analytics.deflated_sharpe`` uses against a trial
                            count, but against a fixed bar instead.
"""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from ...brain.platform import BOOK_SIZE
from ..analytics import DAYS, moment

_N = NormalDist()


def cumulative(daily: pd.Series) -> pd.Series:
    """Running total PnL — the curve a tear sheet's headline chart draws."""
    return daily.cumsum()


def underwater(daily: pd.Series) -> pd.Series:
    """Distance below the running peak, as a fraction of half the book — the same drawdown
    convention ``vault.metrics.stats`` uses, as a full series instead of just its worst point."""
    cum = cumulative(daily)
    peak = cum.cummax()
    return (cum - peak) / (BOOK_SIZE / 2)


def rolling_sharpe(daily: pd.Series, window: int = DAYS) -> pd.Series:
    """Sharpe over a trailing window, annualised the same way as ``analytics.sharpe``."""
    mean = daily.rolling(window).mean()
    std = daily.rolling(window).std(ddof=1)
    return mean / std * math.sqrt(DAYS)


def rolling_vol(daily: pd.Series, window: int = DAYS) -> pd.Series:
    """Annualised volatility over a trailing window."""
    return daily.rolling(window).std(ddof=1) * math.sqrt(DAYS)


def moving_average_bands(daily: pd.Series, window: int = 20) -> pd.DataFrame:
    """A moving average of daily PnL with +/-2 sigma bands, so a day reads against its own
    recent run rather than the whole history."""
    ma = daily.rolling(window).mean()
    sd = daily.rolling(window).std(ddof=1)
    return pd.DataFrame({"ma": ma, "upper": ma + 2 * sd, "lower": ma - 2 * sd})


def monthly_returns(daily: pd.Series) -> dict[tuple[int, int], float]:
    """PnL summed by calendar month — the grid a returns heatmap draws."""
    index = pd.DatetimeIndex(daily.index)
    grouped = daily.groupby([index.year, index.month]).sum()
    return {(int(y), int(m)): float(v) for (y, m), v in grouped.items()}  # pyright: ignore[reportGeneralTypeIssues]


def return_distribution(daily: pd.Series, worst_n: int = 10) -> dict[str, object]:
    """Shape of the daily PnL distribution: is it the normal bell a Sharpe assumes, or does it
    hide fat tails and a handful of very bad days?"""
    x = daily.to_numpy(dtype=float)
    n = x.size
    if n < 2:
        return {
            "mean": math.nan,
            "std": math.nan,
            "skew": math.nan,
            "kurtosis": math.nan,
            "qq": [],
            "worst_days": [],
        }
    sorted_x = np.sort(x)
    probs = (np.arange(1, n + 1) - 0.5) / n
    theoretical = np.array([_N.inv_cdf(p) for p in probs]) * x.std(ddof=1) + x.mean()
    worst = daily.sort_values().head(worst_n)
    worst_days = list(zip(pd.DatetimeIndex(worst.index), worst.to_numpy(), strict=True))
    return {
        "mean": float(x.mean()),
        "std": float(x.std(ddof=1)),
        "skew": moment(pd.Series(x).skew()),
        "kurtosis": moment(pd.Series(x).kurt()) + 3.0,  # pandas gives excess kurtosis
        "qq": list(zip(theoretical.tolist(), sorted_x.tolist(), strict=True)),
        "worst_days": [(ts.date(), float(v)) for ts, v in worst_days],
    }


def bootstrap_sharpe_ci(
    daily: pd.Series,
    *,
    block_days: int,
    samples: int,
    rng: np.random.Generator | None = None,
) -> tuple[float, float]:
    """A 90% confidence interval for the Sharpe, resampling whole blocks of ``block_days`` so
    the bootstrap doesn't shuffle away a real PnL series's own autocorrelation."""
    x = daily.to_numpy(dtype=float)
    n = x.size
    if n < block_days * 2:
        return math.nan, math.nan
    rng = rng or np.random.default_rng()
    n_blocks = math.ceil(n / block_days)
    sharpes = np.empty(samples)
    for i in range(samples):
        starts = rng.integers(0, n - block_days + 1, size=n_blocks)
        resampled = np.concatenate([x[s : s + block_days] for s in starts])[:n]
        spread = resampled.std(ddof=1)
        sharpes[i] = resampled.mean() / spread * math.sqrt(DAYS) if spread > 0 else math.nan
    valid = sharpes[~np.isnan(sharpes)]
    if valid.size == 0:
        return math.nan, math.nan
    return float(np.percentile(valid, 5)), float(np.percentile(valid, 95))


def permutation_test(
    daily: pd.Series, *, samples: int, rng: np.random.Generator | None = None
) -> float:
    """Sign-flip permutation test: the share of random sign-flips of this same PnL whose Sharpe
    matches or beats the real one."""
    x = daily.to_numpy(dtype=float)
    n = x.size
    if n < 2:
        return math.nan
    spread = x.std(ddof=1)
    if spread <= 0:
        return math.nan
    observed = abs(x.mean() / spread * math.sqrt(DAYS))
    rng = rng or np.random.default_rng()
    signs = rng.choice(np.array([-1.0, 1.0]), size=(samples, n))
    permuted = signs * x
    means = permuted.mean(axis=1)
    stds = permuted.std(axis=1, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sharpes = np.abs(means / stds * math.sqrt(DAYS))
    sharpes = np.nan_to_num(sharpes, nan=0.0)
    return float((sharpes >= observed).mean())


def prob_sharpe_above(daily: pd.Series, threshold: float = 1.25) -> float:
    """P(true annualised Sharpe > ``threshold``), via the same skew/kurtosis-adjusted normal
    approximation ``analytics.deflated_sharpe`` uses against a trial count — but against a fixed
    bar instead."""
    x = daily.to_numpy(dtype=float)
    t = x.size
    if t < 30:
        return math.nan
    spread = x.std(ddof=1)
    if spread <= 0:
        return math.nan
    sr = float(x.mean() / spread)
    skew = moment(pd.Series(x).skew())
    kurt = moment(pd.Series(x).kurt()) + 3.0
    sr0 = threshold / math.sqrt(DAYS)
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / denom)


#: Trading-day lags the IC proxy below is checked at.
IC_HORIZONS: tuple[int, ...] = (1, 2, 5, 10, 21)

#: Minimum non-overlapping observations a lag needs before it's trusted.
_MIN_IC_OBS = 60


def return_based_ic(daily: pd.Series, horizons: tuple[int, ...] = IC_HORIZONS) -> dict[int, float]:
    """A return-based stand-in for Information Coefficient, decayed across lags.

    A true IC is the cross-sectional rank correlation between a signal and the forward return it
    is betting on, per stock per day. BRAIN never hands out per-stock values here (CLAUDE.md's
    ``Simulator`` only returns portfolio PnL/turnover/yearly-stats), so there is nothing
    cross-sectional to rank-correlate. What this computes instead is the Pearson correlation
    between the alpha's own daily PnL and its PnL ``h`` trading days later, for each horizon —
    whether a day of edge tends to be followed by another one, or fades out. Read it as "does
    today's edge persist", not as the textbook IC a per-instrument backtest would report.
    """
    x = daily.dropna().to_numpy(dtype=float)
    out: dict[int, float] = {}
    for h in horizons:
        if x.size <= h + _MIN_IC_OBS:
            out[h] = math.nan
            continue
        a, b = x[:-h], x[h:]
        if a.std(ddof=1) <= 0 or b.std(ddof=1) <= 0:
            out[h] = math.nan
            continue
        out[h] = float(np.corrcoef(a, b)[0, 1])
    return out


def sharpe_trend(rolling: pd.Series) -> str:
    """``"rising"``, ``"falling"`` or ``"flat"``: the second half of the rolling-Sharpe history
    against its first half, so a one-line verdict can say whether the edge looks like it is
    improving or fading rather than just quoting its current level."""
    valid = rolling.dropna()
    if len(valid) < 20:
        return "flat"
    half = len(valid) // 2
    delta = float(valid.iloc[half:].mean() - valid.iloc[:half].mean())
    if delta > 0.2:
        return "rising"
    if delta < -0.2:
        return "falling"
    return "flat"


_TREND_PHRASE = {
    "rising": "Sharpe has been improving over time",
    "falling": "Sharpe has been fading over time",
    "flat": "Sharpe has been stable over time",
}


def plain_verdict(
    *,
    trend: str,
    top_correlation: tuple[str, float] | None,
    dominant_factor: tuple[str, float] | None,
    residual_sharpe: float | None,
) -> str:
    """One sentence combining the Sharpe trend, pool crowding and factor exposure — the three
    things that together say whether an alpha is a genuine, uncrowded, idiosyncratic edge."""
    trend_phrase = _TREND_PHRASE.get(trend, _TREND_PHRASE["flat"])

    if top_correlation is not None and not math.isnan(top_correlation[1]):
        corr_id, corr_value = top_correlation
        corr_phrase = f"it correlates {corr_value:.2f} with the closest pool alpha ({corr_id})"
    else:
        corr_phrase = "it has no comparable alpha yet in the pool to check crowding against"

    if (
        dominant_factor is not None
        and not math.isnan(dominant_factor[1])
        and abs(dominant_factor[1]) >= 0.05
    ):
        factor_id, share = dominant_factor
        factor_phrase = f"{abs(share) * 100:.0f}% of it traces back to {factor_id}"
    elif residual_sharpe is not None and not math.isnan(residual_sharpe):
        factor_phrase = "it runs mostly independent of the factors tracked here"
    else:
        factor_phrase = "its factor exposure hasn't been checked yet"

    return f"{trend_phrase}, {corr_phrase}, and {factor_phrase}."
