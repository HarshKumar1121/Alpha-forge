/**
 * The Data Explorer's Datasets tab (Forge): every dataset in the market with what BRAIN pays
 * for it, how crowded it is, and what I have already done there. A row opens the dataset page.
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import type { Scope } from '@/api/types'
import { DASH, fmt, isNum } from '@/lib/format'
import { multiplier, STAT } from '@/screens/data/state'
import { Notice, Panel } from '@/ui/kit'
import { type Column, DataTable, type Sort } from '@/ui/table'
import { type ForgeDataset, forgeData } from './api'
import { ColumnHint, CROWDING_HINT, Crowding, MINE_HINT } from './extras'

/** Sort keys and how each reads a row; nested "mine" figures included. */
const VALUE: Record<string, (d: ForgeDataset) => number | string | null | undefined> = {
  dataset_id: (d) => d.datasetId,
  category: (d) => d.categoryName,
  multiplier: (d) => d.accountMultiplier ?? d.pyramidMultiplier,
  crowding: (d) => d.crowding,
  coverage: (d) => d.coverage,
  fields: (d) => d.fieldCount,
  value: (d) => d.valueScore,
  my_alphas: (d) => d.mine.alphas,
  my_sims: (d) => d.mine.sims,
  my_pass: (d) => d.mine.passRate,
}

function sorted(rows: ForgeDataset[], sort: Sort): ForgeDataset[] {
  const read = VALUE[sort.key] ?? VALUE['crowding']
  if (!read) return rows
  return [...rows].sort((a, b) => {
    const x = read(a)
    const y = read(b)
    // Absent values last, whichever the direction.
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1
    const order =
      typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
    return sort.desc ? -order : order
  })
}

/** A header that explains itself; see {@link ColumnHint} for why it takes the text as a prop. */
const head = (text: string, hint: string) => <ColumnHint text={text} hint={hint} />

const COLUMNS: Column<ForgeDataset>[] = [
  {
    key: 'dataset_id',
    header: 'Dataset',
    width: 'minmax(120px,1.4fr)',
    sortable: true,
    cell: (d) => (
      <span className="flex min-w-0 flex-col">
        <span className="num truncate text-ink">{d.datasetId}</span>
        <span className="truncate text-body-compact text-ink-subtle" title={d.name ?? undefined}>
          {d.name ?? DASH}
        </span>
      </span>
    ),
  },
  {
    key: 'category',
    header: 'Category',
    width: 'minmax(96px,1fr)',
    sortable: true,
    cell: (d) => <span className="truncate text-ink-muted">{d.categoryName ?? DASH}</span>,
  },
  {
    key: 'multiplier',
    header: head(
      'Pyramid ×',
      "What BRAIN multiplies your pay by for alphas in this category, region and delay. Your account's own multiplier when BRAIN could be asked, else the one listed on the dataset. ● means the pyramid already has its 3 alphas this quarter.",
    ),
    width: 'minmax(84px,0.6fr)',
    align: 'right',
    sortable: true,
    cell: (d) => (
      <span className="num">
        {d.pyramidLit && <span className="mr-1 text-pnl-positive-text">●</span>}
        {multiplier(d.accountMultiplier ?? d.pyramidMultiplier)}
      </span>
    ),
  },
  {
    key: 'crowding',
    header: head('Crowding', CROWDING_HINT),
    width: 'minmax(84px,0.6fr)',
    align: 'right',
    sortable: true,
    cell: (d) => <Crowding value={d.crowding} />,
  },
  {
    key: 'coverage',
    header: head(
      'Coverage',
      "BRAIN's coverage for the dataset: the share of the universe it has values for.",
    ),
    width: 'minmax(76px,0.5fr)',
    align: 'right',
    sortable: true,
    cell: (d) => fmt.pct(d.coverage, 0),
  },
  {
    key: 'fields',
    header: 'Fields',
    width: 'minmax(60px,0.4fr)',
    align: 'right',
    sortable: true,
    cell: (d) => fmt.int(d.fieldCount),
  },
  {
    key: 'value',
    header: head(
      'Value',
      "BRAIN's value score for the dataset: its own estimate of how useful the data is. Higher is better.",
    ),
    width: 'minmax(60px,0.4fr)',
    align: 'right',
    sortable: true,
    cell: (d) => (isNum(d.valueScore) ? fmt.ratio(d.valueScore, 1) : DASH),
  },
  {
    key: 'my_sims',
    header: head(
      'My sims',
      'Simulations sent to BRAIN from here, in this region and delay, whose expression reads this dataset.',
    ),
    width: 'minmax(68px,0.4fr)',
    align: 'right',
    sortable: true,
    cell: (d) => fmt.int(d.mine.sims || null),
  },
  {
    key: 'my_alphas',
    header: head('My alphas', MINE_HINT),
    width: 'minmax(72px,0.4fr)',
    align: 'right',
    sortable: true,
    cell: (d) => fmt.int(d.mine.alphas || null),
  },
  {
    key: 'my_pass',
    header: head(
      'Pass rate',
      'Of your alphas here that BRAIN has decided, the share that passed every check.',
    ),
    width: 'minmax(80px,0.5fr)',
    align: 'right',
    sortable: true,
    cell: (d) =>
      d.mine.checked ? (
        <span className="num" title={`${d.mine.passed} of ${d.mine.checked} passed`}>
          {fmt.pct(d.mine.passRate, 0)}
        </span>
      ) : (
        DASH
      ),
  },
]

export function DatasetsTab({ scope }: { scope: Scope }) {
  const navigate = useNavigate()
  const [sort, setSort] = useState<Sort>({ key: 'crowding', desc: false })
  const query = useQuery({
    queryKey: ['forge-datasets', scope],
    queryFn: () => forgeData.datasets(scope),
  })
  const rows = useMemo(() => sorted(query.data?.datasets ?? [], sort), [query.data, sort])

  return (
    <Panel
      title="Datasets"
      description="What this tells you: where the data is uncrowded and well paid, and where you have already worked. Sorted least crowded first; a row opens the dataset."
      actions={
        query.data && (
          <span className={STAT}>
            <span className="num text-ink">{fmt.int(rows.length)}</span>
            datasets
          </span>
        )
      }
    >
      <div className="flex flex-col gap-3">
        {query.data && !query.data.pyramidsLive && (
          <Notice tone="info" title="Account multipliers unavailable">
            BRAIN could not be asked for your pyramid multipliers (signed out, or rate limited), so
            Pyramid × shows the multiplier listed on each dataset.
          </Notice>
        )}
        {query.data && query.data.unparsed > 0 && (
          <p className="text-body-compact text-ink-subtle">
            {fmt.int(query.data.unparsed)} of your expressions could not be read, so they are left
            out of the "my" columns.
          </p>
        )}
        <DataTable
          label="Datasets"
          rows={rows}
          columns={COLUMNS}
          rowKey={(d) => d.datasetId}
          sort={sort}
          onSort={setSort}
          rowHeight={40}
          onRowClick={(d) =>
            void navigate({
              to: '/forge/datasets/$datasetId',
              params: { datasetId: d.datasetId },
              search: { ...scope },
            })
          }
          loading={query.isPending}
          error={query.error}
          empty="No dataset details for this market yet. Download it in BRAIN › Sync: fields come first, then each dataset's details."
        />
      </div>
    </Panel>
  )
}
