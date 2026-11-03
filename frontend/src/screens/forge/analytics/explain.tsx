/**
 * Factor attribution: how much of this alpha's PnL is explained by a handful of well-known
 * risk factors (config/forge/factors.yaml), versus what is left over once they are subtracted
 * out. A high residual Sharpe after removing the factors it loads on is a better sign than the
 * same Sharpe explained entirely by, say, size or momentum exposure.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { ApiError } from '@/api/http'
import { fmt } from '@/lib/format'
import { Tip } from '@/screens/alpha/panels'
import { color } from '@/screens/pool/pnl-chart'
import { Empty, ErrorNotice, Metric, Panel, Skeleton } from '@/ui/kit'
import { ChartFigure, chartBase, EChart } from '../charts/echart'
import { forgeAnalytics } from './api'

/** Mirrors `config/forge/factors.yaml`'s ids — the API returns ids only, not display names. */
const FACTOR_LABELS: Record<string, string> = {
  momentum: 'Momentum',
  short_term_reversal: 'Short-term reversal',
  size: 'Size',
  low_volatility: 'Low volatility',
  liquidity: 'Liquidity',
}

function Label({ text, tip }: { text: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {text}
      <Tip title={text}>{tip}</Tip>
    </span>
  )
}

const EXPLAIN = {
  alpha: "The part of daily PnL the factors don't explain at all — the regression's intercept.",
  rSquared:
    'How much of the day-to-day PnL swings these factors explain together, from 0 to 1. Low means the PnL mostly moves on its own, not with known factors.',
  residualSharpe:
    'Sharpe of the PnL left over after subtracting out what the factors explain. Compare it with the headline Sharpe: if it barely drops, the edge is not just a factor bet.',
  betas:
    "Each factor's loading: how much the alpha's daily PnL moves per unit of that factor's own PnL, holding the others fixed.",
} as const

export function Explain({ alphaId }: { alphaId: string }) {
  const query = useQuery({
    queryKey: ['forge-analytics-factors', alphaId],
    queryFn: () => forgeAnalytics.factors(alphaId),
    retry: (count, e) => !(e instanceof ApiError && e.status === 404) && count < 2,
  })

  const betaOption = useMemo(() => {
    if (!query.data) return null
    const base = chartBase()
    const ids = query.data.factorsUsed
    const betas = ids.map((id) => query.data?.betas[id] ?? 0)
    return {
      ...base,
      xAxis: { type: 'category', data: ids.map((id) => FACTOR_LABELS[id] ?? id), ...base.axis },
      yAxis: { type: 'value', name: 'Beta', ...base.axis },
      series: [
        {
          type: 'bar',
          data: betas.map((v) => ({
            value: v,
            itemStyle: { color: color(v >= 0 ? 'color-pnl-positive' : 'color-pnl-negative') },
          })),
        },
      ],
    }
  }, [query.data])

  if (query.isError) {
    const notOnBoard = query.error instanceof ApiError && query.error.code === 'unknown_alpha'
    if (notOnBoard) {
      return (
        <Panel title="Factor attribution">
          <Empty title="Not on the Submittable Board yet">
            Factor attribution needs the alpha's own settings (region, delay, universe) to run the
            factor expressions in a comparable scope — only alphas the Board already knows about can
            be regressed.
          </Empty>
        </Panel>
      )
    }
    return (
      <Panel title="Factor attribution">
        <ErrorNotice error={query.error} title="Could not run the factor regression" />
      </Panel>
    )
  }

  if (!query.data) {
    return (
      <Panel title="Factor attribution">
        <Skeleton className="h-48" label="Running the factor regression" />
      </Panel>
    )
  }

  const { alpha, rSquared, residualSharpe, nObs, factorsUsed } = query.data
  const dropped = Object.keys(FACTOR_LABELS).filter((id) => !factorsUsed.includes(id))

  return (
    <Panel
      title="Factor attribution"
      description={`Over ${fmt.int(nObs)} overlapping days against ${factorsUsed.length} of ${Object.keys(FACTOR_LABELS).length} named factors.`}
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Metric
            size="sm"
            label={<Label text="Alpha" tip={EXPLAIN.alpha} />}
            value={fmt.ratio(alpha, 4)}
          />
          <Metric
            size="sm"
            label={<Label text="R²" tip={EXPLAIN.rSquared} />}
            value={fmt.ratio(rSquared)}
          />
          <Metric
            size="sm"
            label={<Label text="Residual Sharpe" tip={EXPLAIN.residualSharpe} />}
            value={fmt.ratio(residualSharpe)}
          />
        </div>
        {betaOption && factorsUsed.length > 0 ? (
          <ChartFigure caption={EXPLAIN.betas}>
            <EChart option={betaOption} label="Factor betas" />
          </ChartFigure>
        ) : (
          <Empty title="No factor had enough overlapping history">
            Every named factor needs at least a few months of days in common with this alpha to be
            included.
          </Empty>
        )}
        {dropped.length > 0 && (
          <p className="text-body-compact text-ink-subtle">
            Dropped for too little overlapping history:{' '}
            {dropped.map((id) => FACTOR_LABELS[id]).join(', ')}.
          </p>
        )}
      </div>
    </Panel>
  )
}
