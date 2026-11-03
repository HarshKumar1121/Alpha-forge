/**
 * Forge Analytics: pick a BRAIN alpha id and read everything a desk reads for it — the real
 * tear sheet from its own recorded history, and how much of it a handful of known factors
 * explain. Read-only: a GET against recordsets BRAIN already has, never a simulation
 * (CLAUDE.md rule 3 is about simulations).
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { ActivityIcon, SearchIcon } from 'lucide-react'
import { type KeyboardEvent, useState } from 'react'
import { ApiError } from '@/api/http'
import {
  Button,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { forgeAnalytics } from './api'
import { Explain } from './explain'
import { TearSheet } from './tear-sheet'

export function AnalyticsScreen() {
  const { alpha } = useSearch({ from: '/forge/analytics' })
  const navigate = useNavigate()
  const [input, setInput] = useState(alpha ?? '')

  const query = useQuery({
    queryKey: ['forge-analytics-tear-sheet', alpha],
    queryFn: () => forgeAnalytics.tearSheet(alpha as string),
    enabled: !!alpha,
  })

  const go = () => {
    const id = input.trim()
    if (id) void navigate({ to: '/forge/analytics', search: { alpha: id } })
  }
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter') go()
  }

  return (
    <Page>
      <PageHeader
        title="Analytics"
        description="Everything a desk reads for one alpha, built from its own recorded history on BRAIN — the tear sheet, how confident the Sharpe really is, and what known factors explain of it."
      />

      <Panel title="Alpha">
        <div className="flex max-w-md items-end gap-2">
          <Field
            label="Alpha id"
            hint="From the Submittable Board, the Alpha page, or BRAIN's own URL."
          >
            <Input
              className="num"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="e.g. 1a2b3c4"
            />
          </Field>
          <Button variant="primary" onClick={go} disabled={!input.trim()}>
            <SearchIcon className="size-3.5" aria-hidden />
            Load
          </Button>
        </div>
      </Panel>

      {!alpha && (
        <Panel>
          <Empty icon={<ActivityIcon />} title="Enter an alpha id to see its analytics">
            Open this from the Submittable Board's "Analytics" link to skip typing the id.
          </Empty>
        </Panel>
      )}

      {alpha && query.isError && (
        <ErrorNotice
          error={query.error}
          title={
            query.error instanceof ApiError && query.error.code === 'no_pnl'
              ? 'BRAIN has no recorded PnL for this alpha yet'
              : "Could not load this alpha's tear sheet"
          }
        />
      )}
      {alpha && !query.data && !query.isError && (
        <Panel>
          <Skeleton className="h-64" label="Loading the tear sheet" />
        </Panel>
      )}
      {alpha && query.data && (
        <>
          <TearSheet sheet={query.data} />
          <Explain alphaId={alpha} />
        </>
      )}
    </Page>
  )
}
