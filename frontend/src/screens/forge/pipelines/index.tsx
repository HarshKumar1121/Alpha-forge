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
import { useEffect, useState } from 'react'
import { fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import { fieldSets } from '@/screens/forge/data/api'
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
import { type PipelineOut, pipelines, type RunReport, type RunSummary } from './api'

export function PipelinesScreen() {
  const queryClient = useQueryClient()
  const [pipelineName, setPipelineName] = useState<string | null>(null)
  const [fieldSetId, setFieldSetId] = useState<number | null>(null)
  const [templateIds, setTemplateIds] = useState<string[]>([])
  const [budget, setBudget] = useState<number | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)

  const pipelineList = useQuery({ queryKey: ['forge-pipelines'], queryFn: pipelines.list })
  const runHistory = useQuery({
    queryKey: ['forge-pipelines', 'runs'],
    queryFn: pipelines.listRuns,
  })
  const sets = useQuery({ queryKey: ['forge-fieldsets'], queryFn: fieldSets.list })
  const templates = useQuery({
    queryKey: ['forge-templates', 'list'],
    queryFn: forgeTemplates.list,
    staleTime: 5 * 60 * 1000,
  })

  const pipeline = pipelineName ?? pipelineList.data?.[0]?.name ?? null
  const selected = pipelineList.data?.find((p) => p.name === pipeline) ?? null
  const set = fieldSetId ?? sets.data?.[0]?.id ?? null
  const effectiveBudget = budget ?? selected?.defaultBudget ?? null

  // A pipeline draws only from its own template complexities (config/forge/pipelines/*.yaml).
  const eligibleTemplates = (templates.data?.templates ?? []).filter(
    (t) => !selected || selected.templateComplexity.includes(t.complexity),
  )

  const ready = Boolean(pipeline && set != null && templateIds.length > 0 && effectiveBudget)

  const preview = useQuery({
    queryKey: ['forge-pipelines', 'preview', pipeline, set, templateIds, effectiveBudget],
    queryFn: () =>
      pipelines.preview({
        pipeline: pipeline as string,
        fieldSetId: set as number,
        templateIds,
        budget: effectiveBudget,
      }),
    enabled: ready,
  })

  const run = useMutation({
    mutationFn: () =>
      pipelines.run({
        pipeline: pipeline as string,
        fieldSetId: set as number,
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
        sets={sets.data}
        fieldSetId={set}
        onFieldSet={(id) => {
          setFieldSetId(id)
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

      {sets.data?.length === 0 && (
        <Panel title="No field sets yet">
          <Empty title="A pipeline needs real fields to cross with its templates">
            Make one in the Data Explorer: narrow a market to the fields behind an idea, tick them,
            and choose "Save as field set".
          </Empty>
        </Panel>
      )}

      {!ready && !taskId && (sets.data?.length ?? 0) > 0 && (
        <Empty title="Pick at least one template to plan a run">
          Choose the templates this pipeline should cross with the field set above, and a budget.
          The plan preview appears here once both are picked — nothing runs until you approve it.
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
  sets,
  fieldSetId,
  onFieldSet,
  templates,
  templateIds,
  onTemplates,
  budget,
  onBudget,
}: {
  pipelineList: PipelineOut[] | undefined
  selected: PipelineOut | null
  onPipeline: (name: string) => void
  sets:
    | { id: number; name: string; scope: { region: string; delay: number; universe: string } }[]
    | undefined
  fieldSetId: number | null
  onFieldSet: (id: number) => void
  templates: { id: string; name: string; complexity: string }[]
  templateIds: string[]
  onTemplates: (ids: string[]) => void
  budget: number | null
  onBudget: (n: number) => void
}) {
  return (
    <Panel
      title="What to run"
      description="What this tells you: which factory, on which fields and ideas, for how many simulations. The lane and template complexities come from the pipeline's own config — this screen only picks the field set, the templates and the budget."
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
      <Field label="Field set" hint="Its market decides which fields exist.">
        <Select
          label="Field set"
          items={(sets ?? []).map((s) => ({
            value: String(s.id),
            label: `${s.name} — ${s.scope.region} D${s.scope.delay} ${s.scope.universe}`,
          }))}
          value={fieldSetId == null ? null : String(fieldSetId)}
          onChange={(value) => onFieldSet(Number(value))}
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
            <code className="num block text-body-compact break-all text-ink">{c.expression}</code>
            <div className="flex flex-wrap items-center gap-2 text-body-compact text-ink-subtle">
              {c.alphaId && <Badge tone="profit">{c.alphaId}</Badge>}
              <span>train sharpe {fmt.ratio(c.trainSharpe)}</span>
              <span>test sharpe {fmt.ratio(c.testSharpe)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
