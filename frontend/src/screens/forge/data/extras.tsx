/**
 * Forge's extra Data Explorer columns: crowding, and my own alphas and BRAIN pass rate on each
 * field. Fetched for the page on screen only, but crowding is ranked against the whole market.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import type { DataFieldRow } from '@/api/catalog'
import type { Scope } from '@/api/types'
import { DASH, fmt } from '@/lib/format'
import { Tooltip } from '@/ui/overlay'
import type { Column } from '@/ui/table'
import { type FieldExtras, forgeData } from './api'

export const CROWDING_HINT =
  'Crowding, 0–100: how much competition this already has in this market. The average of its percentile ranks on Alphas (how mined the signal is) and Users (how many researchers work it). 90 = more crowded than about 90% of what this market offers.'

export const MINE_HINT =
  'Your alphas in the vault that read this, and the share BRAIN passed on every check among those it has decided. Alphas still pending, or never checked, count in neither.'

export function useFieldExtras(scope: Scope, rows: DataFieldRow[] | undefined) {
  const ids = useMemo(() => (rows ?? []).map((r) => r.field_id), [rows])
  return useQuery({
    queryKey: ['forge-field-extras', scope, ids],
    queryFn: () => forgeData.fieldExtras(scope, ids),
    enabled: ids.length > 0,
    staleTime: 60_000,
  }).data
}

/** Crowding as a 0–100 score; the bar reads faster than the number. */
export function Crowding({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-ink-subtle">{DASH}</span>
  return (
    <span className="flex items-center justify-end gap-1.5">
      <span className="h-1.5 w-8 overflow-hidden rounded-full bg-surface-3" aria-hidden>
        <span className="block h-full bg-ink-subtle" style={{ width: `${value * 100}%` }} />
      </span>
      <span className="num">{fmt.int(value * 100)}</span>
    </span>
  )
}

/**
 * A column header that explains itself on hover.
 *
 * It is a component taking the text as a *prop*, not a `<Tooltip>` wrapped around children,
 * because `DataTable` runs its headers through `wholeWords`, which clones any element that has
 * children and rebuilds them with `Children.map` — and that returns an array. `Tooltip` would
 * then hand an array to Base UI's trigger `render`, which takes one element, and the whole
 * screen would fail to mount. An element with no children is passed through untouched.
 */
export function ColumnHint({ text, hint }: { text: string; hint: string }) {
  return (
    <Tooltip content={hint}>
      <span className="cursor-help">{text}</span>
    </Tooltip>
  )
}

/** The columns appended to the Data Explorer's, reading from this page's extras. */
export function forgeFieldColumns(extras: FieldExtras | undefined): Column<DataFieldRow>[] {
  return [
    {
      key: 'forge_crowding',
      header: <ColumnHint text="Crowding" hint={CROWDING_HINT} />,
      width: 'minmax(80px,0.6fr)',
      align: 'right',
      cell: (r) => <Crowding value={extras?.fields[r.field_id]?.crowding} />,
    },
    {
      key: 'forge_mine',
      header: <ColumnHint text="Mine" hint={MINE_HINT} />,
      width: 'minmax(84px,0.6fr)',
      align: 'right',
      cell: (r) => {
        const m = extras?.fields[r.field_id]?.mine
        if (!m?.alphas) return <span className="text-ink-subtle">{DASH}</span>
        return (
          <span className="num" title={`${m.passed} of ${m.checked} decided alphas passed`}>
            {fmt.int(m.alphas)} · {m.checked ? fmt.pct(m.passRate, 0) : DASH}
          </span>
        )
      },
    },
  ]
}
