/**
 * What each figure on the tear sheet means, in plain language. One place, so the words stay
 * consistent and a learner reads the same explanation wherever the number appears.
 */

export const EXPLAIN = {
  trainSharpe:
    'Risk-adjusted return over the years you research on: average daily PnL divided by its ' +
    'day-to-day spread, scaled to a year (250 days, as BRAIN counts). Above about 1.25 is ' +
    "BRAIN's bar for a Regular Alpha. This is the number to choose by.",
  testSharpe:
    'The same Sharpe on the last years, held out from your research. Look at it once, at the ' +
    'end: if you tweak the expression until this looks good, it stops being a test and becomes ' +
    'one more thing you fitted.',
  fitness:
    "Sharpe × √(|returns| ÷ turnover). BRAIN's single score: it rewards a strong signal that " +
    'earns a lot per unit of trading. Turnover below 12.5% counts as 12.5%.',
  turnover:
    'The share of the book traded each day. High turnover means high trading costs on a real ' +
    'desk; BRAIN fails anything above 70% and below 1%.',
  returns:
    'Annual PnL as a fraction of half the $20M book (the long or the short side). Small numbers ' +
    'are normal: market-neutral books earn single-digit percents.',
  drawdown:
    'The deepest fall from a previous peak of cumulative PnL, as a fraction of half the book. ' +
    'The worst stretch you would have lived through.',
  margin:
    'PnL per dollar traded, in basis points. Below a few bps, real trading costs eat the edge.',
  subUniverse:
    'Sharpe on the larger half of stocks only. A real, tradeable effect should survive in liquid ' +
    'names — expect it to drop by about √½ just from having half as many bets.',
  icMean:
    "Information coefficient: the rank correlation between today's signal and tomorrow's " +
    'returns, across stocks, averaged over days. A good equity signal has an IC of only ' +
    '0.01–0.05; Sharpe comes from making that small edge on many stocks every day.',
  icT:
    'How sure we are the IC is not zero: mean IC divided by its standard error. Above about 3 ' +
    'is hard to get by luck on one try — but not after hundreds of tries.',
  icHit:
    'The share of days the IC was positive. A steady signal is right a little more often than 50%.',
} as const

export const CHECK_EXPLAIN: Record<string, string> = {
  LOW_SHARPE: 'Sharpe must reach the limit for the alpha to be worth submitting.',
  LOW_FITNESS: "Fitness must reach the limit: BRAIN's combined score of strength and cost.",
  LOW_TURNOVER: 'The book must trade at least a little; a signal that never changes is stale.',
  HIGH_TURNOVER: 'The book must not churn: past this, trading costs swamp most edges.',
  CONCENTRATED_WEIGHT: 'No single stock may carry too much of the book on any day.',
  LOW_SUB_UNIVERSE_SHARPE:
    'The large-cap half must keep enough Sharpe, after the expected loss from fewer bets. ' +
    'Effects that live only in small, illiquid stocks fail here.',
}

/** One line under each chart: what a researcher reads from it. */
export const CHART_CAPTION = {
  pnl:
    'What this tells you: whether the edge is steady or came from one lucky stretch, and how ' +
    'deep the bad periods were. The test years are coloured apart — judge on the train years.',
  icDecay:
    'What this tells you: how long the signal stays predictive. A fast fall means a short-lived ' +
    'signal that must trade a lot; a slow fall means you can smooth it (decay) and trade less.',
  quintiles:
    'What this tells you: whether the whole ranking carries information. You want a staircase ' +
    'from Q1 (lowest signal) to Q5 (highest), not one lucky extreme bucket.',
  yearly:
    'What this tells you: whether the alpha works in most years. One great year carrying a ' +
    'ten-year Sharpe is fragile.',
  checks:
    "What this tells you: which of BRAIN's submission checks the alpha would fail, with the " +
    'value and the limit. The Gym approximates them; BRAIN’s own checks are final.',
} as const
