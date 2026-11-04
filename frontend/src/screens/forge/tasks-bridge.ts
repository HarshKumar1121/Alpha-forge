/** Projects Forge's own run records — Pipelines, Workbench, Workshop Court — into the Tasks
 *  screen's `LabTask` shape, so every Forge simulation shows up in the one place every lab's
 *  own simulations already do: same list, same status badge, same progress bar.
 *
 *  Nuggets runs no BRAIN simulations today (it only does local correlation math and free BRAIN
 *  correlation reads), so it has nothing to project here yet.
 *
 *  Each source has its own durable table (`forge_pipeline_run`, `forge_workbench_run_item`,
 *  `forge_workshop_trial`) rather than a `Study`/`Trial` row: a pipeline or Workbench run is
 *  "spend a fixed budget, then report", not an Optuna sweep with a resumable queue, so this
 *  reads those tables and maps them rather than forcing them through the lab scheduler. That
 *  also means a Forge-origin row has no Run/Pause/Stop/Edit of its own here — it already ran to
 *  completion (or failure) the moment its screen started it.
 */

import { useQuery } from '@tanstack/react-query'
import { type RunSummary as PipelineRunSummary, pipelines } from '@/screens/forge/pipelines/api'
import { type RunSummary as WorkbenchRunSummary, workbench } from '@/screens/forge/workbench/api'
import { type TrialOut, workshop } from '@/screens/forge/workshop/api'
import type { LabTask, TaskStatus } from '@/screens/tasks/api'

const STATUS_OF: Record<string, TaskStatus> = {
  running: 'RUNNING',
  done: 'COMPLETE',
  failed: 'FAILED',
}

/** Fields every projected row shares, so each mapper below only states what makes it different. */
function base(): Omit<
  LabTask,
  | 'id'
  | 'source'
  | 'name'
  | 'lab'
  | 'labName'
  | 'status'
  | 'message'
  | 'region'
  | 'delay'
  | 'universe'
  | 'target'
  | 'simulated'
  | 'failed'
  | 'best'
  | 'createdAt'
  | 'startedAt'
  | 'finishedAt'
> {
  return {
    templateName: null,
    template: null,
    stopping: false,
    alphaId: null,
    markets: null,
    truncation: null,
    truncationAgent: false,
    nanHandling: null,
    testPeriod: null,
    expression: null,
    seeds: 0,
    population: null,
    mutationRate: null,
    decay: null,
    cores: 1,
    datasetIds: [],
    fields: 0,
    cached: 0,
    fullRuns: 0,
    queued: 0,
    running: 0,
    failures: [],
    objectiveLabel: 'Sharpe',
    queuedAt: null,
    llmModels: {},
  }
}

function pipelineRow(r: PipelineRunSummary): LabTask {
  return {
    ...base(),
    id: r.taskId,
    source: 'forge-pipeline',
    name: `${r.pipeline} on ${r.fieldSet}`,
    lab: 'forge-pipeline',
    labName: 'Forge Pipeline',
    status: STATUS_OF[r.status] ?? 'RUNNING',
    message:
      r.status === 'failed' ? 'The pipeline run failed — open it on the Pipelines screen.' : null,
    region: null,
    delay: null,
    universe: null,
    target: r.budget,
    simulated: r.spent,
    failed: r.rejectedCount,
    best: null,
    createdAt: r.startedAt,
    startedAt: r.startedAt,
    finishedAt: r.finishedAt,
  }
}

function workbenchRow(r: WorkbenchRunSummary): LabTask {
  const status: TaskStatus =
    r.done < r.total ? 'RUNNING' : r.failed === r.total ? 'FAILED' : 'COMPLETE'
  return {
    ...base(),
    id: r.taskId,
    source: 'forge-workbench',
    name: `${r.total} candidate${r.total === 1 ? '' : 's'} on ${r.region} D${r.delay} ${r.universe}`,
    lab: 'forge-workbench',
    labName: 'Forge Workbench',
    status,
    message: null,
    region: r.region,
    delay: r.delay,
    universe: r.universe,
    target: r.total,
    simulated: r.done,
    failed: r.failed,
    best: r.bestSharpe,
    createdAt: r.createdAt,
    startedAt: r.createdAt,
    finishedAt: status === 'RUNNING' ? null : r.createdAt,
  }
}

function workshopRow(r: TrialOut): LabTask {
  const status: TaskStatus =
    r.status === 'running' ? 'RUNNING' : r.status === 'failed' ? 'FAILED' : 'COMPLETE'
  const title = (r.hypothesis as { title?: string } | null)?.title ?? 'Workshop idea'
  return {
    ...base(),
    id: `workshop-${r.id}`,
    source: 'forge-workshop',
    name: `${title} · ${r.pipeline}${r.verdict ? ` (${r.verdict})` : ''}`,
    lab: 'forge-workshop',
    labName: 'Forge Workshop',
    status,
    message: r.error || null,
    region: null,
    delay: null,
    universe: null,
    target: 1,
    simulated: status === 'RUNNING' ? 0 : 1,
    failed: status === 'FAILED' ? 1 : 0,
    best: null,
    createdAt: r.createdAt,
    startedAt: r.createdAt,
    finishedAt: r.finishedAt,
  }
}

/** Every Forge simulation, shaped like a lab's own Task, polled the same way the Tasks screen
 *  polls `studies`/`simulations` — so a run started from Pipelines, Workbench or the Workshop
 *  appears in the Tasks list within a few seconds of starting, same as a lab sweep does. */
export function useForgeTasks(): LabTask[] {
  const runs = useQuery({
    queryKey: ['forge-pipeline-runs'],
    queryFn: pipelines.listRuns,
    refetchInterval: 3_000,
  })
  const items = useQuery({
    queryKey: ['forge-workbench-runs'],
    queryFn: workbench.listRuns,
    refetchInterval: 3_000,
  })
  const trials = useQuery({
    queryKey: ['forge-workshop-trials'],
    queryFn: workshop.listTrials,
    refetchInterval: 3_000,
  })
  return [
    ...(runs.data ?? []).map(pipelineRow),
    ...(items.data ?? []).map(workbenchRow),
    ...(trials.data ?? []).map(workshopRow),
  ]
}

/** A Forge-origin task's id, as its own API wants it (its string run id, or a trial's numeric
 *  id with the `workshop-` prefix stripped back off). */
export function forgeRunId(task: LabTask): string {
  return String(task.id).replace(/^workshop-/, '')
}
