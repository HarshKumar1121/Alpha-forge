/** Every scope at a glance. A row opens that scope. Each scope has its own 100,000 points. */

import { DASH, fmt } from '@/lib/format'
import { Badge, Panel } from '@/ui/kit'
import { type Column, DataTable } from '@/ui/table'
import type { OsmosisSummary, OsmosisSummaryRow } from './api'
import { basisLabel, methodLabel, Tip } from './scope'

const key = (r: Pick<OsmosisSummaryRow, 'region' | 'delay'>) => `${r.region} D${r.delay}`

const COLUMNS: Column<OsmosisSummaryRow>[] = [
  {
    key: 'scope',
    header: (
      <Tip text="Region and delay. Each scope is planned on its own, with its own 100,000 points.">
        Scope
      </Tip>
    ),
    width: '110px',
    cell: (r) => <span className="num">{key(r)}</span>,
  },
  {
    key: 'alphas',
    header: (
      <Tip text="Submitted alphas in this scope with stored PnL. Every one is a candidate.">
        Alphas
      </Tip>
    ),
    width: '72px',
    align: 'right',
    cell: (r) => <span className="num">{fmt.int(r.alphaCount)}</span>,
  },
  {
    key: 'rec',
    header: (
      <Tip text="The N where adding more alphas stops improving combined Sharpe by much. Unverified rule of thumb: Osmosis reportedly needs at least 10 alphas per scope.">
        Recommended N
      </Tip>
    ),
    width: '104px',
    align: 'right',
    cell: (r) => <span className="num">{r.blocked ? DASH : fmt.int(r.recommendedN)}</span>,
  },
  {
    key: 'n',
    header: (
      <Tip text="The N this summary allocates: the recommendation, unless changed on the scope.">
        N used
      </Tip>
    ),
    width: '72px',
    align: 'right',
    cell: (r) => <span className="num">{r.blocked ? DASH : fmt.int(r.n)}</span>,
  },
  {
    key: 'method',
    header: <Tip text="The method whose weights ship for this scope.">Method</Tip>,
    width: 'minmax(170px,1.4fr)',
    cell: (r) => (
      <span className="flex flex-wrap items-center gap-2">
        {r.blocked ? DASH : methodLabel(r.method)}
        {!r.blocked && (
          <Badge tone={r.validated ? 'profit' : 'muted'}>
            {r.validated ? 'Validated' : 'Not validated'}
          </Badge>
        )}
      </span>
    ),
  },
  {
    key: 'sharpe',
    header: (
      <Tip text="Sharpe of the combined allocation at its N and method, over the selection window (training data).">
        Combined Sharpe
      </Tip>
    ),
    width: '96px',
    align: 'right',
    cell: (r) => <span className="num">{fmt.ratio(r.combinedSharpe)}</span>,
  },
  {
    key: 'drawdown',
    header: (
      <Tip text="Largest fall from a running peak of the combined allocation's cumulative PnL, over the selection window, in PnL units.">
        Max drawdown
      </Tip>
    ),
    width: '112px',
    align: 'right',
    cell: (r) => <span className="num">{r.blocked ? DASH : fmt.int(r.maxDrawdown)}</span>,
  },
  {
    key: 'basis',
    header: (
      <Tip text="The PnL the numbers are on. After cost charges 5 bps per dollar traded. A scope is net only when every candidate has turnover stored; otherwise gross.">
        PnL basis
      </Tip>
    ),
    width: '130px',
    cell: (r) => <span className="text-ink-muted">{basisLabel(r.basis)}</span>,
  },
  {
    key: 'notes',
    header: 'Notes',
    width: 'minmax(180px,1.6fr)',
    cell: (r) => (
      <span className="flex flex-wrap gap-2">
        {r.blocked && <Badge tone="warn">{r.blocked}</Badge>}
        {r.belowMinimum && !r.blocked && (
          <Badge
            tone="warn"
            title="Osmosis reportedly needs at least 10 alphas per scope. This is an unverified rule."
          >
            Below 10 alphas (unverified Osmosis minimum)
          </Badge>
        )}
      </span>
    ),
  },
]

export function SummaryPanel({
  summary,
  selected,
  onSelect,
}: {
  summary: OsmosisSummary
  selected: { region: string; delay: number } | null
  onSelect: (region: string, delay: number) => void
}) {
  const rows = [...summary.scopes].sort((a, b) => b.alphaCount - a.alphaCount)
  const active = selected ? key(selected) : null
  return (
    <Panel
      title="All regions"
      description="Every scope with its own 100,000 points. Click a row to open it. What this tells you: where the alphas are, and whether each scope's history supports a validated method."
    >
      <DataTable
        label="All regions summary"
        rows={rows}
        rowKey={key}
        activeKey={active}
        onRowClick={(r) => onSelect(r.region, r.delay)}
        columns={COLUMNS}
        maxHeight="none"
      />
      {summary.parentsExcluded.length > 0 && (
        <p className="mt-2 text-xs text-ink-subtle">
          Excluded from every scope: {summary.parentsExcluded.length} parent alpha(s), region ALL,
          no PnL of their own ({summary.parentsExcluded.join(', ')}).
        </p>
      )}
    </Panel>
  )
}
