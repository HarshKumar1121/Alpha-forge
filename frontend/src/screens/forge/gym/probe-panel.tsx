/**
 * "Probe this field's behaviour": pick any real field from the catalog — not just the Gym's
 * fixed set — and run the 3 cheap behaviour probes on it through BRAIN (the Quota Governor's
 * `explore` lane). Unlike the rest of the Gym screen, this spends real quota: a small,
 * worthwhile one, before spending real quota building templates on a field you know nothing
 * about yet.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { catalog } from '@/api/catalog'
import { useDebounced } from '@/lib/use-debounced'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import { Badge, Button, ErrorNotice, Field, Input, Panel } from '@/ui/kit'
import { ANGLE_EXPLAIN, ARCHETYPE_EXPLAIN } from './explain'
import { forgeProbe, type ProbeResult } from './probe-api'

/** A tone per archetype, reusing the kit's P&L-neutral tones rather than inventing new colors. */
const ARCHETYPE_TONE = {
  slow: 'muted',
  medium: 'neutral',
  persistent: 'profit',
  fast: 'warn',
} as const

export function ProbeFieldPanel() {
  const [scope, setScope] = useForgeScope()
  const [search, setSearch] = useState('')
  const [fieldId, setFieldId] = useState<string | null>(null)
  const term = useDebounced(search.trim(), 250)

  const query = useQuery({
    queryKey: ['catalog', 'fields', 'probe', scope, term],
    queryFn: ({ signal }) => catalog.fields(scope, { search: term, limit: 8 }, signal),
    enabled: term.length > 0,
  })

  const probe = useMutation({
    mutationFn: (id: string) => forgeProbe.field(scope, id),
    // Shown in place, under the picker, rather than as a passing toast.
    meta: { inline: true },
  })

  const run = (id: string) => {
    setFieldId(id)
    probe.mutate(id)
  }

  return (
    <Panel
      title="Probe a field's behaviour"
      description="Pick any real field in this market and run the 3 cheap probes on it through BRAIN — worth the small spend (the explore lane) before you build templates on a field whose behaviour you don't know yet."
    >
      <div className="flex flex-col gap-4">
        <ForgeScopePicker scope={scope} onChange={setScope} />
        <Field
          label="Field"
          hint="Any field in this market's catalog — not just the Gym's fixed 20."
        >
          <Input
            className="w-full sm:w-80"
            placeholder="Search Field ID or Description"
            aria-label="Search field catalog"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </Field>
        {term && (
          <div className="flex flex-wrap items-center gap-2">
            {(query.data?.results ?? []).map((f) => (
              <Button
                key={f.field_id}
                size="sm"
                variant={fieldId === f.field_id ? 'primary' : 'secondary'}
                title={f.description ?? undefined}
                onClick={() => run(f.field_id)}
                loading={probe.isPending && fieldId === f.field_id}
                disabled={probe.isPending}
              >
                <span className="num">{f.field_id}</span>
              </Button>
            ))}
            {query.isPending && (
              <span className="text-body-compact text-ink-subtle">Searching…</span>
            )}
            {!query.isPending && (query.data?.results ?? []).length === 0 && (
              <span className="text-body-compact text-ink-subtle">No fields match.</span>
            )}
          </div>
        )}
        {probe.isError && <ErrorNotice error={probe.error} title="Could not probe this field" />}
        {probe.data && <ProbeOutcome result={probe.data} />}
      </div>
    </Panel>
  )
}

function ProbeOutcome({ result }: { result: ProbeResult }) {
  const tone = ARCHETYPE_TONE[result.archetype as keyof typeof ARCHETYPE_TONE] ?? 'neutral'
  const archetypeWhy = ARCHETYPE_EXPLAIN[result.archetype] ?? ''
  return (
    <div className="flex flex-col gap-3 rounded-sm border border-hairline p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-ink">{result.fieldId}</span>
        <Badge tone={tone} title={archetypeWhy}>
          {result.archetype}
        </Badge>
        <span className="text-body-compact text-ink-subtle">
          Best angle:{' '}
          <span className="num text-ink" title={ANGLE_EXPLAIN[result.bestAngle]}>
            {result.bestAngle}
          </span>{' '}
          ({result.sign > 0 ? 'use as is' : 'flip the sign'})
        </span>
      </div>
      <p className="text-pretty text-body-compact text-ink-subtle">{archetypeWhy}</p>
      <ul className="flex flex-col gap-1">
        {result.notes.map((n) => (
          <li key={n} className="text-body-compact text-ink-subtle">
            {n}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-4">
        {result.angles.map((a) => (
          <div key={a.name} className="flex flex-col gap-0.5" title={ANGLE_EXPLAIN[a.name]}>
            <span className="text-body-compact text-ink-subtle">{a.name}</span>
            <span className="num text-ink">
              {a.error ? 'failed' : a.trainSharpe != null ? a.trainSharpe.toFixed(2) : '—'}
            </span>
          </div>
        ))}
      </div>
      <p className="text-body-compact text-ink-subtle">
        Spent {result.admitted} of {result.asked} probes from the {result.lane} lane.
      </p>
    </div>
  )
}
