/**
 * Workbench: a market, the fields you're working with in it, the templates and hand-written
 * expressions built from them, and the runs made from those candidates — one screen instead of
 * switching between Data Explorer, Template Studio and Pipelines for a single idea.
 *
 * A run goes through the same orchestrator and Quota Governor every other forge screen that
 * spends quota uses, tagged `forge.reserve` so it shows up in Tasks and the Simulation Matrix
 * exactly like a Pipelines run — nothing here submits anything (CLAUDE.md rule 1).
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { catalog } from '@/api/catalog'
import { type Scope, scopeLabel } from '@/api/types'
import { DASH, fmt } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { PreflightAdvisor } from '@/screens/forge/advisor/panel'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import { type PreviewResponse, type RenderedAlpha, studio } from '@/screens/forge/studio/api'
import { forgeTemplates, type ScopeTemplate } from '@/screens/forge/templates/api'
import { templateLab } from '@/screens/research-labs/template/api'
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Segmented,
  Skeleton,
} from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { type RunItemOut, workbench } from './api'
import { ExpressionEditor } from './expression-editor'

/** A candidate expression picked for a run, whichever source it came from. */
export interface Candidate {
  expression: string
  source: 'template' | 'manual'
  templateId: string | null
  fieldIds: string[]
}

export function WorkbenchScreen() {
  const [scope, setScope] = useForgeScope()
  const client = useQueryClient()
  const [fieldIds, setFieldIds] = useState<string[]>([])
  const [saveError, setSaveError] = useState<unknown>(null)
  // The scope the tray in `fieldIds` was loaded for, so a scope change loads fresh instead of
  // saving the previous scope's fields under the new one.
  const loadedFor = useRef<string | null>(null)

  const tray = useQuery({
    queryKey: ['forge-workbench-tray', scope],
    queryFn: () => workbench.getTray(scope),
  })

  // biome-ignore lint/correctness/useExhaustiveDependencies: load once per scope, not per render
  useEffect(() => {
    if (!tray.data) return
    loadedFor.current = scopeLabel(scope)
    setFieldIds(tray.data.fieldIds)
  }, [tray.data, scope.region, scope.delay, scope.universe, scope.instrumentType])

  const debounced = useDebounced(fieldIds, 400)
  // biome-ignore lint/correctness/useExhaustiveDependencies: save only when the debounced tray settles
  useEffect(() => {
    if (loadedFor.current !== scopeLabel(scope)) return
    workbench
      .setTray(scope, debounced)
      .then(() => {
        setSaveError(null)
        void client.invalidateQueries({ queryKey: ['forge-workbench-tray', scope] })
      })
      .catch(setSaveError)
  }, [debounced])

  const [candidates, setCandidates] = useState<Candidate[]>([])
  const addCandidate = (c: Omit<Candidate, 'source'> & { source: Candidate['source'] }) =>
    setCandidates((prev) => (prev.some((p) => p.expression === c.expression) ? prev : [...prev, c]))
  const removeCandidate = (expression: string) =>
    setCandidates((prev) => prev.filter((c) => c.expression !== expression))

  const [taskId, setTaskId] = useState<string | null>(null)
  const [runError, setRunError] = useState<unknown>(null)
  const [running, setRunning] = useState(false)
  const run = useQuery({
    queryKey: ['forge-workbench-run', taskId],
    queryFn: () => workbench.getRun(taskId as string),
    enabled: taskId != null,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })

  const startRun = async () => {
    setRunError(null)
    setRunning(true)
    try {
      const started = await workbench.run(
        scope,
        candidates.map((c) => ({
          expression: c.expression,
          templateId: c.templateId,
          fieldIds: c.fieldIds,
        })),
      )
      setTaskId(started.taskId)
    } catch (e) {
      setRunError(e)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Page>
      <PageHeader
        title="Workbench"
        description="Pick a market, search and tick the fields you want to work with, bind a template or write an expression by hand, and run the ones worth it."
      />
      <Panel
        title="Market"
        description="What this tells you: which instrument type, region, delay and universe everything below is tied to. A field id means different data in another market."
      >
        <ForgeScopePicker scope={scope} onChange={setScope} />
      </Panel>
      <Panel
        title="Field tray"
        description={`What this tells you: the fields picked so far in ${scopeLabel(scope)}, from an existing field set or a live search of the catalog.`}
        actions={<Badge tone="outline">{fieldIds.length} in tray</Badge>}
      >
        {Boolean(saveError) && <ErrorNotice error={saveError} title="Could not save the tray" />}
        {tray.isError && <ErrorNotice error={tray.error} title="Could not load the tray" />}
        {tray.isPending ? (
          <Skeleton className="h-40" label="Loading the tray" />
        ) : (
          <FieldPicker scope={scope} selected={fieldIds} onChange={(ids) => setFieldIds(ids)} />
        )}
      </Panel>
      <ExpressionPanel
        fieldIds={fieldIds}
        onAdd={(expression) =>
          addCandidate({ expression, source: 'manual', templateId: null, fieldIds })
        }
      />
      <TemplateLab
        scope={scope}
        onAdd={(expression, templateId, alphaFieldIds) =>
          addCandidate({ expression, source: 'template', templateId, fieldIds: alphaFieldIds })
        }
      />
      {candidates.length > 0 && (
        <Panel
          title="Candidates"
          description="What this tells you: the expressions queued so far, from a template or written by hand."
          actions={
            <Button variant="primary" size="sm" loading={running} onClick={() => void startRun()}>
              Run {candidates.length} candidate{candidates.length === 1 ? '' : 's'}
            </Button>
          }
          bodyClassName="flex flex-col divide-y divide-hairline p-0"
        >
          {Boolean(runError) && <ErrorNotice error={runError} title="Could not start the run" />}
          {candidates.map((c) => (
            <div key={c.expression} className="flex items-center gap-2 px-4 py-2">
              <code className="num min-w-0 flex-1 truncate text-body-compact text-ink">
                {c.expression}
              </code>
              <Badge tone="outline">{c.source}</Badge>
              <Button variant="ghost" size="sm" onClick={() => removeCandidate(c.expression)}>
                Remove
              </Button>
            </div>
          ))}
        </Panel>
      )}
      {taskId && (
        <ResultsPanel
          scope={scope}
          status={run.data?.status}
          items={run.data?.items ?? []}
          error={run.isError ? run.error : null}
        />
      )}
    </Page>
  )
}

function ExpressionPanel({
  fieldIds,
  onAdd,
}: {
  fieldIds: string[]
  onAdd: (expression: string) => void
}) {
  const [text, setText] = useState('')
  const options = useQuery({
    queryKey: ['template-lab-options'],
    queryFn: () => templateLab.options(),
    staleTime: 10 * 60 * 1000,
  })

  return (
    <Panel
      title="Write an expression"
      description="What this tells you: a free-hand expression, autocompleted from the account's own operators and the fields in the tray above — nothing offered here is invented."
    >
      {options.isError && (
        <ErrorNotice error={options.error} title="Could not load the operator list" />
      )}
      <ExpressionEditor
        value={text}
        operators={options.data?.reference ?? []}
        fieldIds={fieldIds}
        onChange={setText}
        label="Expression"
      />
      <div className="mt-2 flex justify-end">
        <Button
          variant="primary"
          size="sm"
          disabled={!text.trim()}
          onClick={() => {
            onAdd(text.trim())
            setText('')
          }}
        >
          Add to candidates
        </Button>
      </div>
    </Panel>
  )
}

/** A market and a dataset category, then only the templates that make sense for that data —
 * selection-first, matching old alpha-harness's Template Lab. Fields stay editable per slot
 * (never locked to a saved set) by previewing against this market's whole field catalog and
 * letting a pinned slot narrow it, the same `choices` mechanism Template Studio uses. */
function TemplateLab({
  scope,
  onAdd,
}: {
  scope: Scope
  onAdd: (expression: string, templateId: string, fieldIds: string[]) => void
}) {
  const [datasetCategory, setDatasetCategory] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState<string | null>(null)
  const [choices, setChoices] = useState<Record<string, string | number>>({})

  const byScope = useQuery({
    queryKey: ['forge-templates', 'by-scope', scope],
    queryFn: () =>
      forgeTemplates.byScope({
        region: scope.region,
        universe: scope.universe,
        delay: scope.delay,
        instrumentType: scope.instrumentType,
      }),
  })
  const categories = byScope.data?.datasetCategories ?? []
  const eligible = (byScope.data?.templates ?? []).filter((t) => t.scopeEligible)
  const inCategory = datasetCategory
    ? eligible.filter((t) => t.datasetCategory === datasetCategory)
    : eligible
  const template: ScopeTemplate | null = inCategory.find((t) => t.id === templateId) ?? null

  useEffect(() => {
    if (templateId && !inCategory.some((t) => t.id === templateId)) {
      setTemplateId(null)
      setChoices({})
    }
  }, [inCategory, templateId])

  // The field pool a template's slots can draw from, before any single field is picked — the
  // same 500-field cap scope eligibility was checked against.
  const pool = useQuery({
    queryKey: ['forge-templates-lab', 'catalog', scope],
    queryFn: ({ signal }) => catalog.fields(scope, { limit: 500 }, signal),
  })
  const poolIds = pool.data?.results.map((r) => r.field_id) ?? []

  const canPreview = Boolean(templateId) && poolIds.length > 0
  const preview = useQuery({
    queryKey: ['forge-workbench', 'lab-preview', scope, templateId, poolIds, choices],
    queryFn: () =>
      studio.preview({
        templateId: templateId as string,
        fieldIds: poolIds,
        region: scope.region,
        universe: scope.universe,
        delay: scope.delay,
        instrumentType: scope.instrumentType,
        choices,
        limit: 50,
      }),
    enabled: canPreview,
  })
  const p: PreviewResponse | undefined = preview.data

  const pin = (name: string, kind: string, value: string) =>
    setChoices((prev) => ({ ...prev, [name]: kind === 'window' ? Number(value) : value }))
  const unpin = (name: string) =>
    setChoices((prev) => {
      const { [name]: _, ...rest } = prev
      return rest
    })

  return (
    <Panel
      title="Template Lab"
      description="What this tells you: pick a market and a dataset category first, then only the templates that fit it — swap in whichever real field, window or group you want per slot before running, nothing here is simulated."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <Field
        label="Dataset category"
        hint="Templates for this market, grouped by the kind of data they're built for."
      >
        <Segmented
          label="Dataset category"
          value={datasetCategory ?? '__all__'}
          onChange={(v) => setDatasetCategory(v === '__all__' ? null : v)}
          items={[
            { value: '__all__', label: 'All' },
            ...categories.map((c) => ({ value: c, label: c })),
          ]}
        />
      </Field>

      {byScope.isError && (
        <ErrorNotice error={byScope.error} title="Could not load templates for this market" />
      )}

      <Field
        label="Template"
        hint={
          byScope.isSuccess && inCategory.length === 0
            ? 'No template in the library binds to this market and category.'
            : 'Only templates whose field slots this market could fill at all are offered.'
        }
      >
        <Select
          label="Template"
          className="w-full"
          value={templateId}
          onChange={setTemplateId}
          items={inCategory.map((t) => ({ value: t.id, label: `${t.name} (${t.complexity})` }))}
          disabled={inCategory.length === 0}
        />
      </Field>

      {template && (
        <>
          <p className="text-body-compact text-ink-subtle">{template.rationale}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {template.slots.map((slot) => (
              <Field
                key={slot.name}
                label={`Slot ${slot.name}${slot.hint ? ` — ${slot.hint}` : ''}`}
                hint="Legal choices for this market right now; leave unpinned to see every combination."
              >
                <div className="flex items-center gap-2">
                  <Select
                    label={`Slot ${slot.name}`}
                    className="w-full"
                    mono
                    value={choices[slot.name] != null ? String(choices[slot.name]) : null}
                    onChange={(v) => pin(slot.name, slot.kind, v)}
                    items={(p?.offered[slot.name] ?? []).map((v) => ({
                      value: String(v),
                      label: String(v),
                    }))}
                    disabled={!p || (p.offered[slot.name] ?? []).length === 0}
                  />
                  {choices[slot.name] != null && (
                    <Button variant="ghost" size="sm" onClick={() => unpin(slot.name)}>
                      Clear
                    </Button>
                  )}
                </div>
              </Field>
            ))}
          </div>
        </>
      )}

      {preview.isError && <ErrorNotice error={preview.error} title="Could not render this" />}
      {preview.isPending && canPreview && <Skeleton className="h-48" label="Rendering alphas" />}

      {p && templateId && (
        <PreflightAdvisor
          scope={scope}
          templateId={templateId}
          fieldIds={[...new Set(p.rendered.flatMap((r) => r.dataFields))]}
        />
      )}

      {p && (
        <div className="flex flex-wrap items-end gap-4">
          <Metric
            label="Alphas rendered"
            value={String(p.total)}
            hint={
              p.rendered.length < p.total
                ? `Showing the first ${p.rendered.length}`
                : 'Every combination of field, window and group'
            }
          />
        </div>
      )}
      {p && p.blockers.length > 0 && (
        <Notice tone="warn" title="This template cannot run on this market">
          <ul className="ml-4 list-disc">
            {p.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </Notice>
      )}
      {p && p.notes.length > 0 && (
        <Notice tone="info" title="What these alphas qualify for">
          <ul className="ml-4 list-disc">
            {p.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </Notice>
      )}
      {p && p.rendered.length > 0 && templateId && (
        <div className="flex flex-col divide-y divide-hairline rounded-md border border-hairline">
          {p.rendered.map((alpha) => (
            <CandidateRow
              key={alpha.expression}
              alpha={alpha}
              onAdd={() => onAdd(alpha.expression, templateId, alpha.dataFields)}
            />
          ))}
        </div>
      )}
    </Panel>
  )
}

function CandidateRow({ alpha, onAdd }: { alpha: RenderedAlpha; onAdd: () => void }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <code className="num min-w-0 flex-1 truncate text-body-compact text-ink">
        {alpha.expression}
      </code>
      {alpha.fitsAll ? (
        <Badge tone="profit">fits every type</Badge>
      ) : alpha.fits.length > 0 ? (
        <Badge tone="profit">{alpha.fits.length} types fit</Badge>
      ) : (
        <Badge tone="muted">no type fits</Badge>
      )}
      <Button variant="ghost" size="sm" onClick={onAdd}>
        Add
      </Button>
    </div>
  )
}

/** One group of results: the template/field/region combination every row in it shares. */
interface Group {
  key: string
  templateId: string | null
  fieldIds: string[]
  region: string
  items: RunItemOut[]
}

function groupBy(items: RunItemOut[]): Group[] {
  const groups = new Map<string, Group>()
  for (const item of items) {
    const key = `${item.templateId ?? DASH}·${item.fieldIds.join(',')}·${item.region}`
    const group = groups.get(key)
    if (group) group.items.push(item)
    else
      groups.set(key, {
        key,
        templateId: item.templateId,
        fieldIds: item.fieldIds,
        region: item.region,
        items: [item],
      })
  }
  return [...groups.values()]
}

function ResultsPanel({
  status,
  items,
  error,
}: {
  scope: Scope
  status: string | undefined
  items: RunItemOut[]
  error: unknown
}) {
  const groups = groupBy(items)
  const done = items.filter((i) => i.status !== 'queued').length

  return (
    <Panel
      title="Results"
      description="What this tells you: every candidate from this run, grouped by the template, fields and region it came from. Train-period Sharpe only — test metrics, once BRAIN has them, are shown separately and never used to pick a winner (CLAUDE.md rule 6)."
      actions={
        <Badge tone={status === 'running' ? 'outline' : 'profit'}>
          {status === 'running' ? `${done} / ${items.length}` : 'done'}
        </Badge>
      }
      bodyClassName="flex flex-col divide-y divide-hairline p-0"
    >
      {Boolean(error) && <ErrorNotice error={error} title="Could not load this run" />}
      {groups.map((g) => (
        <div key={g.key} className="flex flex-col gap-1.5 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-body-compact text-ink-subtle">
            <Badge tone="outline">{g.templateId ?? 'hand-written'}</Badge>
            <span className="num">{g.fieldIds.join(', ') || DASH}</span>
            <span className="num">{g.region}</span>
          </div>
          {g.items.map((item) => (
            <div key={item.expression} className="flex items-center gap-2 pl-1">
              <code className="num min-w-0 flex-1 truncate text-body-compact text-ink">
                {item.expression}
              </code>
              {item.status === 'queued' && <Badge tone="outline">running</Badge>}
              {item.status === 'done' && (
                <Metric size="sm" label="Train Sharpe" value={fmt.ratio(item.trainSharpe)} />
              )}
              {item.status === 'failed' && (
                <Badge tone="muted" {...(item.error ? { title: item.error } : {})}>
                  failed
                </Badge>
              )}
            </div>
          ))}
        </div>
      ))}
    </Panel>
  )
}
