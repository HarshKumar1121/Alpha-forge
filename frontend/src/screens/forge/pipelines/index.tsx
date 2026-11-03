/**
 * Pipelines: pick a pipeline config, a field set and template(s), see what it would cost, then
 * run it for real against BRAIN through the Quota Governor.
 *
 * Preview never simulates — it is the same generate + lint + admit the orchestrator itself does
 * before spending anything. Approve starts a background run; progress is read live from the
 * `tasks` registry the top bar's own busy indicator uses, over the websocket already open for
 * it — no new channel, no polling loop.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { useCallback, useEffect, useState } from 'react'
import type { Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import { forgeTemplates } from '@/screens/forge/templates/api'
import {
  Badge,
  Button,
  Chips,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Progress,
  Skeleton,
} from '@/ui/kit'
import { Select } from '@/ui/overlay'
import {
  type CandidateOut,
  type PipelineOut,
  pipelines,
  type RunReport,
  type RunSummary,
} from './api'

export function PipelinesScreen() {
  const queryClient = useQueryClient()
  // Sent by Template Studio's "Send to Pipelines" — preselects the field set and template once.
  const search = useSearch({ from: '/forge/pipelines' })
  const [scope, setScope] = useForgeScope()
  const [pipelineName, setPipelineName] = useState<string | null>(null)
  const [fieldSetId, setFieldSetId] = useState<number | null>(search.fs ?? null)
  const [fieldIds, setFieldIds] = useState<string[]>([])
  const [templateIds, setTemplateIds] = useState<string[]>(search.t ? [search.t] : [])
  const [budget, setBudget] = useState<number | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)

  const pipelineList = useQuery({ queryKey: ['forge-pipelines'], queryFn: pipelines.list })
  const runHistory = useQuery({
    queryKey: ['forge-pipelines', 'runs'],
    queryFn: pipelines.listRuns,
  })
  const templates = useQuery({
    queryKey: ['forge-templates', 'list'],
    queryFn: forgeTemplates.list,
    staleTime: 5 * 60 * 1000,
  })

  const pipeline = pipelineName ?? pipelineList.data?.[0]?.name ?? null
  const selected = pipelineList.data?.find((p) => p.name === pipeline) ?? null
  const effectiveBudget = budget ?? selected?.defaultBudget ?? null

  // A pipeline draws only from its own template complexities (config/forge/pipelines/*.yaml).
  const eligibleTemplates = (templates.data?.templates ?? []).filter(
    (t) => !selected || selected.templateComplexity.includes(t.complexity),
  )

  const ready = Boolean(
    pipeline && fieldIds.length > 0 && templateIds.length > 0 && effectiveBudget,
  )
  // A saved field set is sent by id; fields picked on the spot go as an inline, unsaved set.
  const fieldArgs = useCallback(
    () =>
      fieldSetId != null
        ? { fieldSetId }
        : {
            fields: {
              fieldIds,
              region: scope.region,
              universe: scope.universe,
              delay: scope.delay,
              instrumentType: scope.instrumentType,
            },
          },
    [fieldSetId, fieldIds, scope],
  )

  const preview = useQuery({
    queryKey: [
      'forge-pipelines',
      'preview',
      pipeline,
      fieldSetId,
      fieldIds,
      templateIds,
      effectiveBudget,
    ],
    queryFn: () =>
      pipelines.preview({
        pipeline: pipeline as string,
        ...fieldArgs(),
        templateIds,
        budget: effectiveBudget,
      }),
    enabled: ready,
  })

  const run = useMutation({
    mutationFn: () =>
      pipelines.run({
        pipeline: pipeline as string,
        ...fieldArgs(),
        templateIds,
        budget: effectiveBudget,
      }),
    onSuccess: (started) => {
      setTaskId(started.taskId)
      void queryClient.invalidateQueries({ queryKey: ['forge-pipelines', 'runs'] })
    },
  })

  return (
    <Page>
      <PageHeader
        title="Pipelines"
        description="Cross a pipeline's templates with a field set, see what it would cost before anything runs, then approve it. A run spends real BRAIN simulations through the Quota Governor — nothing here ever submits an alpha; that stays a manual step on the BRAIN website."
      />

      <Setup
        pipelineList={pipelineList.data}
        selected={selected}
        onPipeline={(name) => {
          setPipelineName(name)
          setTaskId(null)
        }}
        scope={scope}
        onScope={setScope}
        fieldIds={fieldIds}
        initialFieldSetId={fieldSetId}
        onFields={(ids, setId) => {
          setFieldIds(ids)
          setFieldSetId(setId)
          setTaskId(null)
        }}
        templates={eligibleTemplates}
        templateIds={templateIds}
        onTemplates={(ids) => {
          setTemplateIds(ids)
          setTaskId(null)
        }}
        budget={effectiveBudget}
        onBudget={(n) => {
          setBudget(n)
          setTaskId(null)
        }}
      />

      {!ready && !taskId && (
        <Empty title="Pick fields, templates and a budget to plan a run">
          Choose a field set or search fields above, the templates this pipeline should cross them
          with, and a budget. The plan preview appears here once all three are picked — nothing runs
          until you approve it.
        </Empty>
      )}

      {ready && !taskId && (
        <PlanPreviewPanel
          preview={preview.data}
          isPending={preview.isPending}
          error={preview.isError ? preview.error : null}
          onApprove={() => run.mutate()}
          approving={run.isPending}
          approveError={run.isError ? run.error : null}
        />
      )}

      {taskId && <RunProgress taskId={taskId} onReset={() => setTaskId(null)} />}

      <RunHistoryPanel runs={runHistory.data} isPending={runHistory.isPending} />
    </Page>
  )
}

function RunHistoryPanel({
  runs,
  isPending,
}: {
  runs: RunSummary[] | undefined
  isPending: boolean
}) {
  return (
    <Panel
      title="Run history"
      description="What this tells you: every pipeline run this process knows about, including runs you started earlier or before a restart — not just the one you're watching now."
      bodyClassName="flex flex-col gap-0 divide-y divide-hairline p-0"
    >
      {isPending && <Skeleton className="h-24 m-4" label="Loading run history" />}
      {!isPending && (runs?.length ?? 0) === 0 && (
        <Empty title="No runs yet" className="m-4">
          Approve a plan above to start one — it will show up here once it starts, and stay here
          after it finishes.
        </Empty>
      )}
      {runs?.map((r) => (
        <div key={r.taskId} className="flex flex-col gap-1 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              tone={r.status === 'done' ? 'profit' : r.status === 'failed' ? 'loss' : 'neutral'}
            >
              {r.status}
            </Badge>
            <span className="text-body-compact text-ink">
              {r.pipeline} on {r.fieldSet}
            </span>
            <span className="text-body-compact text-ink-subtle">{fmt.dateTime(r.startedAt)}</span>
          </div>
          <div className="flex flex-wrap gap-3 text-body-compact text-ink-subtle">
            <span>budget {fmt.int(r.budget)}</span>
            <span>spent {fmt.int(r.spent)}</span>
            <span>{fmt.int(r.candidatesCount)} candidate(s)</span>
            <span>{fmt.int(r.finalistsCount)} finalist(s)</span>
          </div>
        </div>
      ))}
    </Panel>
  )
}

function Setup({
  pipelineList,
  selected,
  onPipeline,
  scope,
  onScope,
  fieldIds,
  initialFieldSetId,
  onFields,
  templates,
  templateIds,
  onTemplates,
  budget,
  onBudget,
}: {
  pipelineList: PipelineOut[] | undefined
  selected: PipelineOut | null
  onPipeline: (name: string) => void
  scope: Scope
  onScope: (change: Partial<Scope>) => void
  fieldIds: string[]
  initialFieldSetId: number | null
  onFields: (fieldIds: string[], fieldSetId: number | null) => void
  templates: { id: string; name: string; complexity: string }[]
  templateIds: string[]
  onTemplates: (ids: string[]) => void
  budget: number | null
  onBudget: (n: number) => void
}) {
  return (
    <Panel
      title="What to run"
      description="What this tells you: which factory, on which fields and ideas, for how many simulations. The lane and template complexities come from the pipeline's own config — this screen only picks the market, the fields, the templates and the budget."
      bodyClassName="grid gap-4 p-4 sm:grid-cols-2"
    >
      <Field label="Pipeline" hint="Which factory, and which BRAIN lane pays for it.">
        <Select
          label="Pipeline"
          items={(pipelineList ?? []).map((p) => ({ value: p.name, label: p.name }))}
          value={selected?.name ?? null}
          onChange={onPipeline}
        />
      </Field>
      <Field
        label="Market"
        hint="Which account scope the fields below come from."
        className="sm:col-span-2"
      >
        <ForgeScopePicker scope={scope} onChange={onScope} />
      </Field>
      <Field
        label="Fields"
        hint="Pick a saved field set, or search and tick fields straight from this market — both end up crossed with the templates below."
        className="sm:col-span-2"
      >
        <FieldPicker
          scope={scope}
          selected={fieldIds}
          onChange={onFields}
          initialFieldSetId={initialFieldSetId}
        />
      </Field>
      <Field
        label="Templates"
        hint={
          selected
            ? `This pipeline draws from ${selected.templateComplexity.join(', ')} templates.`
            : 'Pick a pipeline first.'
        }
        className="sm:col-span-2"
      >
        {templates.length === 0 ? (
          <p className="text-body-compact text-ink-subtle">
            No templates of this pipeline's complexity are available yet.
          </p>
        ) : (
          <Chips
            label="Templates"
            items={templates.map((t) => ({ value: t.id, label: t.name }))}
            value={templateIds}
            onChange={onTemplates}
          />
        )}
      </Field>
      <Field
        label="Budget"
        hint={
          selected
            ? `Simulations to spend at most this round, capped further by the Quota Governor. Default: ${fmt.int(selected.defaultBudget)}.`
            : 'Simulations to spend at most this round.'
        }
      >
        <Input
          type="number"
          min={1}
          value={budget ?? ''}
          onChange={(e) => {
            const n = Number(e.target.value)
            if (n > 0) onBudget(n)
          }}
        />
      </Field>
    </Panel>
  )
}

function PlanPreviewPanel({
  preview,
  isPending,
  error,
  onApprove,
  approving,
  approveError,
}: {
  preview: Awaited<ReturnType<typeof pipelines.preview>> | undefined
  isPending: boolean
  error: unknown
  onApprove: () => void
  approving: boolean
  approveError: unknown
}) {
  if (error) return <ErrorNotice error={error} title="Could not plan this run" />
  if (isPending || !preview) return <Skeleton className="h-48" label="Planning the run" />

  const admission = preview.admission
  const willTrim = admission.trimmed > 0

  return (
    <Panel
      title="Plan preview"
      description="What this tells you: how many alphas this would generate, how many survive the alpha type's own rules before costing anything, and how many simulations the Quota Governor would actually let through right now. Nothing has run yet."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric
          label="Generated"
          value={fmt.int(preview.generated)}
          hint="Every alpha the bound templates x field set render"
        />
        <Metric
          label="Eligible"
          value={fmt.int(preview.eligible)}
          hint="What the alpha type's own lint would not refuse before it costs a simulation"
        />
        <Metric
          label="Admitted"
          value={fmt.int(admission.admitted)}
          tone={willTrim ? 'warn' : 'profit'}
          hint={admission.reason}
        />
        <Metric
          label="Lane remaining"
          value={fmt.int(admission.laneRemaining)}
          hint={`${admission.lane}: what it may still plan today, before this run`}
        />
      </div>

      {preview.rejectedTotal > 0 && (
        <Notice
          tone="info"
          title={`${fmt.int(preview.rejectedTotal)} candidate(s) lint would drop`}
        >
          <ul className="ml-4 list-disc">
            {preview.rejectedSample.slice(0, 5).map((r) => (
              <li key={r.expression} className="text-body-compact">
                <code className="num break-all">{r.expression}</code> — {r.reason}
              </li>
            ))}
          </ul>
        </Notice>
      )}

      {willTrim && (
        <Notice tone="warn" title="The Quota Governor would trim this plan">
          {admission.reason} Only {fmt.int(admission.admitted)} of {fmt.int(admission.asked)} asked
          would run this time.
        </Notice>
      )}

      {preview.candidatesSample.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-body-compact text-ink-subtle">
            What this tells you: a sample of eligible candidates, each judged against every alpha
            type — not just this pipeline's own — so you can see where the same idea might also fit,
            and where it technically fits but isn't a clean choice.
          </p>
          <div className="flex flex-col gap-2 divide-y divide-hairline">
            {preview.candidatesSample.slice(0, 5).map((c) => (
              <CandidateVerdicts key={c.expression} candidate={c} />
            ))}
          </div>
        </div>
      )}

      {approveError != null && <ErrorNotice error={approveError} title="The run could not start" />}

      <div>
        <Button
          variant="primary"
          onClick={onApprove}
          disabled={approving || admission.admitted === 0}
        >
          {approving
            ? 'Starting…'
            : `Approve: spend up to ${fmt.int(admission.admitted)} simulations`}
        </Button>
        {admission.admitted === 0 && (
          <p className="mt-2 text-body-compact text-ink-subtle">
            Nothing would be admitted right now — {admission.reason.toLowerCase()}
          </p>
        )}
      </div>
    </Panel>
  )
}

const VERDICT_TONE = {
  FITS: 'profit',
  FITS_BUT_WEAK: 'warn',
  DOESNT_FIT: 'loss',
} as const

/** One candidate's expression plus a per-alpha-type FITS / FITS BUT WEAK / DOESN'T FIT badge
 *  row, each with a one-line reason on hover — replaces a blanket "fits every type" claim. */
function CandidateVerdicts({ candidate }: { candidate: CandidateOut }) {
  const entries = Object.entries(candidate.verdicts ?? {})
  return (
    <div className="flex flex-col gap-1 py-2">
      <code className="num block text-body-compact break-all text-ink">{candidate.expression}</code>
      {entries.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {entries.map(([type, v]) => (
            <Badge
              key={type}
              tone={VERDICT_TONE[v.verdict as keyof typeof VERDICT_TONE] ?? 'neutral'}
              title={v.reason ?? `Fits ${type.replaceAll('_', ' ')} cleanly`}
            >
              {type.replaceAll('_', ' ')}
              {v.verdict === 'FITS_BUT_WEAK' && ' (weak)'}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

function RunProgress({ taskId, onReset }: { taskId: string; onReset: () => void }) {
  const queryClient = useQueryClient()
  const tasksLive = useLive((s) => s.tasks)
  const task = tasksLive?.tasks.find((t) => t.id === taskId) ?? null
  const finished = task == null || task.state !== 'running'

  const report = useQuery({
    queryKey: ['forge-pipelines', 'run', taskId],
    queryFn: () => pipelines.getRun(taskId),
    enabled: finished,
    retry: (count, error) => {
      const code = (error as { code?: string } | undefined)?.code
      return code === 'not_ready' && count < 20
    },
    retryDelay: 500,
  })

  const settled = task?.state === 'done' || task?.state === 'failed'
  useEffect(() => {
    if (settled) void queryClient.invalidateQueries({ queryKey: ['forge-pipelines', 'runs'] })
  }, [settled, queryClient])

  return (
    <Panel
      title="This run"
      description="What this tells you: what the pipeline is doing right now, and — once it finishes — every candidate it produced, checked and correlated. Checks and correlations are BRAIN's own; nothing here submits an alpha."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      {task && (
        <div className="flex flex-col gap-2">
          <Progress
            value={task.progress ?? null}
            label={task.state === 'running' ? 'Running' : task.state}
          />
          <p className="text-body-compact text-ink-subtle">{task.detail || 'Starting…'}</p>
        </div>
      )}
      {!task && !report.data && <Skeleton className="h-24" label="Waiting for the run to start" />}

      {task?.state === 'failed' && (
        <Notice tone="error" title="The run failed">
          {task.error ?? 'No further detail was recorded.'}
        </Notice>
      )}

      {report.isError && (report.error as { code?: string }).code !== 'not_ready' && (
        <ErrorNotice error={report.error} title="The run's report could not load" />
      )}

      {report.data && <RunReportPanel report={report.data} />}

      <div>
        <Button
          variant="secondary"
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ['forge-pipelines', 'preview'] })
            onReset()
          }}
        >
          Plan another run
        </Button>
      </div>
    </Panel>
  )
}

function RunReportPanel({ report }: { report: RunReport }) {
  if (report.candidates.length === 0) {
    return (
      <Empty title="Nothing passed">
        Every candidate either failed to simulate or missed the alpha type's train gate. Try a wider
        field set, a different template, or a bigger budget.
      </Empty>
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="Spent" value={fmt.int(report.spent)} hint="Simulations this run paid for" />
        <Metric
          label="Candidates"
          value={fmt.int(report.candidates.length)}
          hint="Simulated and kept for ranking"
        />
        <Metric
          label="Finalists"
          value={fmt.int(report.finalists.length)}
          hint="Survived robustness and the judge"
        />
        <Metric
          label="Checked"
          value={fmt.int(Object.keys(report.checks).length)}
          hint="Got a real BRAIN /check — the train-gate passers only"
        />
      </div>
      <div className="flex flex-col divide-y divide-hairline">
        {report.finalists.map((c) => (
          <div key={c.expression} className="flex flex-col gap-1 py-2">
            <div className="flex flex-wrap items-center gap-2 text-body-compact text-ink-subtle">
              {c.alphaId && <Badge tone="profit">{c.alphaId}</Badge>}
              <span>train sharpe {fmt.ratio(c.trainSharpe)}</span>
              <span>test sharpe {fmt.ratio(c.testSharpe)}</span>
            </div>
            <CandidateVerdicts candidate={c} />
          </div>
        ))}
      </div>
    </div>
  )
}
