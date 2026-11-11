/**
 * Results Breakdown: what a finished Template Lab task's alphas actually did, read from what
 * already ran — zero new simulations except the one opt-in repair below. Three sections:
 * pass rate & Sharpe by slice, why alphas failed and what's fixable, and a suggested submit
 * order by correlation. Nothing here submits an alpha (CLAUDE.md rule 1); "Queue repair" is the
 * only thing that can spend anything, and it shows the cost and remaining lane budget before
 * it does, then goes through the existing Submittable Board repair route and its Quota Governor
 * admission (CLAUDE.md rule 3) — the same lane the Board itself uses.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useState } from 'react'
import { fmt } from '@/lib/format'
import { board } from '@/screens/forge/board/api'
import { forgeQuota } from '@/screens/forge/today/api'
import { labTasks } from '@/screens/tasks/api'
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { Confirm, Select } from '@/ui/overlay'
import { DataTable } from '@/ui/table'
import {
  breakdown,
  type GroupBy,
  type NearMiss,
  type SettingName,
  type SubmitCandidate,
} from './api'

const GROUP_BY_ITEMS: { value: GroupBy; label: string }[] = [
  { value: 'field', label: 'Field' },
  { value: 'dataset', label: 'Dataset' },
  { value: 'setting', label: 'Setting' },
]
const SETTING_ITEMS: { value: SettingName; label: string }[] = [
  { value: 'neutralization', label: 'Neutralization' },
  { value: 'decay', label: 'Decay' },
  { value: 'universe', label: 'Universe' },
]

/** The repair-queue flow: show the exact cost and remaining lane budget before confirming,
 * then stage the alpha (a local write) and queue the repair (the one thing that spends). */
function RepairButton({ taskId, miss }: { taskId: number; miss: NearMiss }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()

  const estimate = useQuery({
    queryKey: ['forge-quota-estimate', 'validate'],
    queryFn: () => forgeQuota.estimate({ lane: 'validate', costEach: 1, simulations: 1 }),
    enabled: open,
  })

  const queue = useMutation({
    mutationFn: async () => {
      await breakdown.stage(taskId, miss.alphaId)
      return board.repair({ alphaId: miss.alphaId, repair: miss.repairKey })
    },
    onSuccess: () => {
      setOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['forge-breakdown', taskId] })
    },
  })

  return (
    <>
      <Button variant="ghost" onClick={() => setOpen(true)}>
        Queue repair
      </Button>
      <Confirm
        open={open}
        onOpenChange={setOpen}
        title={`Queue the ${miss.repairKey} repair for ${miss.alphaId}?`}
        confirmLabel="Queue repair"
        pending={queue.isPending}
        onConfirm={() => queue.mutate()}
      >
        <div className="flex flex-col gap-2">
          <p>{miss.why}</p>
          {estimate.isPending ? (
            <Skeleton className="h-6" />
          ) : estimate.data ? (
            <p className="num text-ink-subtle">
              Costs 1 simulation in the <strong>validate</strong> lane ·{' '}
              {fmt.int(estimate.data.laneRemaining)} remaining today
              {estimate.data.admitted < 1 && ' — the lane is full; this will be refused.'}
            </p>
          ) : null}
          {queue.isError && <ErrorNotice error={queue.error} title="Could not queue the repair" />}
        </div>
      </Confirm>
    </>
  )
}

function Slices({
  taskId,
  groupBy,
  setGroupBy,
  settingName,
  setSettingName,
}: {
  taskId: number
  groupBy: GroupBy
  setGroupBy: (g: GroupBy) => void
  settingName: SettingName
  setSettingName: (s: SettingName) => void
}) {
  const query = useQuery({
    queryKey: ['forge-breakdown', taskId, groupBy, settingName],
    queryFn: () => breakdown.get(taskId, { groupBy, settingName, minN: 3 }),
  })
  const data = query.data

  return (
    <Panel
      title="Pass Rate & Sharpe by Slice"
      description="Where this task's idea actually works: pass rate and median TRAIN/test Sharpe, grouped so one lucky run can't rank first alone (slices under 3 alphas are hidden)."
      actions={
        <div className="flex items-center gap-2">
          <Select value={groupBy} onChange={setGroupBy} items={GROUP_BY_ITEMS} label="Group by" />
          {groupBy === 'setting' && (
            <Select
              value={settingName}
              onChange={setSettingName}
              items={SETTING_ITEMS}
              label="Setting"
            />
          )}
        </div>
      }
    >
      {query.isError && <ErrorNotice error={query.error} title="Could not read the breakdown" />}
      {query.isPending ? (
        <Skeleton className="h-40" />
      ) : !data || data.slices.length === 0 ? (
        <Empty title="No slice has 3 or more alphas yet." />
      ) : (
        <DataTable
          label="Pass rate by slice"
          rows={data.slices}
          rowKey={(s) => s.key}
          columns={[
            { key: 'key', header: 'Key', width: 'minmax(140px,2fr)', cell: (s) => s.key },
            {
              key: 'n',
              header: 'N',
              width: 'minmax(60px,0.6fr)',
              align: 'right',
              cell: (s) => fmt.int(s.n),
            },
            {
              key: 'passRate',
              header: 'Pass %',
              width: 'minmax(80px,0.8fr)',
              align: 'right',
              cell: (s) => fmt.pct(s.passRate),
            },
            {
              key: 'medianTrainSharpe',
              header: 'Median TRAIN Sharpe',
              width: 'minmax(100px,1fr)',
              align: 'right',
              cell: (s) => fmt.ratio(s.medianTrainSharpe),
            },
            {
              key: 'medianTestSharpe',
              header: 'Median test Sharpe',
              width: 'minmax(100px,1fr)',
              align: 'right',
              cell: (s) => fmt.ratio(s.medianTestSharpe),
            },
          ]}
          maxHeight="40vh"
          empty="No Alphas back yet."
        />
      )}
    </Panel>
  )
}

function Failures({ taskId }: { taskId: number }) {
  const query = useQuery({
    queryKey: ['forge-breakdown-failures', taskId],
    queryFn: () => breakdown.get(taskId, { minN: 1 }),
  })
  const data = query.data

  return (
    <Panel
      title="Why Alphas Failed, and What's Fixable"
      description="Which check refused the most alphas, and the near misses: fails exactly one check, that check has a court.yaml repair, and the failing value is close to the limit — a real margin computed from BRAIN's own value and limit, not a guess."
    >
      {query.isPending ? (
        <Skeleton className="h-40" />
      ) : !data ? null : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {data.failures.length === 0 ? (
              <span className="text-ink-subtle">Nothing failed a gating check.</span>
            ) : (
              data.failures.map((f) => (
                <Badge key={f.check} tone="loss">
                  {f.check} × {fmt.int(f.count)}
                </Badge>
              ))
            )}
          </div>
          {data.nearMisses.length === 0 ? (
            <Empty title="No near misses: nothing failed just one repairable check within the margin." />
          ) : (
            <DataTable
              label="Near misses"
              rows={data.nearMisses}
              rowKey={(n) => n.alphaId}
              columns={[
                {
                  key: 'alphaId',
                  header: 'Alpha',
                  width: 'minmax(80px,1fr)',
                  cell: (n) => n.alphaId,
                },
                {
                  key: 'field',
                  header: 'Field',
                  width: 'minmax(100px,1.2fr)',
                  cell: (n) => n.field,
                },
                {
                  key: 'failedCheck',
                  header: 'Failed Check',
                  width: 'minmax(120px,1.4fr)',
                  cell: (n) => n.failedCheck,
                },
                {
                  key: 'margin',
                  header: 'Margin',
                  width: 'minmax(80px,0.8fr)',
                  align: 'right',
                  cell: (n) => fmt.pct(n.margin),
                },
                {
                  key: 'repair',
                  header: 'Repair',
                  width: 'minmax(140px,1.6fr)',
                  cell: (n) => <RepairButton taskId={taskId} miss={n} />,
                },
              ]}
              maxHeight="40vh"
              empty="No near misses."
            />
          )}
        </div>
      )}
    </Panel>
  )
}

function SubmitOrder({ taskId }: { taskId: number }) {
  const query = useQuery({
    queryKey: ['forge-breakdown-order', taskId],
    queryFn: () => breakdown.get(taskId, { minN: 1 }),
  })
  const data = query.data
  const order: SubmitCandidate[] = data?.submitOrder ?? []
  const uncheckedCount = order.filter((c) => !c.hasCheckData).length

  return (
    <Panel
      title="Submit Order by Correlation"
      description={`Passing alphas, one leader per family, ordered fewest-correlated-first against your real submitted pool — cumulatively, so picking #1 changes what counts as overlap for #2 onward. Nothing here submits: it is a read-only order, repair-queue aside. Flagged "high overlap" above ${fmt.pct(data?.poolCollisionCeiling ?? 0.7, 0)}. Ranked by local PnL correlation only. This approximates self-correlation but says nothing about BRAIN's production-correlation check, which has no local equivalent — always verify on the Board or Submit Helper before actually submitting.`}
    >
      {query.isPending ? (
        <Skeleton className="h-40" />
      ) : order.length === 0 ? (
        <Empty title="No passing alpha yet to order." />
      ) : (
        <>
          {uncheckedCount > 0 && (
            <Notice
              tone="warn"
              title="This order is PnL-only, not BRAIN's own check"
              className="mb-3"
            >
              {uncheckedCount} of {order.length} passing{' '}
              {uncheckedCount === 1 ? 'alpha hasn’t' : 'alphas haven’t'} had Check Submission
              (self/production correlation) fetched yet — fetch it on the Board or Submit Helper
              before you rely on this order to submit.
            </Notice>
          )}
          <DataTable
            label="Submit order"
            rows={order}
            rowKey={(c) => c.alphaId}
            columns={[
              {
                key: 'rank',
                header: '#',
                width: 'minmax(40px,0.4fr)',
                align: 'right',
                cell: (c) => fmt.int(order.indexOf(c) + 1),
              },
              {
                key: 'alphaId',
                header: 'Alpha',
                width: 'minmax(80px,1fr)',
                cell: (c) => c.alphaId,
              },
              {
                key: 'familySize',
                header: 'Family',
                width: 'minmax(70px,0.6fr)',
                align: 'right',
                cell: (c) => fmt.int(c.familySize),
              },
              {
                key: 'trainSharpe',
                header: 'TRAIN Sharpe',
                width: 'minmax(90px,0.9fr)',
                align: 'right',
                cell: (c) => fmt.ratio(c.trainSharpe),
              },
              {
                key: 'bestCorrelation',
                header: 'Best Correlation',
                width: 'minmax(100px,1fr)',
                align: 'right',
                cell: (c) =>
                  c.bestCorrelation == null ? (
                    <span className="text-ink-subtle">no PnL yet</span>
                  ) : (
                    <span className={c.highOverlap ? 'text-pnl-negative-text' : ''}>
                      {fmt.ratio(c.bestCorrelation)}
                      {c.bestCorrelationWith ? ` vs ${c.bestCorrelationWith}` : ''}
                    </span>
                  ),
              },
              {
                key: 'flag',
                header: '',
                width: 'minmax(120px,1fr)',
                cell: (c) =>
                  c.highOverlap ? <Badge tone="loss">High overlap, consider skipping</Badge> : null,
              },
            ]}
            maxHeight="40vh"
            empty="No passing alpha yet."
          />
        </>
      )}
    </Panel>
  )
}

export function ResultsBreakdownScreen() {
  const { taskId } = useParams({ from: '/forge/breakdown/$taskId' })
  const id = Number(taskId)
  const [groupBy, setGroupBy] = useState<GroupBy>('field')
  const [settingName, setSettingName] = useState<SettingName>('neutralization')

  const tasksQuery = useQuery({ queryKey: ['lab-tasks'], queryFn: labTasks.list })
  const task = tasksQuery.data?.tasks.find((t) => t.id === id)

  return (
    <Page>
      <PageHeader
        title={task ? `Results Breakdown · ${task.name || task.templateName}` : `Results Breakdown`}
        description="Three views over results you already paid for: nothing here spends a simulation, except one opt-in repair you confirm first."
        actions={
          <Button
            variant="ghost"
            render={<Link to="/tasks/$taskId" params={{ taskId: String(id) }} />}
          >
            <ArrowLeftIcon />
            Back to Task
          </Button>
        }
      />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric boxed label="Total Alphas" value={fmt.int(task?.simulated ?? 0)} />
      </div>
      <Slices
        taskId={id}
        groupBy={groupBy}
        setGroupBy={setGroupBy}
        settingName={settingName}
        setSettingName={setSettingName}
      />
      <Failures taskId={id} />
      <SubmitOrder taskId={id} />
    </Page>
  )
}
