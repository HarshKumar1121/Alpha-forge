/**
 * The Submittable Board: recent alphas clustered into families (same bet, by PnL correlation or,
 * lacking that, by template+field), grouped by day then alpha type, each led by its best TRAIN
 * Sharpe. For every leader: BRAIN's own check verdict, correlation against the ceiling it would
 * actually be judged on, and — for a non-ready leader — why not, with a one-click repair queued
 * into the validate lane. A ranked checklist closes it out. Nothing here submits an alpha; that
 * stays a manual step on the BRAIN website (CLAUDE.md rule 1). "Fetch latest" only reads from
 * BRAIN (pnl, checks, correlation, before/after) — it never spends a simulation.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import type { CheckResult } from '@/api/types'
import { fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import { OpenInBrain } from '@/screens/pool/shared'
import {
  Badge,
  Button,
  checkTone,
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
  type Tone,
} from '@/ui/kit'
import {
  type AlphaOut,
  type Board,
  board,
  type ChecklistItem,
  type CheckOut,
  type DayGroup,
  type FamilyOut,
  type RepairOut,
  type TypeGroup,
} from './api'

const STATUS_TONE: Record<AlphaOut['status'], Tone> = {
  ready: 'profit',
  pending: 'warn',
  not_ready: 'loss',
  unchecked: 'muted',
}

const STATUS_LABEL: Record<AlphaOut['status'], string> = {
  ready: 'Ready',
  pending: 'Pending',
  not_ready: 'Not ready',
  unchecked: 'Unchecked',
}

export function BoardScreen() {
  const [days, setDays] = useState(2)
  const [fetchTaskId, setFetchTaskId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const query = useQuery({ queryKey: ['forge-board', days], queryFn: () => board.get(days) })

  const tasksLive = useLive((s) => s.tasks)
  const fetchTask = tasksLive?.tasks.find((t) => t.id === fetchTaskId) ?? null
  const fetching = fetchTask != null && fetchTask.state === 'running'

  const fetchMutation = useMutation({
    mutationFn: () => board.fetch({ days }),
    onSuccess: (started) => setFetchTaskId(started.taskId),
  })

  // The fetch task just finished: pull in whatever it filled, then stop watching it.
  useEffect(() => {
    if (fetchTask && fetchTask.state !== 'running') {
      void queryClient.invalidateQueries({ queryKey: ['forge-board'] })
      setFetchTaskId(null)
    }
  }, [fetchTask, queryClient])

  const repairMutation = useMutation({
    mutationFn: board.repair,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forge-board'] })
    },
  })

  return (
    <Page>
      <PageHeader
        title="Submittable Board"
        description="What's ready to submit, grouped by day, alpha type and family — and why anything else isn't. Nothing here submits an alpha; submission stays a manual step on the BRAIN website."
      />

      <Panel
        title="Range"
        description="What this tells you: how many days back the board looks, today included. Fetching only reads BRAIN's own PnL, checks, correlation and before/after figures for what's already here — it never spends a simulation."
        bodyClassName="flex flex-wrap items-end gap-4 p-4"
      >
        <Field label="Days back" hint="1 to 14 days, today included." className="w-40">
          <Input
            type="number"
            min={1}
            max={14}
            value={days}
            onChange={(e) => {
              const n = Number(e.target.value)
              if (n >= 1 && n <= 14) setDays(n)
            }}
          />
        </Field>
        <Button
          variant="secondary"
          disabled={fetching}
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ['forge-board'] })
            fetchMutation.mutate()
          }}
        >
          {fetching ? 'Fetching…' : 'Fetch latest from BRAIN'}
        </Button>
        {fetchMutation.isError && (
          <ErrorNotice error={fetchMutation.error} title="Could not start the fetch" />
        )}
      </Panel>

      {fetchTask && fetchTask.state === 'running' && (
        <Panel bodyClassName="flex flex-col gap-2 p-4">
          <Progress value={fetchTask.progress ?? null} label="Fetching" />
          <p className="text-body-compact text-ink-subtle">{fetchTask.detail || 'Starting…'}</p>
        </Panel>
      )}

      {query.isError && <ErrorNotice error={query.error} title="The board could not load" />}
      {query.isPending && <Skeleton className="h-48" label="Loading the board" />}

      {query.data && (
        <BoardBody
          boardData={query.data}
          onRepair={(alphaId, repair) =>
            repairMutation.mutate(repair ? { alphaId, repair } : { alphaId })
          }
          repairingId={repairMutation.isPending ? repairMutation.variables?.alphaId : undefined}
          repairError={repairMutation.isError ? repairMutation.error : null}
        />
      )}
    </Page>
  )
}

function BoardBody({
  boardData,
  onRepair,
  repairingId,
  repairError,
}: {
  boardData: Board
  onRepair: (alphaId: string, repair?: string) => void
  repairingId: string | undefined
  repairError: unknown
}) {
  const nothingMissing =
    boardData.missing.pnl === 0 &&
    boardData.missing.checks === 0 &&
    boardData.missing.correlations === 0 &&
    boardData.missing.performance === 0

  if (boardData.days.length === 0) {
    return (
      <Empty title="Nothing on the board yet">
        Run a pipeline first — the board groups what pipelines have already produced into families.
        See Pipelines to start one.
      </Empty>
    )
  }

  return (
    <>
      {!nothingMissing && (
        <Notice tone="info" title="Some figures are missing">
          {boardData.missing.pnl > 0 && `${fmt.int(boardData.missing.pnl)} alpha(s) lack PnL. `}
          {boardData.missing.checks > 0 &&
            `${fmt.int(boardData.missing.checks)} leader(s) lack a BRAIN check. `}
          {boardData.missing.correlations > 0 &&
            `${fmt.int(boardData.missing.correlations)} leader(s) lack correlation. `}
          {boardData.missing.performance > 0 &&
            `${fmt.int(boardData.missing.performance)} leader(s) lack before/after figures. `}
          Fetch latest from BRAIN to fill these in.
        </Notice>
      )}

      {repairError != null && (
        <ErrorNotice error={repairError} title="The repair could not queue" />
      )}

      {boardData.days.map((day) => (
        <DayPanel key={day.day} day={day} onRepair={onRepair} repairingId={repairingId} />
      ))}

      <ChecklistPanel checklist={boardData.checklist} />
    </>
  )
}

function DayPanel({
  day,
  onRepair,
  repairingId,
}: {
  day: DayGroup
  onRepair: (alphaId: string, repair?: string) => void
  repairingId: string | undefined
}) {
  return (
    <Panel title={day.day} bodyClassName="flex flex-col gap-4 p-4">
      {day.types.map((type) => (
        <TypeSection
          key={type.alphaType}
          type={type}
          onRepair={onRepair}
          repairingId={repairingId}
        />
      ))}
    </Panel>
  )
}

function TypeSection({
  type,
  onRepair,
  repairingId,
}: {
  type: TypeGroup
  onRepair: (alphaId: string, repair?: string) => void
  repairingId: string | undefined
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 border-b border-hairline-subtle pb-1">
        <h3 className="text-body-strong text-ink">{type.alphaType}</h3>
        <Badge tone={type.ready > 0 ? 'profit' : 'muted'}>
          {fmt.int(type.ready)} / {fmt.int(type.alphas)} ready
        </Badge>
      </div>
      <div className="flex flex-col gap-3">
        {type.families.map((family) => (
          <FamilyCard
            key={family.id}
            family={family}
            onRepair={onRepair}
            repairingId={repairingId}
          />
        ))}
      </div>
    </div>
  )
}

function FamilyCard({
  family,
  onRepair,
  repairingId,
}: {
  family: FamilyOut
  onRepair: (alphaId: string, repair?: string) => void
  repairingId: string | undefined
}) {
  const [showMembers, setShowMembers] = useState(false)
  const leader = family.leader

  return (
    <div className="rounded-md border border-hairline bg-surface-2 p-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-body-compact text-ink-subtle">
        <span>{family.comparisonSet}</span>
        <span>·</span>
        <span>
          {family.basis === 'pnl' ? 'Grouped by PnL correlation' : 'Grouped by template and field'}
        </span>
      </div>
      <AlphaRow alpha={leader} onRepair={onRepair} repairing={repairingId === leader.alphaId} />
      {family.members.length > 1 && (
        <div className="mt-2">
          <Button size="sm" variant="ghost" onClick={() => setShowMembers((v) => !v)}>
            {showMembers ? 'Hide' : 'Show'} {fmt.int(family.members.length - 1)} other member(s)
          </Button>
          {showMembers && (
            <div className="mt-2 flex flex-col gap-2 border-t border-hairline-subtle pt-2">
              {family.members
                .filter((m) => m.alphaId !== leader.alphaId)
                .map((m) => (
                  <div key={m.alphaId} className="text-body-compact text-ink-subtle">
                    <code className="num text-ink">{m.alphaId}</code>
                    {m.leaderCorrelation != null && (
                      <span> — correlation to leader {fmt.ratio(m.leaderCorrelation)}</span>
                    )}
                    {m.whyNot[0] && <span> — {m.whyNot[0]}</span>}
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AlphaRow({
  alpha,
  onRepair,
  repairing,
}: {
  alpha: AlphaOut
  onRepair: (alphaId: string, repair?: string) => void
  repairing: boolean
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="num block min-w-0 break-all text-body-compact text-ink">
          {alpha.expression}
        </code>
        <div className="flex shrink-0 items-center gap-2">
          {alpha.powerPool && <Badge tone="neutral">Power Pool</Badge>}
          <Badge tone={STATUS_TONE[alpha.status]}>{STATUS_LABEL[alpha.status]}</Badge>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric
          size="sm"
          label="Train Sharpe"
          value={fmt.ratio(alpha.trainSharpe)}
          hint="Used to rank and lead this family"
        />
        <Metric
          size="sm"
          label="Test Sharpe"
          value={fmt.ratio(alpha.testSharpe)}
          hint="Held-out years — shown, never used to rank"
        />
        {alpha.correlation && (
          <Metric
            size="sm"
            label={alpha.correlation.kind === 'power-pool' ? 'Power Pool corr.' : 'Self corr.'}
            value={fmt.ratio(alpha.correlation.max)}
            tone={alpha.correlation.over ? 'loss' : 'neutral'}
            hint={`Ceiling ${fmt.ratio(alpha.correlation.ceiling)}`}
          />
        )}
        {alpha.adds && (
          <Metric
            size="sm"
            label="Adds to pool"
            value={
              alpha.adds.scoreDelta != null
                ? `${alpha.adds.scoreDelta > 0 ? '+' : ''}${fmt.ratio(alpha.adds.scoreDelta)}`
                : fmt.ratio(null)
            }
            tone={
              alpha.adds.scoreDelta == null
                ? 'neutral'
                : alpha.adds.scoreDelta > 0
                  ? 'profit'
                  : 'loss'
            }
            hint="BRAIN's own before/after score change"
          />
        )}
      </div>

      {alpha.checks.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {alpha.checks.map((c, i) => (
            <CheckChip key={`${c.name}-${i}`} check={c} />
          ))}
        </div>
      )}

      {alpha.description && (
        <div className="rounded-sm border border-hairline-subtle bg-canvas p-2 text-body-compact text-ink-subtle">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-ink">Description draft</span>
            {alpha.description.edited && <Badge tone="neutral">Edited</Badge>}
          </div>
          <p>{alpha.description.text}</p>
        </div>
      )}

      {alpha.whyNot.length > 0 && (
        <Notice tone={alpha.status === 'pending' ? 'warn' : 'error'} title="Why not submittable">
          <ul className="ml-4 list-disc">
            {alpha.whyNot.map((reason, i) => (
              <li key={i} className="text-body-compact">
                {reason}
              </li>
            ))}
          </ul>
          {alpha.repairs.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {alpha.repairs.map((r) => (
                <RepairAction
                  key={r.check}
                  repair={r}
                  onQueue={(key) => onRepair(alpha.alphaId, key)}
                  queuing={repairing}
                />
              ))}
            </div>
          )}
        </Notice>
      )}

      <div className="flex flex-wrap gap-2 border-t border-hairline-subtle pt-2">
        <OpenInBrain url={alpha.brainUrl} />
        <Button size="sm" render={<Link to="/forge/analytics" search={{ alpha: alpha.alphaId }} />}>
          Analytics
        </Button>
        {alpha.repairsQueued > 0 && (
          <span className="self-center text-body-compact text-ink-subtle">
            {fmt.int(alpha.repairsQueued)} repair(s) queued
          </span>
        )}
      </div>
    </div>
  )
}

function RepairAction({
  repair,
  onQueue,
  queuing,
}: {
  repair: RepairOut
  onQueue: (key?: string) => void
  queuing: boolean
}) {
  if (!repair.key) {
    return (
      <p className="text-body-compact text-ink-subtle">
        {repair.check}: {repair.why}
      </p>
    )
  }
  return (
    <Button
      size="sm"
      variant="secondary"
      disabled={queuing}
      onClick={() => onQueue(repair.key ?? undefined)}
    >
      {queuing ? 'Queuing…' : `Queue repair: ${repair.key}`}
    </Button>
  )
}

function CheckChip({ check }: { check: CheckOut }) {
  const tone = checkTone(check.result as CheckResult)
  const figure =
    typeof check.value === 'number'
      ? `${check.value}${typeof check.limit === 'number' ? ` / ${check.limit}` : ''}`
      : null
  return (
    <Badge tone={tone}>
      {check.name}
      {figure && <span className="text-ink-subtle"> {figure}</span>}
    </Badge>
  )
}

function ChecklistPanel({ checklist }: { checklist: ChecklistItem[] }) {
  return (
    <Panel
      title="Submission checklist"
      description="What this tells you: the manual order to submit in, ranked by what each alpha ADDS to the pool, not raw Sharpe. Submit on BRAIN, then move to the next one — each submission moves self-correlation for the rest."
      bodyClassName="flex flex-col divide-y divide-hairline p-4"
    >
      {checklist.length === 0 ? (
        <Empty title="Nothing is submittable yet">
          Every family's leader still has a failing or pending check. Fix the "why not" reasons
          above, or fetch latest from BRAIN if anything looks stale.
        </Empty>
      ) : (
        checklist.map((item) => <ChecklistRow key={item.alphaId} item={item} />)
      )}
    </Panel>
  )
}

function ChecklistRow({ item }: { item: ChecklistItem }) {
  return (
    <div className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-body-strong text-ink">{item.rank}.</span>
        <code className="num min-w-0 break-all text-body-compact text-ink">{item.expression}</code>
        {item.powerPool && <Badge tone="neutral">Power Pool</Badge>}
      </div>
      <div className="flex flex-wrap gap-3 text-body-compact text-ink-subtle">
        <span>train sharpe {fmt.ratio(item.trainSharpe)}</span>
        <span>adds {fmt.ratio(item.scoreDelta)}</span>
        <OpenInBrain url={item.brainUrl} />
      </div>
      <ol className="ml-4 list-decimal text-body-compact text-ink-subtle">
        {item.steps.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>
    </div>
  )
}
