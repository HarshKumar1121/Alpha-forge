/**
 * Every submittable Alpha from every task, on the same pane a single task's results use.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/cn'
import { DASH, fmt } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { AlphaPane } from '@/screens/tasks/alpha-pane'
import { labTasks, type RankedAlpha, type TaskAlpha } from '@/screens/tasks/api'
import { AFTER_COST_HEADER, DELAY, INVESTABILITY, investability } from '@/screens/tasks/columns'
import { signTone, TEXT_TONE } from '@/ui/kit'
import type { Column, Sort } from '@/ui/table'

const setting = (r: RankedAlpha, key: string) => String(r.settings?.[key] ?? '')
const task = (r: RankedAlpha) => (r as TaskAlpha).taskName ?? ''

const METRICS: { key: keyof RankedAlpha; label: string; show: (v: number | null) => string }[] = [
  { key: 'sharpe', label: 'Sharpe', show: (v) => fmt.ratio(v) },
  { key: 'turnover', label: 'Turnover', show: (v) => fmt.pct(v, 2) },
  { key: 'fitness', label: 'Fitness', show: (v) => fmt.ratio(v) },
  { key: 'returns', label: 'Returns', show: (v) => fmt.pct(v, 2) },
  { key: 'drawdown', label: 'Drawdown', show: (v) => fmt.pct(v, 2) },
  { key: 'margin', label: 'Margin', show: (v) => fmt.bps(v, 2) },
  { key: 'afterCostSharpe', label: 'After-Cost Sharpe', show: (v) => fmt.ratio(v) },
]

const SIGNED = new Set(['sharpe', 'fitness', 'returns', 'margin', 'afterCostSharpe'])

/** The task's own columns, minus Checks Failed — every row here has none — plus the task. */
const columns = (): Column<RankedAlpha>[] => [
  {
    key: 'region',
    header: 'Region',
    width: 'minmax(70px,0.7fr)',
    sortable: true,
    cell: (r) => <span className="num">{setting(r, 'region') || DASH}</span>,
  },
  DELAY,
  {
    key: 'universe',
    header: 'Universe',
    width: 'minmax(84px,0.9fr)',
    sortable: true,
    cell: (r) => <span className="num truncate">{setting(r, 'universe') || DASH}</span>,
  },
  {
    key: 'neutralization',
    header: 'Neutralization',
    width: 'minmax(96px,1fr)',
    sortable: true,
    cell: (r) => <span className="num truncate">{setting(r, 'neutralization') || DASH}</span>,
  },
  INVESTABILITY,
  ...METRICS.map(
    (m): Column<RankedAlpha> => ({
      key: String(m.key),
      header: m.key === 'afterCostSharpe' ? AFTER_COST_HEADER : m.label,
      width: 'minmax(84px,0.8fr)',
      align: 'right',
      sortable: true,
      cell: (r) => {
        const value = r[m.key] as number | null
        return (
          <span className={cn('num', SIGNED.has(String(m.key)) && TEXT_TONE[signTone(value)])}>
            {m.show(value)}
          </span>
        )
      },
    }),
  ),
  {
    key: 'task',
    header: 'Task',
    width: 'minmax(180px,1.4fr)',
    sortable: true,
    cell: (r) => <span className="truncate text-ink-muted">{task(r)}</span>,
  },
]

function compare(a: RankedAlpha, b: RankedAlpha, sort: Sort): number {
  const pick = (r: RankedAlpha): string | number | null => {
    switch (sort.key) {
      case 'region':
      case 'universe':
      case 'neutralization':
        return setting(r, sort.key)
      case 'investability':
        return investability(r)
      case 'delay':
        return (r.settings?.['delay'] as number | undefined) ?? null
      case 'task':
        return task(r)
      default:
        return (r as unknown as Record<string, number | null>)[sort.key] ?? null
    }
  }
  const x = pick(a)
  const y = pick(b)
  if (x == null) return y == null ? 0 : 1
  if (y == null) return -1
  const order = typeof x === 'string' ? x.localeCompare(String(y)) : Number(x) - Number(y)
  return sort.desc ? -order : order
}

export function SubmittableAlphas() {
  const query = useQuery({ queryKey: ['submittable-alphas'], queryFn: labTasks.submittable })
  // Its own key, refreshed at most every 30s: reading every task's Alphas takes about a second,
  // too long to redo on each of the Tasks screen's two-second updates.
  useRefetchOn('studies', ['submittable-alphas'], 30_000)
  const [sort, setSort] = useState<Sort>({ key: 'sharpe', desc: true })
  const rows = useMemo<RankedAlpha[]>(() => query.data ?? [], [query.data])

  return (
    <AlphaPane
      title="Submittable Alphas"
      rows={rows}
      columns={columns}
      compare={compare}
      poolColumnAfter="investability"
      sort={sort}
      onSort={setSort}
      loading={query.isPending}
      error={query.error}
      onRefresh={() => query.refetch()}
    />
  )
}
