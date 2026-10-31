/** The Gym's tear sheet: BRAIN's headline figures, then the diagnostics a desk reads first. */

import { type ReactNode, useMemo } from 'react'
import { fmt } from '@/lib/format'
import { neutralizationLabel } from '@/lib/neutralization'
import { Tip } from '@/screens/alpha/panels'
import { ChartLegend, PortfolioChart } from '@/screens/portfolio/chart'
import {
  Badge,
  checkTone,
  cutoffTone,
  figureTone,
  type Limits,
  Metric,
  Panel,
  signTone,
} from '@/ui/kit'
import type { GymTearSheet } from './api'
import { type Bar, Bars, FILL } from './bars'
import { CHART_CAPTION, CHECK_EXPLAIN, EXPLAIN } from './explain'

/** A figure's label with its explanation behind a dot. */
function Label({ text, tip }: { text: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {text}
      <Tip title={text}>{tip}</Tip>
    </span>
  )
}

/** Checks that measure a share of the book read as percents; the rest are ratios. */
const PERCENT_CHECKS = new Set(['LOW_TURNOVER', 'HIGH_TURNOVER', 'CONCENTRATED_WEIGHT'])
const checkFigure = (name: string, v: number | null | undefined) =>
  PERCENT_CHECKS.has(name) ? fmt.pct(v, 2) : fmt.ratio(v)

const fillOf = (tone: ReturnType<typeof signTone>): keyof typeof FILL =>
  tone === 'profit' ? 'profit' : tone === 'loss' ? 'loss' : 'neutral'

export function TearSheet({ sheet }: { sheet: GymTearSheet }) {
  // The alpha's own check limits colour its figures, as on every other screen.
  const limits: Limits = Object.fromEntries(
    sheet.checks.filter((c) => c.limit != null).map((c) => [c.name, c.limit as number]),
  )
  const train = sheet.train
  const dates = useMemo(() => sheet.pnl.map((p) => p.day), [sheet.pnl])
  const curve = useMemo(() => sheet.pnl.map((p) => p.pnl), [sheet.pnl])
  const testStart = sheet.applied.testYears > 0 ? sheet.testStart : null

  const icBars: Bar[] = sheet.icDecay.map((p) => ({
    key: p.horizon,
    label: `${p.horizon}d`,
    value: p.ic,
    fill: fillOf(cutoffTone(p.ic)),
    title: `IC against returns ${p.horizon} day${p.horizon > 1 ? 's' : ''} ahead: ${fmt.ratio(p.ic, 4)}`,
  }))
  const quintileBars: Bar[] = sheet.quintiles.map((q) => ({
    key: q.quintile,
    label: `Q${q.quintile}`,
    value: q.meanReturn,
    fill: fillOf(signTone(q.meanReturn)),
    title: `Quintile ${q.quintile}: mean next-day return ${fmt.bps(q.meanReturn, 1)}`,
  }))
  const yearlyBars: Bar[] = sheet.yearly.map((y) => ({
    key: y.year,
    label: String(y.year).slice(2),
    value: y.sharpe,
    fill: y.inTest ? 'test' : 'train',
    title: `${y.year}: Sharpe ${fmt.ratio(y.sharpe)} (${y.inTest ? 'test, held out' : 'train'})`,
  }))

  return (
    <>
      <Panel
        title="Headline"
        description={
          <Applied sheet={sheet}>
            Figures are for the train period, the years you research on. The test period is shown
            once, labelled: choose on train.
          </Applied>
        }
        actions={
          <Badge tone={sheet.passed ? 'profit' : 'loss'}>
            {sheet.passed ? 'All checks pass' : 'Fails checks'}
          </Badge>
        }
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
          <Metric
            boxed
            label={<Label text="Sharpe" tip={EXPLAIN.trainSharpe} />}
            value={fmt.ratio(train.sharpe)}
            tone={figureTone('sharpe', train.sharpe, limits)}
          />
          <Metric
            boxed
            label={<Label text="Test Sharpe" tip={EXPLAIN.testSharpe} />}
            value={fmt.ratio(sheet.test.sharpe)}
            hint={testStart ? `Held out from ${fmt.date(testStart)}` : 'No test period'}
          />
          <Metric
            boxed
            label={<Label text="Fitness" tip={EXPLAIN.fitness} />}
            value={fmt.ratio(train.fitness)}
            tone={figureTone('fitness', train.fitness, limits)}
          />
          <Metric
            boxed
            label={<Label text="Turnover" tip={EXPLAIN.turnover} />}
            value={fmt.pct(train.turnover, 2)}
            tone={figureTone('turnover', train.turnover, limits)}
          />
          <Metric
            boxed
            label={<Label text="Returns" tip={EXPLAIN.returns} />}
            value={fmt.pct(train.returns, 2)}
            tone={figureTone('returns', train.returns, limits)}
          />
          <Metric
            boxed
            label={<Label text="Drawdown" tip={EXPLAIN.drawdown} />}
            value={fmt.pct(train.drawdown, 2)}
          />
          <Metric
            boxed
            label={<Label text="Margin" tip={EXPLAIN.margin} />}
            value={fmt.bps(train.margin, 1)}
            tone={figureTone('margin', train.margin, limits)}
          />
          <Metric
            boxed
            label={<Label text="Large-cap Sharpe" tip={EXPLAIN.subUniverse} />}
            value={fmt.ratio(sheet.subUniverseSharpe)}
            tone={cutoffTone(sheet.subUniverseSharpe)}
          />
        </div>
      </Panel>

      <Panel title="Cumulative PnL" description={CHART_CAPTION.pnl}>
        <div className="flex flex-col gap-3">
          <PortfolioChart
            dates={dates}
            curve={curve}
            testStart={testStart}
            label={`Cumulative PnL of ${sheet.expression}`}
          />
          <ChartLegend testStart={testStart} />
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Information coefficient" description={CHART_CAPTION.icDecay}>
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-3 gap-3">
              <Metric
                size="sm"
                label={<Label text="Mean IC" tip={EXPLAIN.icMean} />}
                value={fmt.ratio(sheet.ic.mean, 4)}
                tone={cutoffTone(sheet.ic.mean)}
              />
              <Metric
                size="sm"
                label={<Label text="IC t-stat" tip={EXPLAIN.icT} />}
                value={fmt.ratio(sheet.ic.t, 1)}
                tone={cutoffTone(sheet.ic.t)}
              />
              <Metric
                size="sm"
                label={<Label text="Days IC > 0" tip={EXPLAIN.icHit} />}
                value={fmt.pct(sheet.ic.hit, 0)}
              />
            </div>
            <Bars
              bars={icBars}
              show={(v) => fmt.ratio(v, 4)}
              label="IC decay: IC against returns 1, 2, 5, 10 and 21 days ahead"
            />
          </div>
        </Panel>

        <Panel title="Quintile returns" description={CHART_CAPTION.quintiles}>
          <Bars
            bars={quintileBars}
            show={(v) => fmt.bps(v, 1).replace(' bps', '')}
            label="Mean next-day return of each fifth of stocks, ranked by the signal, in basis points"
          />
          <p className="mt-2 text-body-compact text-ink-subtle">
            Mean next-day return, in basis points (1 bp = 0.01%). Q1 holds the lowest-ranked fifth.
          </p>
        </Panel>

        <Panel title="Sharpe by year" description={CHART_CAPTION.yearly}>
          <Bars
            bars={yearlyBars}
            show={(v) => fmt.ratio(v, 1)}
            label="Sharpe ratio for each calendar year"
          />
          <YearLegend />
        </Panel>

        <Panel title="Checks" description={CHART_CAPTION.checks} bodyClassName="p-0">
          <table className="w-full text-body-compact">
            <thead>
              <tr className="h-8 border-b border-hairline-strong text-left text-caption text-ink-subtle uppercase">
                <th className="px-4 font-medium">Check</th>
                <th className="px-2 font-medium">Result</th>
                <th className="px-2 text-right font-medium">Value</th>
                <th className="px-4 text-right font-medium">Limit</th>
              </tr>
            </thead>
            <tbody>
              {sheet.checks.map((c) => (
                <tr key={c.name} className="h-8 border-b border-hairline-subtle last:border-0">
                  <td className="px-4">
                    <span className="inline-flex items-center gap-1 text-ink">
                      <span className="num">{c.name}</span>
                      <Tip title={c.name}>{CHECK_EXPLAIN[c.name] ?? 'A BRAIN-like check.'}</Tip>
                    </span>
                  </td>
                  <td className="px-2">
                    <Badge tone={checkTone(c.result)}>{c.result}</Badge>
                  </td>
                  <td className="num px-2 text-right text-ink">{checkFigure(c.name, c.value)}</td>
                  <td className="num px-4 text-right text-ink-subtle">
                    {checkFigure(c.name, c.limit)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  )
}

/** How the Gym actually ran the settings: it scales BRAIN's to its own small market. */
function Applied({ sheet, children }: { sheet: GymTearSheet; children: ReactNode }) {
  const a = sheet.applied
  return (
    <span className="flex flex-col gap-1">
      <span>{children}</span>
      <span className="inline-flex flex-wrap items-center gap-x-1">
        Ran on <span className="num text-ink-muted">{a.stocks}</span> synthetic stocks ·{' '}
        {neutralizationLabel(a.neutralization)} neutralization · decay{' '}
        <span className="num text-ink-muted">{a.decay}</span> · truncation{' '}
        <span className="num text-ink-muted">{fmt.pct(a.truncation, 0)}</span>
        <Tip title="Settings on the Gym">
          The Gym has 400 stocks, one region and delay 1. It scales BRAIN's universes to its size
          (TOP1000 is the largest third) and has no subindustries, so SUBINDUSTRY runs as INDUSTRY.
          Compare Gym figures with other Gym figures, not with BRAIN's.
        </Tip>
      </span>
    </span>
  )
}

function YearLegend() {
  const swatch = (fill: keyof typeof FILL, text: string) => (
    <span className="flex items-center gap-1.5">
      <span className={`size-2.5 rounded-xs ${FILL[fill]}`} aria-hidden />
      {text}
    </span>
  )
  return (
    <p className="mt-2 flex flex-wrap gap-4 text-body-compact text-ink-subtle">
      {swatch('train', 'Train years')}
      {swatch('test', 'Test years (held out)')}
    </p>
  )
}
