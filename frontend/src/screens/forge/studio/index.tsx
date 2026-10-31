/**
 * Template Studio: put a template on a field set and see the actual alphas before any of them
 * costs a simulation.
 *
 * The question this screen answers is the one the Template Library cannot: not "could this
 * template be a Power Pool alpha?" but "is *this* alpha, on *these* fields, in *this* market,
 * one BRAIN would take — and if not, what exactly is wrong with it?"
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { DASH, fmt } from '@/lib/format'
import { fieldSets } from '@/screens/forge/data/api'
import { forgeTemplates } from '@/screens/forge/templates/api'
import {
  Badge,
  Empty,
  ErrorNotice,
  Field,
  KV,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { Select, Sheet } from '@/ui/overlay'
import { type RenderedAlpha, studio } from './api'
import { DescriptionPanel } from './description'

export function TemplateStudioScreen() {
  const search = useSearch({ from: '/forge/studio' })
  const navigate = useNavigate()
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  const sets = useQuery({ queryKey: ['forge-fieldsets'], queryFn: fieldSets.list })
  const templates = useQuery({
    queryKey: ['forge-templates', 'list'],
    queryFn: forgeTemplates.list,
    staleTime: 5 * 60 * 1000,
  })

  const templateId = search.t ?? templates.data?.templates[0]?.id
  const fieldSetId = search.fs ?? sets.data?.[0]?.id
  const set = (templateId ?? '') && (fieldSetId ?? 0) ? { templateId, fieldSetId } : null

  const preview = useQuery({
    queryKey: ['forge-studio', 'preview', templateId, fieldSetId],
    queryFn: () =>
      studio.preview({
        templateId: templateId as string,
        fieldSetId: fieldSetId as number,
        limit: 50,
      }),
    enabled: Boolean(set),
  })
  const p = preview.data

  const choose = (change: { t?: string; fs?: number }) => {
    setOpenIndex(null)
    void navigate({ to: '/forge/studio', search: { ...search, ...change } })
  }

  return (
    <Page>
      <PageHeader
        title="Template Studio"
        description="Bind a template to a field set to see the real alphas it renders, which alpha types each one qualifies for, and why the others are out. Nothing here is simulated."
      />

      <Panel
        title="What to render"
        description="What this tells you: which idea, on which fields, in which market. The field set carries its own scope — a field id means different data elsewhere."
        bodyClassName="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3"
      >
        <Field label="Template" hint="Built-ins and the templates you wrote.">
          <Select
            label="Template"
            items={(templates.data?.templates ?? []).map((t) => ({
              value: t.id,
              label: `${t.name}${t.source === 'user' ? ' (yours)' : ''}`,
            }))}
            value={templateId ?? null}
            onChange={(value) => choose({ t: value })}
          />
        </Field>
        <Field label="Field set" hint="Its market decides which fields and groups exist.">
          <Select
            label="Field set"
            items={(sets.data ?? []).map((s) => ({
              value: String(s.id),
              label: `${s.name} — ${s.scope.region} D${s.scope.delay} ${s.scope.universe}`,
            }))}
            value={fieldSetId == null ? null : String(fieldSetId)}
            onChange={(value) => choose({ fs: Number(value) })}
          />
        </Field>
        {p && (
          <div className="flex flex-wrap items-end gap-4">
            <Metric
              label="Alphas rendered"
              value={fmt.int(p.total)}
              hint={
                p.rendered.length < p.total
                  ? `Showing the first ${fmt.int(p.rendered.length)}`
                  : 'Every combination of field, window and group'
              }
            />
            <Metric label="Scope" value={<span className="num text-body">{p.scope}</span>} />
          </div>
        )}
      </Panel>

      {sets.data?.length === 0 && (
        <Panel title="No field sets yet">
          <Empty title="A template needs real fields to bind to">
            Make one in the Data Explorer: narrow a market to the fields behind an idea, tick them,
            and choose "Save as field set".
          </Empty>
        </Panel>
      )}

      {preview.isError && <ErrorNotice error={preview.error} title="Could not render this" />}
      {preview.isPending && set && <Skeleton className="h-64" label="Rendering alphas" />}

      {p && p.blockers.length > 0 && (
        <Notice tone="warn" title="This template cannot run on this field set">
          <ul className="ml-4 list-disc">
            {p.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </Notice>
      )}
      {p?.missingFields.length ? (
        <Notice tone="info" title="Some of the set's fields are not in this market">
          <span className="num">{p.missingFields.join(', ')}</span> — they are skipped.
        </Notice>
      ) : null}

      {p && p.rendered.length > 0 && (
        <>
          <FitSummary counts={p.fitCounts} total={p.rendered.length} notes={p.notes} />
          <Panel
            title="Rendered alphas"
            description="What this tells you: every alpha this template makes from the set, and what BRAIN's rules say about each. Open one to see why a type is out, and to write its description."
            bodyClassName="flex flex-col divide-y divide-hairline p-0"
          >
            {p.rendered.map((alpha, index) => (
              <AlphaRow key={alpha.expression} alpha={alpha} onOpen={() => setOpenIndex(index)} />
            ))}
          </Panel>
        </>
      )}

      <AlphaSheet
        alpha={openIndex == null ? null : (p?.rendered[openIndex] ?? null)}
        templateId={templateId}
        fieldSetId={fieldSetId}
        scope={p?.scope ?? ''}
        onClose={() => setOpenIndex(null)}
      />
    </Page>
  )
}

function FitSummary({
  counts,
  total,
  notes,
}: {
  counts: Record<string, number>
  total: number
  notes: string[]
}) {
  return (
    <Panel
      title="What these alphas qualify for"
      description="What this tells you: how many of the rendered alphas each alpha type would accept, judged by that type's own rules. BRAIN's own check is still the last word."
      bodyClassName="flex flex-col gap-3 p-4"
    >
      <div className="flex flex-wrap gap-4">
        {Object.entries(counts).map(([type, n]) => (
          <Metric
            key={type}
            boxed
            size="sm"
            label={type.replaceAll('_', ' ')}
            value={`${fmt.int(n)} / ${fmt.int(total)}`}
            tone={n ? 'profit' : 'neutral'}
            hint={n ? 'eligible here' : 'none eligible'}
          />
        ))}
      </div>
      {notes.map((note) => (
        <p key={note} className="text-body-compact text-ink-subtle">
          {note}
        </p>
      ))}
    </Panel>
  )
}

function AlphaRow({ alpha, onOpen }: { alpha: RenderedAlpha; onOpen: () => void }) {
  const missed = Object.values(alpha.eligibility).filter((e) => !e.ok)
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors hover:bg-surface-2"
    >
      <code className="num block text-body-compact break-all text-ink">{alpha.expression}</code>
      <span className="flex flex-wrap items-center gap-2">
        {alpha.fits.length > 0 ? (
          alpha.fits.map((type) => (
            <Badge key={type} tone="profit">
              {type.replaceAll('_', ' ')}
            </Badge>
          ))
        ) : (
          <Badge tone="muted">no type fits</Badge>
        )}
        <span className="text-body-compact text-ink-subtle">
          {fmt.int(alpha.operatorCount)} operators · {fmt.int(alpha.dataFields.length)} fields ·{' '}
          {alpha.datasets.join(', ') || DASH}
        </span>
        {missed.length > 0 && (
          <span className="text-body-compact text-ink-subtle">
            {fmt.int(missed.length)} type{missed.length === 1 ? '' : 's'} out
          </span>
        )}
      </span>
    </button>
  )
}

function AlphaSheet({
  alpha,
  templateId,
  fieldSetId,
  scope,
  onClose,
}: {
  alpha: RenderedAlpha | null
  templateId: string | undefined
  fieldSetId: number | undefined
  scope: string
  onClose: () => void
}) {
  return (
    <Sheet
      open={alpha != null}
      onOpenChange={(open) => !open && onClose()}
      title="This alpha"
      description={alpha ? `${alpha.operatorCount} operators` : ''}
    >
      {alpha && (
        <div className="flex flex-col gap-4">
          <code className="num block rounded-md bg-canvas p-3 text-body-compact break-all text-ink">
            {alpha.expression}
          </code>
          <KV
            items={[
              [
                'Slots',
                Object.entries({ ...alpha.fields, ...alpha.params })
                  .map(([slot, value]) => `$${slot} = ${value}`)
                  .join(', ') || DASH,
              ],
              ['Data fields', alpha.dataFields.join(', ') || DASH],
              ['Datasets', alpha.datasets.join(', ') || DASH],
            ]}
          />

          <section className="flex flex-col gap-2">
            <h3 className="text-title">Alpha types</h3>
            {Object.entries(alpha.eligibility).map(([type, verdict]) => (
              <div key={type} className="rounded-md border border-hairline p-3">
                <div className="flex items-center gap-2">
                  <Badge tone={verdict.ok ? 'profit' : 'muted'}>
                    {verdict.ok ? 'Eligible' : 'Not eligible'}
                  </Badge>
                  <span className="text-body font-medium text-ink">
                    {type.replaceAll('_', ' ')}
                  </span>
                </div>
                {verdict.reasons.length > 0 && (
                  <ul className="mt-2 ml-4 list-disc text-body-compact text-ink-muted">
                    {verdict.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                )}
                {verdict.warnings.map((w) => (
                  <p key={w} className="mt-2 text-body-compact text-ink-subtle">
                    {w}
                  </p>
                ))}
              </div>
            ))}
          </section>

          {alpha.eligibility['power_pool']?.ok && (
            <DescriptionPanel
              expression={alpha.expression}
              templateId={templateId}
              fieldSetId={fieldSetId}
              scope={scope}
            />
          )}
        </div>
      )}
    </Sheet>
  )
}
