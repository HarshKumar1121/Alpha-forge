"""Alpha Gym: a synthetic stock market with known, planted effects.

Why synthetic? On real data you never learn whether your alpha was real or lucky. Here the
answer key exists, so you (and the tutor) can check. Every classic research lesson is planted:

* a short-term **reversal** in prices (works, but high turnover — costs matter),
* a slow **analyst revision** effect in a sparse, lagged field (needs backfill + patience),
* a **value** effect that only exists in small caps (fails the large-cap sub-universe test),
* a **sentiment** effect that worked for 7 years and then died (the out-of-sample trap),
* an **industry momentum** effect (only visible if you think in groups),
* twenty **pure-noise** "alternative data" fields (the data-mining trap: some WILL look good).

Fields are point-in-time: the value on day t was knowable at the close of day t. That is a
property of THIS market, not of BRAIN's data — see :mod:`..simulator`.

The answer key lives in ``ANSWER_KEY`` — try not to read it until you've done the exercises.

Ported from alpha-copilot ``local/gym.py``.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import numpy.typing as npt

    type Floats = npt.NDArray[np.float64]

N_STOCKS, N_DAYS, N_SECTORS, IND_PER_SECTOR = 400, 2520, 8, 4
N_NOISE = 20

ANSWER_KEY = {
    "reversal": "Negative of the last ~5 days' return predicts tomorrow. e.g. -ts_sum(returns, 5)",
    "analyst_revision": "anl_revision (sparse, updated ~monthly) predicts weeks ahead. "
    "ts_backfill it; higher = better.",
    "value": "book_to_price predicts, but only for the smallest half of stocks by cap.",
    "sentiment": "news_sentiment predicted returns for the first 7 years, then nothing.",
    "industry_momentum": "Stocks whose INDUSTRY did well over ~21 days keep doing well: "
    "group_mean(ts_sum(returns, 21), industry).",
    "noise": "alt_1 … alt_20 are pure noise. Any Sharpe you find in them is luck.",
}

DESCRIPTIONS = {
    "returns": "daily close-to-close return",
    "close": "closing price",
    "volume": "shares traded today",
    "adv20": "average daily dollar volume, 20 days",
    "cap": "market capitalization",
    "anl_revision": "net analyst EPS revisions (up minus down), monthly survey",
    "book_to_price": "book value / price, updated quarterly",
    "news_sentiment": "news sentiment score, daily",
}


@dataclass(frozen=True, slots=True)
class FieldDoc:
    name: str
    kind: str  # MATRIX | GROUP
    description: str
    coverage: float


@dataclass(frozen=True, slots=True)
class Market:
    fields: dict[str, pd.DataFrame]
    groups: dict[str, pd.Series]

    @property
    def n_stocks(self) -> int:
        return len(self.fields["cap"].columns)

    def describe(self) -> list[FieldDoc]:
        rows = [
            FieldDoc(
                k,
                "MATRIX",
                DESCRIPTIONS.get(k, "alternative-data signal (vendor X)"),
                float(v.notna().to_numpy().mean()),
            )
            for k, v in self.fields.items()
        ]
        return rows + [FieldDoc(k, "GROUP", f"GROUP: {k} label", 1.0) for k in self.groups]


def _z(a: Floats) -> Floats:
    """Cross-sectional z-score per row (day)."""
    m = np.nanmean(a, axis=1, keepdims=True)
    s = np.nanstd(a, axis=1, keepdims=True)
    return (a - m) / np.where(s > 0, s, 1)


@lru_cache(maxsize=4)
def make_market(seed: int = 7) -> Market:
    """Build the market. Deterministic per seed, and cached: it takes a few seconds."""
    rng = np.random.default_rng(seed)
    t_days, n = N_DAYS, N_STOCKS
    dates = pd.bdate_range("2015-01-02", periods=t_days)
    tickers = [f"S{i:03d}" for i in range(n)]
    sector = rng.integers(0, N_SECTORS, n)
    industry = sector * IND_PER_SECTOR + rng.integers(0, IND_PER_SECTOR, n)

    # Size: log-normal; small caps are noisier.
    log_cap0 = rng.normal(22.5, 1.2, n)
    small = log_cap0 < np.median(log_cap0)
    idio_vol = np.where(small, 0.022, 0.014)

    # Common factors: market, sector, industry. Industry returns are persistent (momentum).
    mkt = rng.normal(0.0003, 0.010, t_days)
    sec_f = rng.normal(0, 0.006, (t_days, N_SECTORS))
    ind_shock = rng.normal(0, 0.005, (t_days, N_SECTORS * IND_PER_SECTOR))
    ind_f = np.zeros_like(ind_shock)
    for t in range(1, t_days):
        trailing = ind_f[max(0, t - 21) : t].sum(axis=0)
        ind_f[t] = ind_shock[t] + 0.007 * trailing  # small autocorrelation = momentum

    beta = rng.normal(1.0, 0.25, n)
    eps = rng.normal(0, 1, (t_days, n)) * idio_vol

    # Latent drivers ---------------------------------------------------------------------------
    # Analyst revisions: slow AR(1) latent "quality news"; observed monthly, noisily.
    q = np.zeros((t_days, n))
    for t in range(1, t_days):
        q[t] = 0.985 * q[t - 1] + rng.normal(0, 0.17, n)
    # Value: static-ish book_to_price, drifting slowly.
    btp = np.cumsum(rng.normal(0, 0.02, (t_days, n)), axis=0) + rng.normal(0, 1, n)
    # Sentiment: daily, noisy; predictive only before 2022.
    sent_latent = rng.normal(0, 1, (t_days, n))
    alive = np.asarray(dates < pd.Timestamp("2022-01-01"), dtype=float)

    ret = np.zeros((t_days, n))
    for t in range(1, t_days):
        planted = (
            (
                -0.013 * _z(ret[max(0, t - 5) : t].sum(axis=0, keepdims=True))[0]  # reversal
                + 0.0105 * _z(q[t - 1 : t])[0]  # revisions
                + 0.0085 * _z(btp[t - 1 : t])[0] * small  # value, small caps only
                + 0.0135 * _z(sent_latent[t - 1 : t])[0] * alive[t]  # sentiment
            )
            * idio_vol
        )
        ret[t] = beta * mkt[t] + sec_f[t, sector] + ind_f[t, industry] + eps[t] + planted

    close = 50 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    shares = np.exp(log_cap0) / 50
    cap = close * shares
    volume = shares * np.exp(rng.normal(-4.6, 0.5, (t_days, n))) * (1 + 5 * np.abs(ret))
    adv20 = pd.DataFrame(volume * close).rolling(20, min_periods=1).mean().to_numpy()

    # Observed fields -----------------------------------------------------------------------------
    survey_day = np.zeros(t_days, bool)
    survey_day[::21] = True
    rev_obs = np.where(survey_day[:, None], q + rng.normal(0, 0.4, (t_days, n)), np.nan)
    rev_obs[:, rng.random(n) < 0.25] = np.nan  # 25% of stocks never covered by analysts
    quarter_day = np.arange(t_days)[:, None] % 63 == 0
    btp_obs = np.where(quarter_day, btp + rng.normal(0, 0.2, (t_days, n)), np.nan)
    btp_obs = pd.DataFrame(btp_obs).ffill().to_numpy()
    sent_obs = sent_latent + rng.normal(0, 0.8, (t_days, n))

    def df(a: Floats) -> pd.DataFrame:
        return pd.DataFrame(a, index=dates, columns=tickers)

    fields = {
        "returns": df(ret),
        "close": df(close),
        "volume": df(volume),
        "adv20": df(adv20),
        "cap": df(cap),
        "anl_revision": df(rev_obs),
        "book_to_price": df(btp_obs),
        "news_sentiment": df(sent_obs),
    }
    for i in range(1, N_NOISE + 1):
        fields[f"alt_{i}"] = df(
            np.cumsum(rng.normal(0, 1, (t_days, n)), axis=0) * 0.05 + rng.normal(0, 1, (t_days, n))
        )
    groups = {
        "sector": pd.Series(sector, index=tickers),
        "industry": pd.Series(industry, index=tickers),
        # No finer level exists here, so BRAIN-style expressions naming subindustry still run.
        "subindustry": pd.Series(industry, index=tickers),
        "market": pd.Series(0, index=tickers),
    }
    return Market(fields, groups)
