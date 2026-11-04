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
import { type Scope, scopeLabel } from '@/api/types'
import { DASH, fmt } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { nuggets } from '@/screens/forge/nuggets/api'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import { type PreviewResponse, type RenderedAlpha, studio } from '@/screens/forge/studio/api'
import { TemplatePicker } from '@/screens/forge/templates/template-picker'
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

type FieldSource = 'tray' | 'nugget'

export function WorkbenchScreen() {
  const [scope, setScope] = useForgeScope()
  const client = useQueryClient()
  const [fieldIds, setFieldIds] = useState<string[]>([])
  const [fieldSetId, setFieldSetId] = useState<number | null>(null)
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
    setFieldSetId(null) // the saved tray is raw field ids; which field set (if any) is unknown
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
          <FieldPicker
            scope={scope}
            selected={fieldIds}
            onChange={(ids, fsId) => {
              setFieldIds(ids)
              setFieldSetId(fsId)
            }}
          />
        )}
      </Panel>
      <ExpressionPanel
        fieldIds={fieldIds}
        onAdd={(expression) =>
          addCandidate({ expression, source: 'manual', templateId: null, fieldIds })
        }
      />
      <TemplateDrawer
        scope={scope}
        fieldIds={fieldIds}
        fieldSetId={fieldSetId}
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

function TemplateDrawer({
  scope,
  fieldIds,
  fieldSetId,
  onAdd,
}: {
  scope: Scope
  fieldIds: string[]
  fieldSetId: number | null
  onAdd: (expression: string, templateId: string, fieldIds: string[]) => void
}) {
  const [templateId, setTemplateId] = useState<string | null>(null)
  const [source, setSource] = useState<FieldSource>('tray')
  const [nuggetId, setNuggetId] = useState<string | null>(null)

  const myNuggets = useQuery({ queryKey: ['forge-nuggets', 'list'], queryFn: () => nuggets.list() })
  const inScope = (myNuggets.data ?? []).filter(
    (n) =>
      n.scope.region === scope.region &&
      n.scope.delay === scope.delay &&
      n.scope.universe === scope.universe &&
      n.scope.instrumentType === scope.instrumentType,
  )

  const canRender = Boolean(
    templateId && (source === 'tray' ? fieldSetId != null : nuggetId != null),
  )
  const preview = useQuery({
    queryKey: ['forge-workbench', 'preview', templateId, source, fieldSetId, nuggetId],
    queryFn: () =>
      studio.preview({
        templateId: templateId as string,
        fieldSetId: source === 'tray' ? fieldSetId : null,
        nuggetId: source === 'nugget' ? nuggetId : null,
        limit: 50,
      }),
    enabled: canRender,
  })
  const p: PreviewResponse | undefined = preview.data

  return (
    <Panel
      title="Fill a template"
      description="What this tells you: the real alphas a template renders here, and which alpha types each one actually qualifies for — the same check Template Studio and BRAIN's own eligibility rules use. Nothing here is simulated."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TemplatePicker
          scope={scope}
          fieldIds={fieldIds}
          value={templateId}
          onChange={setTemplateId}
        />
        <Field
          label="Field source"
          hint="The tray's fields (saved as a field set) or a Data Nugget bound to this market."
        >
          <div className="flex flex-col gap-2">
            <Segmented
              label="Field source"
              value={source}
              onChange={setSource}
              items={[
                { value: 'tray', label: 'Tray' },
                { value: 'nugget', label: 'Nugget' },
              ]}
            />
            {source === 'nugget' && (
              <Select
                label="Nugget"
                items={inScope.map((n) => ({ value: n.nuggetId, label: n.name }))}
                value={nuggetId}
                onChange={setNuggetId}
                disabled={inScope.length === 0}
              />
            )}
          </div>
        </Field>
      </div>

      {source === 'tray' && fieldSetId == null && fieldIds.length > 0 && (
        <Notice tone="info" title="Save the tray as a field set to fill a template from it">
          Use "Save as field set" above, or pick an existing field set in the tray panel — a
          template renders from a saved set, the same way Template Studio does.
        </Notice>
      )}
      {source === 'nugget' && inScope.length === 0 && (
        <Notice tone="info" title="No nugget saved for this market yet">
          Save one in Data Nuggets first — a nugget's fields keep their own data treatment when a
          template binds to them.
        </Notice>
      )}

      {preview.isError && <ErrorNotice error={preview.error} title="Could not render this" />}
      {preview.isPending && canRender && <Skeleton className="h-48" label="Rendering alphas" />}

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
        <Notice tone="warn" title="This template cannot run on this field source">
          <ul className="ml-4 list-disc">
            {p.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </Notice>
      )}
      {p && p.missingFields.length > 0 && (
        <Notice tone="info" title="Some fields are not in this market">
          <span className="num">{p.missingFields.join(', ')}</span> — they are skipped.
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
