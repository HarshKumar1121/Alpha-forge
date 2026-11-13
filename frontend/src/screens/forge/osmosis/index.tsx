/**
 * The Osmosis Allocator. For each region and delay: the points to type into BRAIN, the N and
 * method behind them, and what was left out. Every submitted alpha with stored PnL is a
 * candidate; BRAIN's Osmosis check is shown beside each one but never gates it. Combination
 * statistics only: BRAIN's multiplier formula is not known to this tool, and nothing here
 * submits or calls BRAIN.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { fmt } from '@/lib/format'
import type { Choice } from '@/lib/scope'
import { Badge, Button, ErrorNotice, Input, Notice, Page, PageHeader, Skeleton } from '@/ui/kit'
import { Select, Tooltip } from '@/ui/overlay'
import { type OsmosisProgress, type OsmosisScopeView, type OsmosisSummary, osmosis } from './api'
import { allocationCsv, METHOD_LABEL, ScopeBody, Tip } from './scope'
import { SummaryPanel } from './summary'

type ScopeKey = { region: string; delay: number }

/** Tolerant reads: a missing number shows as the dash it is, never as a made-up figure. */
const clampN = (value: number, min: number, max: number) =>
  Math.min(Math.max(Math.round(value), min), max)

export function OsmosisScreen() {
  const summary = useQuery({
    queryKey: ['forge', 'osmosis', 'summary'],
    queryFn: () => osmosis.summary(),
  })
  // forge: while the summary calculates (a cold load), poll its progress so the screen can say
  // which scope is being worked on. Stops as soon as the summary has loaded.
  const progress = useQuery({
    queryKey: ['forge', 'osmosis', 'progress'],
    queryFn: () => osmosis.progress(),
    enabled: summary.isPending,
    refetchInterval: summary.isPending ? 500 : false,
  })
  const [picked, setPicked] = useState<ScopeKey | null>(null)
  const [nPick, setNPick] = useState<number | null>(null)
  const [methodPick, setMethodPick] = useState<string | null>(null)

  const current = picked ?? defaultScope(summary.data)

  const scope = useQuery({
    queryKey: ['forge', 'osmosis', 'scope', current?.region, current?.delay, nPick, methodPick],
    queryFn: () => {
      if (!current) throw new Error('No scope selected.')
      return osmosis.scope(current.region, current.delay, nPick, methodPick)
    },
    enabled: current !== null,
  })

  const choose = (next: ScopeKey) => {
    setPicked(next)
    setNPick(null)
    setMethodPick(null)
  }

  if (summary.isPending) {
    return (
      <Page>
        <CalculatingNotice progress={progress.data} />
        <Skeleton className="h-64" label="Calculating scopes" />
      </Page>
    )
  }
  if (summary.error) {
    return (
      <Page>
        <ErrorNotice error={summary.error} title="The Osmosis summary did not load" />
      </Page>
    )
  }
  const rows = summary.data.scopes
  if (rows.length === 0) {
    return (
      <Page>
        <PageHeader title="Osmosis Allocator" />
        <Notice tone="info" title="No scopes yet">
          Submit alphas on BRAIN, then sync them. Each alpha with stored PnL becomes a candidate in
          its region and delay.
        </Notice>
      </Page>
    )
  }

  return (
    <Page>
      <PageHeader
        title="Osmosis Allocator"
        description={
          <>
            Points to type into BRAIN, per region and delay. Combination statistics only: BRAIN's
            multiplier formula is not known to this tool; enter points on BRAIN yourself.
          </>
        }
      />

      <SummaryPanel
        summary={summary.data}
        selected={current}
        onSelect={(region, delay) => choose({ region, delay })}
      />

      <Controls
        summary={summary.data}
        current={current}
        view={scope.data}
        nPick={nPick}
        onRegion={(region) => choose(bestDelay(summary.data, region))}
        onDelay={(delay) => choose({ region: current?.region ?? '', delay })}
        onN={setNPick}
        onMethod={setMethodPick}
      />

      {scope.isPending ? (
        <Skeleton className="h-96" label="Calculating this scope" />
      ) : scope.error ? (
        <ErrorNotice error={scope.error} title="This scope did not calculate" />
      ) : (
        <>
          {scope.data.banner && (
            <Notice tone="warn" title="Not validated">
              {scope.data.banner}
            </Notice>
          )}
          <ScopeBody view={scope.data} />
        </>
      )}
    </Page>
  )
}

/** The cold-load message: which scope is being calculated, out of how many. Warm loads skip it. */
function CalculatingNotice({ progress }: { progress: OsmosisProgress | undefined }) {
  const total = progress?.total ?? 0
  const position = total > 0 ? Math.min((progress?.done ?? 0) + 1, total) : 0
  return (
    <Notice tone="info" title="Calculating the Osmosis allocation">
      {total > 0 && progress?.current
        ? `Scope ${position} of ${total} (${progress.current}). `
        : 'Starting the calculation. '}
      The first load takes about 20 to 25 seconds across all scopes; later loads of the same data
      come from the cache.
    </Notice>
  )
}

function defaultScope(data: OsmosisSummary | undefined): ScopeKey | null {
  if (!data || data.scopes.length === 0) return null
  const largest = data.scopes.reduce((best, r) => (r.alphaCount > best.alphaCount ? r : best))
  return { region: largest.region, delay: largest.delay }
}

/** The delay with the most alphas in a region, so changing region opens its biggest scope. */
function bestDelay(data: OsmosisSummary, region: string): ScopeKey {
  const inRegion = data.scopes.filter((r) => r.region === region)
  const best = inRegion.reduce((a, r) => (r.alphaCount > a.alphaCount ? r : a))
  return { region, delay: best.delay }
}

function Controls({
  summary,
  current,
  view,
  nPick,
  onRegion,
  onDelay,
  onN,
  onMethod,
}: {
  summary: OsmosisSummary
  current: ScopeKey | null
  view: OsmosisScopeView | undefined
  nPick: number | null
  onRegion: (region: string) => void
  onDelay: (delay: number) => void
  onN: (n: number | null) => void
  onMethod: (method: string | null) => void
}) {
  const regionCounts = new Map<string, number>()
  for (const r of summary.scopes) {
    regionCounts.set(r.region, (regionCounts.get(r.region) ?? 0) + r.alphaCount)
  }
  const regions: Choice[] = [...regionCounts]
    .sort((a, b) => b[1] - a[1])
    .map(([region, count]) => ({ value: region, label: `${region} · ${count}` }))
  const delays: Choice[] = summary.scopes
    .filter((r) => r.region === current?.region)
    .map((r) => ({ value: String(r.delay), label: `D${r.delay} · ${r.alphaCount}` }))
  const methods: Choice[] = Object.entries(METHOD_LABEL).map(([value, label]) => ({
    value,
    label: value === view?.defaultMethod ? `${label} (default)` : label,
  }))

  const [draft, setDraft] = useState('')
  useEffect(() => {
    if (view) setDraft(String(view.n))
  }, [view])
  const shown = view && view.blocked === null ? view : null

  const commit = () => {
    if (!shown) return
    const value = Number(draft)
    if (!Number.isFinite(value)) {
      setDraft(String(shown.n))
      return
    }
    const n = clampN(value, shown.nMin, shown.nMax)
    setDraft(String(n))
    onN(n === shown.recommendedN ? null : n)
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3 rounded-md border border-hairline bg-surface-1 p-3">
        <label className="flex flex-col gap-1 text-xs text-ink-subtle">
          <Tip text="Region. The count is the submitted alphas with stored PnL across its delays.">
            Region
          </Tip>
          <Select
            label="Region"
            value={current?.region ?? null}
            onChange={onRegion}
            items={regions}
            mono
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-subtle">
          <Tip text="Delay. Each delay is its own scope with its own 100,000 points.">Delay</Tip>
          <Select
            label="Delay"
            value={current ? String(current.delay) : null}
            onChange={(v) => onDelay(Number(v))}
            items={delays}
            mono
          />
        </label>

        {shown ? (
          <div className="flex flex-col gap-1 text-xs text-ink-subtle">
            <span className="flex items-center gap-2">
              <Tip text="Number of alphas in the allocation. Range: the cap's minimum to the ranked alphas. Unverified rule: Osmosis reportedly needs at least 10 alphas per scope.">
                Number of alphas
              </Tip>
              <Input
                aria-label="Number of alphas"
                type="number"
                min={shown.nMin}
                max={shown.nMax}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => e.key === 'Enter' && commit()}
                className="h-8 w-20 num"
              />
            </span>
            <span className="flex items-center gap-2">
              <Tooltip
                content={`The N where adding alphas stops improving combined Sharpe by more than 0.05 (a rule of thumb). Never below 10. Unverified rule.`}
              >
                <span className="cursor-help num text-ink">
                  Recommended: {fmt.int(shown.recommendedN)}
                </span>
              </Tooltip>
              <Button
                size="sm"
                variant="secondary"
                disabled={nPick === null}
                onClick={() => onN(null)}
              >
                Use
              </Button>
            </span>
          </div>
        ) : null}

        <label className="flex flex-col gap-1 text-xs text-ink-subtle">
          <Tip text="The weighting method. The default is the walk-forward winner when it beat equal weight on holdout; otherwise HRP, which uses no expected returns.">
            Weighting method
          </Tip>
          <Select
            label="Weighting method"
            value={view?.requestedMethod ?? null}
            onChange={onMethod}
            items={methods}
          />
        </label>

        {shown && (
          <Badge tone={shown.validated ? 'profit' : 'muted'}>
            {shown.validated ? 'Walk-forward validated' : 'Not validated'}
          </Badge>
        )}

        <div className="ml-auto">
          <CsvButton view={shown} />
        </div>
      </div>
    </div>
  )
}

function CsvButton({ view }: { view: OsmosisScopeView | null }) {
  const enabled = view !== null && view.rows.length > 0
  return (
    <Tooltip content="Downloads exactly the allocation table on this screen, with its totals row.">
      <Button
        size="sm"
        variant="secondary"
        disabled={!enabled}
        onClick={() => {
          if (!view) return
          const url = URL.createObjectURL(new Blob([allocationCsv(view)], { type: 'text/csv' }))
          const link = document.createElement('a')
          link.href = url
          link.download = `osmosis-${view.region}-D${view.delay}-n${view.n}.csv`
          link.click()
          URL.revokeObjectURL(url)
        }}
      >
        Export CSV
      </Button>
    </Tooltip>
  )
}
