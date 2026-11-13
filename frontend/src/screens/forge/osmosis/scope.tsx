/** One scope in full: the allocation table with its totals, the N curve, the combined
 *  performance against equal weight, what was left out, and the method comparison. Every number
 *  has a tooltip and every chart says what it tells you. Combination statistics only: BRAIN's
 *  multiplier formula is not known to this tool. */

import type { ReactNode } from 'react'
import { DASH, fmt, isNum } from '@/lib/format'
import { EChart } from '@/screens/forge/charts/echart'
import { Badge, Metric, Notice, Panel, type Tone } from '@/ui/kit'
import { Tooltip } from '@/ui/overlay'
import { type Column, DataTable } from '@/ui/table'
import type { OsmosisExcluded, OsmosisMethod, OsmosisRow, OsmosisScopeView } from './api'

/** The methods' names as the screen shows them. Keys match the backend's method names. */
export const METHOD_LABEL: Record<string, string> = {
  equal_weight: 'Equal weight',
  equal_risk_contribution: 'Equal risk contribution',
  hrp: 'Hierarchical risk parity',
  inverse_volatility: 'Inverse volatility',
  max_sharpe: 'Max Sharpe',
  min_variance: 'Minimum variance',
}

export const methodLabel = (method: string | null | undefined) =>
  method ? (METHOD_LABEL[method] ?? method) : DASH

// forge: the PnL basis as the screen names it. Gross is BRAIN's own figure; net is after a cost.
export const basisLabel = (basis: string | null | undefined) =>
  basis === 'after_cost_5bps' ? 'After cost, 5 bps' : basis === 'gross' ? 'Gross' : (basis ?? DASH)

/** A Sharpe-scale number with its sign, so a negative gap reads as negative. */
export const signed = (v: number | null | undefined) =>
  isNum(v) ? `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}` : DASH

/** A term's plain-language tooltip, on the text that names it. */
export function Tip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip content={text}>
      <span className="cursor-help underline decoration-dotted decoration-ink-subtle underline-offset-2">
        {children}
      </span>
    </Tooltip>
  )
}

/** The screen's words for BRAIN's Osmosis result. A missing result is "Not checked", never a
 *  gate: the check is shown for information only. */
export const OSMOSIS_LABEL: Record<string, { text: string; tone: Tone | 'outline'; tip: string }> =
  {
    eligible: { text: 'Pass', tone: 'profit', tip: 'BRAIN passed the Osmosis check.' },
    warning: {
      text: 'Warning',
      tone: 'warn',
      tip: 'BRAIN did not generate the Daily Osmosis Rank for this Alpha.',
    },
    refused: { text: 'Fail', tone: 'loss', tip: 'BRAIN returned a failing Osmosis check.' },
    pending: {
      text: 'Pending',
      tone: 'outline',
      tip: 'BRAIN has not judged the Osmosis check yet.',
    },
    unknown: {
      text: 'Unrecognised',
      tone: 'outline',
      tip: 'BRAIN returned a result the tool does not know.',
    },
    absent: {
      text: 'Not checked',
      tone: 'muted',
      tip: 'BRAIN has not returned the OSMOSIS_ALLOCATION check for this alpha. It is shown for information only and does not affect the allocation.',
    },
  }

const osmosisLabel = (status: string) =>
  OSMOSIS_LABEL[status] ?? { text: status, tone: 'outline' as const, tip: '' }

const pct = (v: number | null | undefined, digits = 1) => fmt.pct(v, digits)

interface Spec {
  key: string
  label: string
  tip: string
  width: string
  align?: 'left' | 'right'
  /** The cell's text: the same string the table shows, and the one the CSV writes. */
  text: (row: OsmosisRow) => string
  cell?: (row: OsmosisRow) => ReactNode
}

const COLS: Spec[] = [
  {
    key: 'rank',
    label: 'Rank',
    tip:
      'Greedy order: 1 is the Alpha that most raised the combined Sharpe when it was added, ' +
      'over the selection window.',
    width: '52px',
    align: 'right',
    text: (r) => String(r.rank),
  },
  {
    key: 'alpha',
    label: 'Alpha',
    tip: "BRAIN's alpha ID.",
    width: '112px',
    text: (r) => r.alphaId,
  },
  {
    key: 'classes',
    label: 'Classifications',
    tip: "BRAIN's classifications for the Alpha, as stored.",
    width: 'minmax(150px,1.4fr)',
    text: (r) => (r.classifications.length ? r.classifications.join('; ') : DASH),
  },
  {
    key: 'universe',
    label: 'Universe',
    tip: 'The stock universe the Alpha trades.',
    width: '96px',
    text: (r) => r.universe ?? DASH,
  },
  {
    key: 'sharpe',
    label: 'Sharpe',
    tip: "BRAIN's annualised Sharpe for this Alpha, full history.",
    width: '64px',
    align: 'right',
    text: (r) => fmt.ratio(r.sharpe),
  },
  {
    key: 'turnover',
    label: 'Turnover',
    tip: "BRAIN's daily turnover for this Alpha: the share of the book it trades each day.",
    width: '72px',
    align: 'right',
    text: (r) => pct(r.turnover),
  },
  {
    key: 'fitness',
    label: 'Fitness',
    tip: "BRAIN's fitness: Sharpe scaled down for turnover. Higher is better.",
    width: '64px',
    align: 'right',
    text: (r) => fmt.ratio(r.fitness),
  },
  {
    key: 'drawdown',
    label: 'Drawdown',
    tip: "BRAIN's largest peak-to-trough fall in this Alpha's PnL, as a share of its book.",
    width: '80px',
    align: 'right',
    text: (r) => pct(r.drawdown),
  },
  {
    key: 'prod',
    label: 'Prod corr',
    tip: "This Alpha's correlation with BRAIN's production alphas. Lower means more diversification.",
    width: '76px',
    align: 'right',
    text: (r) => fmt.ratio(r.prodCorrelation),
  },
  {
    key: 'maxcorr',
    label: 'Max corr in set',
    tip: 'Largest absolute correlation with another Alpha in this allocation, over the selection window. The cap is 0.70.',
    width: '96px',
    align: 'right',
    text: (r) => fmt.ratio(r.maxAbsCorrelation),
  },
  {
    key: 'osmosis',
    label: 'Osmosis',
    tip: "BRAIN's Osmosis check, as stored. Shown for information; it does not affect the allocation.",
    width: '104px',
    text: (r) => osmosisLabel(r.osmosisStatus).text,
    cell: (r) => {
      const label = osmosisLabel(r.osmosisStatus)
      return (
        <Tooltip content={label.tip}>
          <Badge tone={label.tone}>{label.text}</Badge>
        </Tooltip>
      )
    },
  },
  {
    key: 'weight',
    label: 'Weight',
    tip: "This Alpha's share of the scope's total points.",
    width: '72px',
    align: 'right',
    text: (r) => pct(r.weight, 2),
  },
  {
    key: 'points',
    label: 'Points',
    tip: 'Whole points to type into BRAIN. The column sums to exactly 100,000 for this scope.',
    width: '84px',
    align: 'right',
    text: (r) => fmt.int(r.points),
  },
]

/** The table's columns, with each header carrying its tooltip. */
export const TABLE_COLUMNS: Column<OsmosisRow>[] = COLS.map((c) => ({
  key: c.key,
  header: <Tip text={c.tip}>{c.label}</Tip>,
  width: c.width,
  align: c.align ?? 'left',
  cell: c.cell ?? ((r: OsmosisRow) => <span className="num">{c.text(r)}</span>),
}))

/** The allocation as CSV: exactly the table's text, with the totals row last. */
export function allocationCsv(view: OsmosisScopeView): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const lines = [COLS.map((c) => esc(c.label)).join(',')]
  for (const row of view.rows) lines.push(COLS.map((c) => esc(c.text(row))).join(','))
  lines.push(
    COLS.map((c) => {
      if (c.key === 'alpha') return esc('Total')
      if (c.key === 'weight')
        return esc(
          pct(
            view.rows.reduce((s, r) => s + r.weight, 0),
            2,
          ),
        )
      if (c.key === 'points') return esc(fmt.int(view.totalPoints))
      return ''
    }).join(','),
  )
  return `${lines.join('\n')}\n`
}

const EXCLUDED_COLUMNS: Column<OsmosisExcluded>[] = [
  {
    key: 'alpha',
    header: <Tip text="BRAIN's alpha ID.">Alpha</Tip>,
    width: '112px',
    cell: (r) => <span className="num">{r.alphaId}</span>,
  },
  {
    key: 'reason',
    header: <Tip text="Why the Alpha is not in the ranking.">Reason</Tip>,
    width: '96px',
    cell: (r) => (
      <Badge tone={r.reason === 'correlation' ? 'warn' : 'muted'}>
        {r.reason === 'correlation'
          ? 'Correlated'
          : r.reason === 'gap'
            ? 'Gap in history'
            : 'No variation'}
      </Badge>
    ),
  },
  {
    key: 'detail',
    header: 'Detail',
    width: 'minmax(220px,2fr)',
    cell: (r) => <span className="text-ink-muted">{r.detail}</span>,
  },
  {
    key: 'against',
    header: <Tip text="The ranked Alpha this one correlates with most strongly.">Against</Tip>,
    width: '112px',
    cell: (r) => <span className="num">{r.correlatedWith ?? DASH}</span>,
  },
  {
    key: 'corr',
    header: (
      <Tip text="Absolute daily-PnL correlation over the selection window. The cap is 0.70.">
        Correlation
      </Tip>
    ),
    width: '92px',
    align: 'right',
    cell: (r) => <span className="num">{fmt.ratio(r.correlation)}</span>,
  },
]

const METHOD_COLUMNS: Column<OsmosisMethod>[] = [
  {
    key: 'method',
    header: <Tip text="The weighting method.">Method</Tip>,
    width: 'minmax(180px,1.4fr)',
    cell: (r) => (
      <span className="flex items-center gap-2">
        {methodLabel(r.method)}
        {r.isPlan && <Badge tone="profit">Shipped</Badge>}
      </span>
    ),
  },
  {
    key: 'sharpe',
    header: (
      <Tip text="Annualised Sharpe of the method's out-of-sample PnL, over every walk-forward fold.">
        Sharpe
      </Tip>
    ),
    width: '72px',
    align: 'right',
    cell: (r) => <span className="num">{fmt.ratio(r.sharpe)}</span>,
  },
  {
    key: 'drawdown',
    header: (
      <Tip text="Largest fall from a running peak of the out-of-sample cumulative PnL, in PnL units.">
        Drawdown
      </Tip>
    ),
    width: '84px',
    align: 'right',
    cell: (r) => <span className="num">{fmt.ratio(r.maxDrawdown)}</span>,
  },
  {
    key: 'turnover',
    header: <Tip text="Average share of weight moved between consecutive folds.">Turnover</Tip>,
    width: '76px',
    align: 'right',
    cell: (r) => <span className="num">{pct(r.turnover)}</span>,
  },
  {
    key: 'win',
    header: (
      <Tip text="Share of folds where the method's mean daily PnL beat equal weight's.">
        Fold win rate
      </Tip>
    ),
    width: '84px',
    align: 'right',
    cell: (r) => <span className="num">{pct(r.foldWinRate, 0)}</span>,
  },
  {
    key: 'gain',
    header: <Tip text="Sharpe gap over equal weight, on the holdout folds only.">Gap vs equal</Tip>,
    width: '84px',
    align: 'right',
    cell: (r) => (
      <span className="num">
        {r.isPlan || r.identical || r.gain == null ? DASH : signed(r.gain)}
      </span>
    ),
  },
  {
    key: 'ci',
    header: (
      <Tip text="95% paired bootstrap interval for the gap. The method is validated only when its lower end is above zero.">
        95% interval
      </Tip>
    ),
    width: '120px',
    cell: (r) => (
      <span className="num">
        {r.ciLow == null || r.ciHigh == null ? DASH : `${signed(r.ciLow)} to ${signed(r.ciHigh)}`}
      </span>
    ),
  },
  {
    key: 'passed',
    header: (
      <Tip text="Whether the method's gap over equal weight held on the holdout folds.">
        Holdout check
      </Tip>
    ),
    width: '100px',
    cell: (r) =>
      r.identical ? (
        <Tip text="The holdout days are identical to equal weight's, so there is nothing to test. Not a failure and not a pass.">
          <Badge tone="muted">Identical, not a test</Badge>
        </Tip>
      ) : r.passedHoldout == null ? (
        <span className="text-ink-subtle">{DASH}</span>
      ) : (
        <Badge tone={r.passedHoldout ? 'profit' : 'muted'}>
          {r.passedHoldout ? 'Passed' : 'Did not pass'}
        </Badge>
      ),
  },
]

/** The N curve's chart, with the recommended N marked. */
function CurveChart({ view }: { view: OsmosisScopeView }) {
  const points = view.curve.filter((c) => isNum(c.combinedSharpe))
  const option = {
    grid: { left: 48, right: 16, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', name: 'Alphas (N)', data: points.map((c) => String(c.n)) },
    yAxis: { type: 'value', name: 'Sharpe' },
    series: [
      {
        type: 'line',
        name: 'Combined Sharpe',
        data: points.map((c) => c.combinedSharpe),
        symbolSize: 5,
        markLine: {
          symbol: 'none',
          data: [{ xAxis: String(view.recommendedN), name: 'Recommended' }],
          label: { formatter: 'Recommended' },
        },
      },
    ],
  }
  return <EChart option={option} label="Combined Sharpe as Alphas are added in greedy order" />
}

/** Effective number of independent bets as Alphas are added, with the recommended N marked. */
function BetsChart({ view }: { view: OsmosisScopeView }) {
  const option = {
    grid: { left: 56, right: 16, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', name: 'Alphas (N)', data: view.betsCurve.map((b) => String(b.n)) },
    yAxis: { type: 'value', name: 'Bets' },
    series: [
      {
        type: 'line',
        name: 'Effective bets',
        data: view.betsCurve.map((b) => b.effectiveBets),
        symbolSize: 5,
        markLine: {
          symbol: 'none',
          data: [{ xAxis: String(view.recommendedN), name: 'Recommended' }],
          label: { formatter: 'Recommended' },
        },
      },
    ],
  }
  return <EChart option={option} label="Effective number of independent bets as Alphas are added" />
}

/** The basis the numbers are on, and why: shown once per scope, above the Why panel. */
function BasisLine({ view }: { view: OsmosisScopeView }) {
  return (
    <p className="text-xs text-ink-subtle">
      <Tip text="The PnL every number on this screen is measured on. After cost charges 5 bps on each dollar traded. A scope uses it only when every candidate has turnover stored; otherwise it is gross, and the reason names the alpha that lacked it.">
        PnL basis: {basisLabel(view.basis)}.
      </Tip>{' '}
      {view.basisReason}
    </p>
  )
}

/** The "Why this allocation" panel: the method, the size, and the evidence behind them. */
function WhyPanel({ view }: { view: OsmosisScopeView }) {
  const why = view.why
  const lines: { label: string; text: string; tip: string }[] = [
    {
      label: 'Method',
      text: why.methodLine,
      tip: 'How the points are split, and whether that split beat equal weight on held-out folds.',
    },
    {
      label: 'Size',
      text: why.sizeLine,
      tip: 'How many alphas the plan holds, and the rule that set that number.',
    },
    {
      label: 'Evidence',
      text: why.evidenceLine,
      tip: 'The history the method was judged on. Out-of-sample data means days the choice never saw.',
    },
    {
      label: 'Decay',
      text: why.decayLine,
      tip: 'Whether an alpha does worse out of sample than in sample. It needs out-of-sample days.',
    },
    {
      label: 'Correlation',
      text: `${why.correlationExclusions} alpha${why.correlationExclusions === 1 ? '' : 's'} left out of the ranking for correlation above the cap.`,
      tip: 'Alphas this similar to a ranked one would count twice, so they are left out.',
    },
  ]
  return (
    <Panel
      title="Why this allocation"
      description="The evidence behind the method, the size and the points, in plain words. What this tells you: how far to trust the points, and what has not been tested yet."
    >
      <dl className="grid gap-2 text-sm">
        {lines.map((l) => (
          <div key={l.label} className="grid gap-0.5 sm:grid-cols-[7rem_1fr] sm:gap-3">
            <dt className="font-medium">
              <Tip text={l.tip}>{l.label}</Tip>
            </dt>
            <dd className="text-ink-subtle">{l.text}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  )
}

/** Cumulative PnL of the allocation against equal weight, over the selection window. */
function CombinedChart({ view }: { view: OsmosisScopeView }) {
  const option = {
    grid: { left: 56, right: 16, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0 },
    xAxis: { type: 'category', data: view.chart.map((p) => p.date), boundaryGap: false },
    yAxis: { type: 'value', name: 'Cumulative PnL' },
    series: [
      {
        type: 'line',
        name: 'This allocation',
        showSymbol: false,
        data: view.chart.map((p) => p.allocation),
      },
      {
        type: 'line',
        name: 'Equal weight',
        showSymbol: false,
        data: view.chart.map((p) => p.equal),
      },
    ],
  }
  return <EChart option={option} label="Cumulative PnL of this allocation and of equal weight" />
}

function Stat({ label, a, b, tip }: { label: string; a: string; b: string; tip: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-sm border border-hairline p-2">
      <Tip text={tip}>
        <span className="text-xs text-ink-subtle">{label}</span>
      </Tip>
      <div className="grid grid-cols-2 gap-2">
        <Metric label="This allocation" value={a} size="sm" />
        <Metric label="Equal weight" value={b} size="sm" />
      </div>
    </div>
  )
}

/** The scope's body under its controls. Renders a blocked scope's reason and nothing else. */
export function ScopeBody({ view }: { view: OsmosisScopeView }) {
  if (view.blocked) {
    return (
      <Notice tone="warn" title="This scope has no allocation">
        {view.blocked}
      </Notice>
    )
  }
  const total = view.totalPoints
  const combined = view.combined
  const noVariation = view.excluded.filter((e) => e.reason === 'flat').length
  const sameDrawdown =
    view.methods.length > 1 && new Set(view.methods.map((m) => m.maxDrawdown)).size === 1
  return (
    <div className="flex flex-col gap-4">
      {view.warnings.length > 0 && (
        <Notice tone="warn" title="Read before using these points">
          <ul className="list-disc pl-4">
            {view.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}

      <BasisLine view={view} />

      <WhyPanel view={view} />

      <Panel
        title={`Allocation: ${view.n} alphas, ${methodLabel(view.method)}`}
        description="Points for this scope, sorted by points. Each scope sums to its own 100,000. Type them into BRAIN by hand."
      >
        <DataTable
          label="Allocation for this scope"
          rows={[...view.rows]}
          rowKey={(r) => r.alphaId}
          columns={TABLE_COLUMNS}
          maxHeight="none"
        />
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 border-t border-hairline pt-2 text-sm">
          <span className="font-medium">Total</span>
          <span className="num">
            <Tip text="The points this scope asks for, which must total exactly 100,000.">
              {fmt.int(total)} points
            </Tip>
          </span>
          <span className="num">
            <Tip text="The weights of the chosen Alphas, which add to 100%.">
              {pct(
                view.rows.reduce((s, r) => s + r.weight, 0),
                2,
              )}{' '}
              weight
            </Tip>
          </span>
          <span className="text-ink-subtle">{view.rows.length} alphas</span>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="How many alphas"
          description={`Combined Sharpe as alphas are added in greedy order, over the ${view.windowDays}-day selection window (training data). The recommended N comes from the effective-bets curve below, not from this one. What this tells you: how much Sharpe each extra alpha adds.`}
        >
          {view.curve.length > 0 ? (
            <CurveChart view={view} />
          ) : (
            <p className="text-ink-subtle">No ranking yet.</p>
          )}
          {view.rankedBeyondN > 0 && (
            <p className="mt-2 text-xs text-ink-subtle">
              {view.rankedBeyondN} more ranked alphas sit past N. They are not in this allocation.
            </p>
          )}
        </Panel>

        <Panel
          title="Combined performance"
          description="This allocation against equal weight of the same alphas, over the selection window (training data, the data used to choose them). What this tells you: whether weighting beat splitting evenly."
        >
          {combined && view.chart.length > 0 ? (
            <>
              <CombinedChart view={view} />
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <Stat
                  label="Sharpe"
                  tip="Annualised Sharpe of the combined PnL, over the selection window."
                  a={fmt.ratio(combined.allocation.sharpe)}
                  b={fmt.ratio(combined.equal.sharpe)}
                />
                <Stat
                  label="Max drawdown"
                  tip="Largest fall from a running peak of the combined cumulative PnL, in PnL units."
                  a={fmt.ratio(combined.allocation.maxDrawdown)}
                  b={fmt.ratio(combined.equal.maxDrawdown)}
                />
                <Stat
                  label="Turnover"
                  tip="The combined book's turnover: each Alpha's BRAIN turnover, weighted by its share."
                  a={pct(combined.allocation.turnover)}
                  b={pct(combined.equal.turnover)}
                />
              </div>
            </>
          ) : (
            <p className="text-ink-subtle">No combined series to show.</p>
          )}
        </Panel>
      </div>

      <Panel
        title="Effective bets"
        description="How many independent bets the ranked alphas really make. Alphas that trade the same signal count once. The recommended N is the smallest size after which one more alpha raises this by less than the set share. What this tells you: where extra alphas stop adding new risk."
      >
        {view.betsCurve.length > 0 ? (
          <BetsChart view={view} />
        ) : (
          <p className="text-ink-subtle">No ranking yet.</p>
        )}
      </Panel>

      <Panel
        title="Excluded"
        description="Candidates left out of the ranking: correlated above the cap with a ranked alpha, with a gap in the selection window, or with no variation in PnL. Each names the alpha it collided with."
      >
        {noVariation > 0 && (
          <p className="mb-2 text-xs text-ink-subtle">
            {noVariation} alpha{noVariation === 1 ? '' : 's'} with no PnL variation in the latest
            window. Counted here, not dropped silently: a flat PnL has no correlation to measure.
          </p>
        )}
        {view.excluded.length > 0 ? (
          <DataTable
            label="Excluded alphas"
            rows={view.excluded}
            rowKey={(r) => r.alphaId}
            columns={EXCLUDED_COLUMNS}
            maxHeight="none"
          />
        ) : (
          <p className="text-ink-subtle">Nothing excluded in this scope.</p>
        )}
      </Panel>

      <details className="rounded-sm border border-hairline p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Method comparison ({view.methods.length})
        </summary>
        <p className="mt-2 text-xs text-ink-subtle">
          Every method, measured out of sample by walk-forward against equal weight. Only the
          default is validated: a method that did not pass the holdout check is shown for
          comparison, not as a plan.
        </p>
        {sameDrawdown && (
          <p className="mt-2 text-xs text-ink-subtle">
            Every method has the same max drawdown here, so the worst stretch did not separate them:
            their combined PnL matched over it.
          </p>
        )}
        <div className="mt-2">
          {view.methods.length > 0 ? (
            <DataTable
              label="Method comparison"
              rows={view.methods}
              rowKey={(r) => r.method}
              columns={METHOD_COLUMNS}
              maxHeight="none"
            />
          ) : (
            <p className="text-ink-subtle">The walk-forward did not run for this scope.</p>
          )}
        </div>
      </details>
    </div>
  )
}
