/**
 * The real tear sheet: BRAIN's own recorded history for one alpha, read back as a desk reads
 * it — the curve, how it behaves day to day, and how much of the Sharpe is luck. Everything
 * here is built from the alpha's actual `pnl`/`turnover`/`yearly-stats` recordsets, over its
 * full recorded history; there is no train/test split here (CLAUDE.md rule 6 is about
 * selection, which a read-only tear sheet does not do).
 */

import { useMemo } from 'react'
import { DASH, fmt, isNum } from '@/lib/format'
import { Tip } from '@/screens/alpha/panels'
import { color } from '@/screens/pool/pnl-chart'
import { ChartLegend, PortfolioChart } from '@/screens/portfolio/chart'
import { Metric, Panel } from '@/ui/kit'
import { ChartFigure, chartBase, EChart } from '../charts/echart'
import type { TearSheetOut } from './api'

/** A figure's label with its explanation behind a dot. */
function Label({ text, tip }: { text: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {text}
      <Tip title={text}>{tip}</Tip>
    </span>
  )
}

const EXPLAIN = {
  sharpe:
    'Risk-adjusted return over the alpha’s whole recorded history: average daily PnL divided ' +
    'by its day-to-day spread, scaled to a year. Not split into train and test — this is simply ' +
    'what actually happened.',
  bootstrapCi:
    'A 90% range for the true Sharpe, found by resampling blocks of real days thousands of ' +
    'times. A wide range means the point estimate above could easily have come out very ' +
    'different by chance.',
  permutation:
    'The chance a random sign-flip of these same returns would produce a Sharpe this high. ' +
    'Low means the direction of the PnL is unlikely to be noise.',
  deflated:
    'Sharpe adjusted downward for how many simulations this template has already tried: trying ' +
    'enough variations eventually finds one that looks good by luck alone. Shown as a single, ' +
    'unadjusted trial when the template’s trial count is not yet known.',
  probAbove:
    'The probability, given the spread in the data, that the true Sharpe is actually above ' +
    '1.25 — BRAIN’s bar for a Regular Alpha.',
  rollingSharpe: 'Sharpe over a trailing window, so a researcher can see when the edge decayed.',
  rollingVol: 'Daily PnL volatility over the same trailing window, the denominator of Sharpe.',
  bands:
    'Daily PnL against its own 20-day moving average, with a ±2 standard-deviation band. Days ' +
    'that break out of the band are the ones that moved the Sharpe the most.',
  monthly: 'Summed PnL by calendar month — where the good and bad stretches actually fall.',
  qq:
    'Each point compares one day’s return, ranked, against what a normal distribution would ' +
    'predict at that rank. Points that hug the dashed line are close to normal; a tail that ' +
    'bows away from it is a fatter tail than a Sharpe ratio alone would suggest.',
  skew: 'Lopsidedness of the daily returns: negative means rare large losses outweigh rare large gains.',
  kurtosis:
    'How fat the tails are versus a normal distribution (0 is normal); higher means more extreme days.',
  poolCorrelation:
    'How much this alpha’s PnL moves with alphas already submitted. A high correlation to ' +
    'something already in the pool means less of this one’s Sharpe is new.',
  factorExposure:
    'How much of this alpha’s PnL a handful of known, well-worn trades (momentum, size, ' +
    'and the like) already explain. What is left over, the residual Sharpe, is the part that ' +
    'is actually this alpha’s own.',
  icDecay:
    'A stand-in for Information Coefficient: since BRAIN only hands back this alpha’s own ' +
    'portfolio PnL, not its per-stock bets, this correlates a day’s PnL with its own PnL ' +
    'several days later. A bar near zero at longer horizons means the edge doesn’t carry ' +
    'forward — this is not the textbook cross-sectional IC a per-instrument backtest reports.',
} as const

const CHART_CAPTION = {
  pnl:
    'whether the edge is steady or came from one lucky stretch, and how deep the bad periods ' +
    'were, over the alpha’s whole recorded history.',
  rolling: 'whether the signal has decayed recently rather than always having looked like this.',
  bands: 'which individual days drove the PnL away from its own recent average.',
  monthly: 'which months carried the alpha and which dragged on it.',
  qq: 'whether the daily returns look normal or have fatter tails than a Sharpe ratio alone suggests.',
  yearly: 'whether the alpha works in most years or depends on one or two of them.',
  icDecay: 'whether a day of edge tends to be followed by more of the same, or fades out fast.',
} as const

function dayLabel(iso: string) {
  return iso.slice(5)
}

export function TearSheet({ sheet }: { sheet: TearSheetOut }) {
  const [ciLow, ciHigh] = sheet.bootstrapSharpeCi

  const rollingOption = useMemo(() => {
    const base = chartBase()
    return {
      ...base,
      legend: { top: 0, textStyle: { color: base.textStyle.color } },
      grid: { ...base.grid, top: 32 },
      xAxis: {
        type: 'category',
        data: sheet.days.map(dayLabel),
        axisLabel: { ...base.axis.axisLabel, interval: Math.floor(sheet.days.length / 8) },
      },
      yAxis: [
        { type: 'value', name: 'Sharpe', ...base.axis },
        { type: 'value', name: 'Vol', ...base.axis, splitLine: { show: false } },
      ],
      series: [
        {
          name: 'Rolling Sharpe',
          type: 'line',
          data: sheet.rollingSharpe,
          showSymbol: false,
          yAxisIndex: 0,
        },
        {
          name: 'Rolling vol',
          type: 'line',
          data: sheet.rollingVol,
          showSymbol: false,
          yAxisIndex: 1,
        },
      ],
    }
  }, [sheet.days, sheet.rollingSharpe, sheet.rollingVol])

  const bandsOption = useMemo(() => {
    const base = chartBase()
    return {
      ...base,
      legend: { top: 0, textStyle: { color: base.textStyle.color } },
      grid: { ...base.grid, top: 32 },
      xAxis: {
        type: 'category',
        data: sheet.days.map(dayLabel),
        axisLabel: { ...base.axis.axisLabel, interval: Math.floor(sheet.days.length / 8) },
      },
      yAxis: {
        type: 'value',
        ...base.axis,
        axisLabel: { ...base.axis.axisLabel, formatter: (v: number) => fmt.compact(v) },
      },
      series: [
        {
          name: 'Daily PnL',
          type: 'line',
          data: sheet.pnl,
          showSymbol: false,
          lineStyle: { width: 1 },
        },
        { name: '20d average', type: 'line', data: sheet.ma, showSymbol: false },
        {
          name: 'Upper band',
          type: 'line',
          data: sheet.maUpper,
          showSymbol: false,
          lineStyle: { type: 'dashed', opacity: 0.5 },
        },
        {
          name: 'Lower band',
          type: 'line',
          data: sheet.maLower,
          showSymbol: false,
          lineStyle: { type: 'dashed', opacity: 0.5 },
        },
      ],
    }
  }, [sheet.days, sheet.pnl, sheet.ma, sheet.maUpper, sheet.maLower])

  const monthlyOption = useMemo(() => {
    const base = chartBase()
    const months = Object.keys(sheet.monthlyReturns).sort()
    return {
      ...base,
      xAxis: {
        type: 'category',
        data: months,
        axisLabel: { ...base.axis.axisLabel, interval: Math.floor(months.length / 10), rotate: 45 },
      },
      yAxis: {
        type: 'value',
        ...base.axis,
        axisLabel: { ...base.axis.axisLabel, formatter: (v: number) => fmt.compact(v) },
      },
      series: [
        {
          name: 'Monthly PnL',
          type: 'bar',
          data: months.map((m) => {
            const v = sheet.monthlyReturns[m] ?? 0
            return {
              value: v,
              itemStyle: { color: color(v >= 0 ? 'color-pnl-positive' : 'color-pnl-negative') },
            }
          }),
        },
      ],
    }
  }, [sheet.monthlyReturns])

  const qq = sheet.distribution.qq
  const qqOption = useMemo(() => {
    const base = chartBase()
    const xs = qq.map((p) => p[0] ?? 0)
    const ys = qq.map((p) => p[1] ?? 0)
    const lo = Math.min(...xs, ...ys)
    const hi = Math.max(...xs, ...ys)
    return {
      ...base,
      xAxis: { type: 'value', name: 'Theoretical quantile', ...base.axis },
      yAxis: { type: 'value', name: 'Sample quantile', ...base.axis },
      series: [
        { name: 'Days', type: 'scatter', symbolSize: 5, data: qq },
        {
          name: 'Normal',
          type: 'line',
          data:
            Number.isFinite(lo) && Number.isFinite(hi)
              ? [
                  [lo, lo],
                  [hi, hi],
                ]
              : [],
          showSymbol: false,
          lineStyle: { type: 'dashed', opacity: 0.6 },
        },
      ],
    }
  }, [qq])

  const icOption = useMemo(() => {
    const base = chartBase()
    const labels = sheet.ic.horizons.map((h) => `${h}d`)
    return {
      ...base,
      xAxis: { type: 'category', data: labels, ...base.axis },
      yAxis: { type: 'value', name: 'IC (proxy)', ...base.axis },
      series: [
        {
          name: 'IC',
          type: 'bar',
          data: sheet.ic.values.map((v) => ({
            value: v,
            itemStyle: {
              color: color((v ?? 0) >= 0 ? 'color-pnl-positive' : 'color-pnl-negative'),
            },
          })),
        },
      ],
    }
  }, [sheet.ic])

  const yearlyOption = useMemo(() => {
    const base = chartBase()
    const rows = sheet.yearly
    const years = rows.map((r) => String(r['year'] ?? ''))
    const sharpes = rows.map((r) => (isNum(r['sharpe']) ? (r['sharpe'] as number) : 0))
    return {
      ...base,
      xAxis: { type: 'category', data: years, ...base.axis },
      yAxis: { type: 'value', name: 'Sharpe', ...base.axis },
      series: [
        {
          name: 'Sharpe',
          type: 'bar',
          data: sharpes.map((v) => ({
            value: v,
            itemStyle: { color: color(v >= 0 ? 'color-pnl-positive' : 'color-pnl-negative') },
          })),
        },
      ],
    }
  }, [sheet.yearly])

  return (
    <>
      <Panel title="Verdict">
        <p className="text-body text-ink">{sheet.verdict}</p>
      </Panel>

      <Panel
        title="Headline"
        description="Figures over the alpha’s whole recorded history — read-only, nothing here is a selection metric."
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Metric
            boxed
            label={<Label text="Sharpe" tip={EXPLAIN.sharpe} />}
            value={fmt.ratio(sheet.sharpe)}
          />
          <Metric
            boxed
            label={<Label text="90% Sharpe range" tip={EXPLAIN.bootstrapCi} />}
            value={`${fmt.ratio(ciLow)} – ${fmt.ratio(ciHigh)}`}
          />
          <Metric
            boxed
            label={<Label text="Permutation p" tip={EXPLAIN.permutation} />}
            value={fmt.ratio(sheet.permutationPValue, 3)}
          />
          <Metric
            boxed
            label={<Label text="Deflated Sharpe" tip={EXPLAIN.deflated} />}
            value={fmt.ratio(sheet.deflatedSharpe.value)}
            hint={
              sheet.deflatedSharpe.deflated
                ? `Over ${fmt.int(sheet.deflatedSharpe.nTrials)} tries on this template`
                : 'Trial count unknown — shown as one try'
            }
          />
          <Metric
            boxed
            label={<Label text="P(Sharpe > 1.25)" tip={EXPLAIN.probAbove} />}
            value={fmt.pct(sheet.probSharpeAbove125, 0)}
          />
        </div>
      </Panel>

      <Panel title="Cumulative PnL" description={CHART_CAPTION.pnl}>
        <div className="flex flex-col gap-3">
          <PortfolioChart
            dates={sheet.days}
            curve={sheet.cumulative}
            testStart={null}
            label={`Cumulative PnL of ${sheet.alphaId}`}
          />
          <ChartLegend testStart={null} />
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Rolling Sharpe and volatility">
          <ChartFigure caption={CHART_CAPTION.rolling}>
            <EChart
              option={rollingOption}
              label="Rolling Sharpe and rolling volatility over time"
            />
          </ChartFigure>
        </Panel>

        <Panel title="Daily PnL vs. its moving average">
          <ChartFigure caption={CHART_CAPTION.bands}>
            <EChart option={bandsOption} label="Daily PnL against its 20-day moving average band" />
          </ChartFigure>
        </Panel>

        <Panel title="Monthly returns">
          <ChartFigure caption={CHART_CAPTION.monthly}>
            <EChart option={monthlyOption} label="Summed PnL by calendar month" />
          </ChartFigure>
        </Panel>

        <Panel title="Sharpe by year">
          <ChartFigure caption={CHART_CAPTION.yearly}>
            <EChart option={yearlyOption} label="Sharpe ratio for each calendar year" />
          </ChartFigure>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel title={<Label text="Correlation to your pool" tip={EXPLAIN.poolCorrelation} />}>
          <PoolCorrelation rows={sheet.poolCorrelation} />
        </Panel>

        <Panel title={<Label text="Factor exposure" tip={EXPLAIN.factorExposure} />}>
          <p className="text-body-compact text-ink-subtle">
            {sheet.factorSummary ??
              'Not available yet — this alpha isn’t on the Submittable Board, so its ' +
                'settings aren’t known.'}
          </p>
        </Panel>

        <Panel title="Information coefficient" description={CHART_CAPTION.icDecay}>
          <ChartFigure caption={EXPLAIN.icDecay}>
            <EChart option={icOption} label="Return-based information coefficient by horizon" />
          </ChartFigure>
        </Panel>
      </div>

      <Panel title="Return distribution" bodyClassName="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3 sm:w-80">
          <Metric
            size="sm"
            label={<Label text="Skew" tip={EXPLAIN.skew} />}
            value={fmt.ratio(sheet.distribution.skew)}
          />
          <Metric
            size="sm"
            label={<Label text="Kurtosis" tip={EXPLAIN.kurtosis} />}
            value={fmt.ratio(sheet.distribution.kurtosis)}
          />
        </div>
        <ChartFigure caption={CHART_CAPTION.qq}>
          <EChart
            option={qqOption}
            label="QQ plot of daily returns against a normal distribution"
          />
        </ChartFigure>
        <WorstDays rows={sheet.distribution.worstDays} />
      </Panel>
    </>
  )
}

function PoolCorrelation({ rows }: { rows: { alphaId: string; correlation: number }[] }) {
  if (rows.length === 0) {
    return (
      <p className="text-body-compact text-ink-subtle">
        No submitted alphas yet to compare against.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-1">
      {rows.map((r) => (
        <li key={r.alphaId} className="flex justify-between text-body-compact text-ink-subtle">
          <span>{r.alphaId}</span>
          <span className="num">{fmt.ratio(r.correlation, 2)}</span>
        </li>
      ))}
    </ul>
  )
}

function WorstDays({ rows }: { rows: [string, number][] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <div className="mb-1 flex items-center gap-1 text-body-compact text-ink">
        Worst days
        <Tip title="Worst days">
          The days with the largest single-day losses in the recorded history.
        </Tip>
      </div>
      <ul className="flex flex-col gap-0.5">
        {rows.map(([day, value]) => (
          <li key={day} className="flex justify-between text-body-compact text-ink-subtle">
            <span className="num">{fmt.date(day)}</span>
            <span className="num">{isNum(value) ? fmt.compact(value) : DASH}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
