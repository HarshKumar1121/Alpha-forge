/** The Tasks screen's detail panel for a Forge-origin row (see `tasks-bridge.ts`): what it ran
 *  on, its progress, and its results read straight from its own screen's API — not the
 *  Trial/Alpha table a lab task's detail reads, since a Forge run keeps no `Trial` rows. */

import { useQuery } from '@tanstack/react-query'
import { XIcon } from 'lucide-react'
import { fmt } from '@/lib/format'
import { PostrunAdvisor } from '@/screens/forge/advisor/panel'
import { pipelines } from '@/screens/forge/pipelines/api'
import { forgeRunId } from '@/screens/forge/tasks-bridge'
import { workbench } from '@/screens/forge/workbench/api'
import { workshop } from '@/screens/forge/workshop/api'
import type { LabTask } from '@/screens/tasks/api'
import { taskStatus } from '@/screens/tasks/columns'
import { Badge, Button, Empty, Metric, Notice, Panel } from '@/ui/kit'

export function ForgeTaskDetail({ task, onClose }: { task: LabTask; onClose: () => void }) {
  const id = forgeRunId(task)
  const done = task.status === 'COMPLETE' || task.status === 'FAILED'
  const { label, tone } = taskStatus(task)

  const report = useQuery({
    queryKey: ['forge-pipeline-run', id],
    queryFn: () => pipelines.getRun(id),
    enabled: task.source === 'forge-pipeline' && task.status === 'COMPLETE',
    retry: false,
  })
  const run = useQuery({
    queryKey: ['forge-workbench-run', id],
    queryFn: () => workbench.getRun(id),
    enabled: task.source === 'forge-workbench',
    retry: false,
  })
  const trial = useQuery({
    queryKey: ['forge-workshop-trial', id],
    queryFn: () => workshop.getTrial(Number(id)),
    enabled: task.source === 'forge-workshop',
    retry: false,
  })

  return (
    <Panel
      title={task.name ?? task.labName}
      description={`${task.labName} — a Forge run, not a lab sweep: it has no Run/Pause/Stop of its own here.`}
      actions={
        <>
          <Badge tone={tone}>{label}</Badge>
          <Button size="icon-sm" variant="ghost" aria-label="Close this task" onClick={onClose}>
            <XIcon />
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-3 *:min-w-44 *:flex-1">
          <Metric
            boxed
            label="Simulated"
            value={`${fmt.int(task.simulated)} / ${fmt.int(task.target)}`}
          />
          <Metric boxed label="Best" value={fmt.ratio(task.best)} />
          {task.failed > 0 && (
            <Metric boxed tone="loss" label="Failed" value={fmt.int(task.failed)} />
          )}
        </div>
        {task.message && (
          <Notice tone={task.status === 'FAILED' ? 'error' : 'info'} title={task.message} />
        )}

        {task.source === 'forge-pipeline' &&
          (report.data ? (
            <>
              <ResultList
                label="Finalists"
                rows={report.data.finalists.map((c) => ({
                  expression: c.expression,
                  alphaId: c.alphaId,
                  trainSharpe: c.trainSharpe,
                  testSharpe: c.testSharpe,
                }))}
              />
              {done && bestAlphaId(report.data.finalists) && (
                <PostrunAdvisor alphaId={bestAlphaId(report.data.finalists) as string} />
              )}
            </>
          ) : (
            !done && <Empty title="Still running — watch its progress above." />
          ))}

        {task.source === 'forge-workbench' && run.data && (
          <>
            <ResultList
              label="Candidates"
              rows={run.data.items.map((i) => ({
                expression: i.expression,
                alphaId: i.alphaId,
                trainSharpe: i.trainSharpe,
                testSharpe: i.testSharpe,
                note: i.status === 'failed' ? (i.error ?? 'failed') : undefined,
              }))}
            />
            {done && bestAlphaId(run.data.items) && (
              <PostrunAdvisor alphaId={bestAlphaId(run.data.items) as string} />
            )}
          </>
        )}

        {task.source === 'forge-workshop' && trial.data?.case && (
          <div className="flex flex-col gap-1">
            <p className="text-body-compact text-ink-subtle">Verdict by field</p>
            <ul className="flex flex-col gap-1">
              {Object.entries(trial.data.case['verdicts'] as Record<string, string>).map(
                ([field, verdict]) => (
                  <li key={field} className="num text-body-compact">
                    {field}: {verdict}
                  </li>
                ),
              )}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  )
}

/** The best-train-Sharpe alpha with a real BRAIN id among a task's rows, for the one Postrun
 * advisor shown per task — never a ranking UI of its own (CLAUDE.md rule 6: train only). */
function bestAlphaId(
  rows: readonly { alphaId: string | null; trainSharpe: number | null }[],
): string | null {
  const withId = rows.filter((r): r is { alphaId: string; trainSharpe: number | null } =>
    Boolean(r.alphaId),
  )
  if (withId.length === 0) return null
  return withId.reduce((best, r) =>
    (r.trainSharpe ?? -Infinity) > (best.trainSharpe ?? -Infinity) ? r : best,
  ).alphaId
}

interface Row {
  expression: string
  alphaId: string | null
  trainSharpe: number | null
  testSharpe: number | null
  note?: string | undefined
}

function ResultList({ label, rows }: { label: string; rows: Row[] }) {
  if (rows.length === 0) return <Empty title="No results yet." />
  return (
    <div className="flex flex-col gap-1">
      <p className="text-body-compact text-ink-subtle">{label}</p>
      <ul className="flex flex-col gap-1">
        {rows.map((r, i) => (
          <li
            key={`${r.alphaId ?? i}-${r.expression}`}
            className="flex flex-wrap items-center gap-2 text-body-compact"
          >
            <span className="num text-ink-subtle">{r.alphaId ?? '—'}</span>
            <code className="num break-all">{r.expression}</code>
            <span className="num">{fmt.ratio(r.trainSharpe)}</span>
            {r.testSharpe != null && (
              <span className="num text-ink-subtle">test {fmt.ratio(r.testSharpe)}</span>
            )}
            {r.note && <span className="text-pnl-negative-text">{r.note}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}
