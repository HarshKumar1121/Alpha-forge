/**
 * Every submittable Alpha from every task, on the same pane a single task's results use.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { CORE_ORDER } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { AlphaPane } from '@/screens/tasks/alpha-pane'
import { labTasks, type RankedAlpha } from '@/screens/tasks/api'
import { TASK_OFFER } from '@/screens/tasks/columns'
import type { Sort } from '@/ui/table'

/** A task's columns, minus Checks Failed — every row here has none — plus the task. */
const OFFER = [...TASK_OFFER.filter((k) => k !== 'failedChecks'), 'taskName']

const DEFAULTS = [
  'alphaId',
  'taskName',
  'region',
  'delay',
  'universe',
  'neutralization',
  'investability',
  'powerPoolCorrelation',
  'prodCorrelation',
  ...CORE_ORDER,
  'afterCostSharpe',
  'testSharpe',
  'testFitness',
]

export function SubmittableAlphas({ onOpenAlpha }: { onOpenAlpha: (alphaId: string) => void }) {
  const query = useQuery({ queryKey: ['submittable-alphas'], queryFn: labTasks.submittable })
  // Its own key, refreshed at most every 30s: reading every task's Alphas takes seconds on a
  // large account, too long to redo on each of the Tasks screen's two-second updates.
  useRefetchOn('studies', ['submittable-alphas'], 30_000)
  const [sort, setSort] = useState<Sort>({ key: 'sharpe', desc: true })
  const rows = useMemo<RankedAlpha[]>(() => query.data ?? [], [query.data])

  return (
    <AlphaPane
      title="Submittable Alphas"
      view="submittable"
      rows={rows}
      offer={OFFER}
      defaults={DEFAULTS}
      sort={sort}
      onSort={setSort}
      loading={query.isPending}
      error={query.error}
      onRefresh={() => query.refetch()}
      onOpenAlpha={onOpenAlpha}
    />
  )
}
