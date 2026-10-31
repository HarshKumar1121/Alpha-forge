/**
 * Today (Forge home): where BRAIN's daily simulations go, lane by lane.
 *
 * Read from the Quota Governor (`/api/forge/quota`). Every figure carries a caption or a
 * tooltip, and every chart a one-line "what this tells you": the screen is also a lesson in how
 * a fixed research budget is shared out.
 */

import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { fmt } from '@/lib/format'
import { useNow } from '@/lib/now'
import { useRefetchOn } from '@/lib/ws'
import {
  Empty,
  ErrorNotice,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Progress,
  Skeleton,
} from '@/ui/kit'
import { Tooltip } from '@/ui/overlay'
import { forgeQuota, type QuotaHour, type QuotaLane, type QuotaToday } from './api'

export function TodayScreen() {
  const quota = useQuery({
    queryKey: ['forge-quota'],
    queryFn: forgeQuota.today,
    refetchInterval: 30_000,
  })
  useRefetchOn('simulations', ['forge-quota'], 3000)
  const q = quota.data

  return (
    <Page>
      <PageHeader
        title="Today"
        description="Where today's BRAIN simulations go. Each lane is a purpose with its own share of the day, so no single pipeline can spend the budget the others need."
      />
      {quota.isError && <ErrorNotice error={quota.error} title="Today's quota could not load" />}
      {!q && !quota.isError && <Skeleton className="h-40" label="Loading today's quota" />}
      {q && (
        <>
          {q.marginWarning && (
            <Notice tone="warn" title="Into the safety margin">
              Only {fmt.int(Math.max(0, q.limit - q.spent - q.queued))} simulations are left before
              BRAIN's daily limit. The governor keeps the last {fmt.int(q.safetyMargin)} for retries
              and work you do by hand, so planned work now waits for the reset.
            </Notice>
          )}
          <Gauge q={q} updatedAt={quota.dataUpdatedAt} />
          <Lanes q={q} />
          <Hourly q={q} />
        </>
      )}
    </Page>
  )
}

function Gauge({ q, updatedAt }: { q: QuotaToday; updatedAt: number }) {
  // Seconds since the fetch, so the reset countdown ticks between polls (as the header's does).
  const elapsed = Math.max(0, Math.floor((useNow(1000) - updatedAt) / 1000))
  const approx = q.exact ? '' : '~'
  return (
    <Panel
      title="Today's quota"
      description="What this tells you: how much of BRAIN's daily allowance is spent, and how far the day is from the safety margin (the tick)."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <Progress
        value={q.limit ? q.spent / q.limit : 0}
        label="Simulations spent today"
        mark={{
          at: q.limit ? q.budget / q.limit : 1,
          label: `Safety margin starts at ${fmt.int(q.budget)}: the governor never plans past it`,
        }}
      />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
        <Metric
          label="Spent"
          value={`${approx}${fmt.int(q.spent)} / ${fmt.int(q.limit)}`}
          hint={
            q.exact
              ? "BRAIN's own count from today's last simulation"
              : 'Counted locally until BRAIN reports its own figure'
          }
        />
        <Metric
          label="Budget"
          value={fmt.int(q.budget)}
          hint={`Daily limit minus the ${fmt.int(q.safetyMargin)}-simulation safety margin`}
        />
        <Metric
          label="Remaining"
          value={fmt.int(q.remaining)}
          tone={q.remaining ? 'neutral' : 'warn'}
          hint="Budget left after what is spent and what is already queued"
        />
        <Metric
          label="Queued"
          value={fmt.int(q.queued)}
          hint="Waiting in the engine; sent as lanes and pacing allow"
        />
        <Metric
          label="Cache hits"
          value={fmt.int(q.cached)}
          hint="Identical requests answered from earlier results, at no cost"
        />
        <Metric
          label="Resets in"
          value={fmt.countdown(Math.max(0, q.resetsInSeconds - elapsed))}
          hint="BRAIN's day ends at midnight US Eastern"
        />
      </div>
      {q.unrecorded > 0 && (
        <p className="text-body-compact text-ink-subtle">
          BRAIN counts {fmt.int(q.unrecorded)} more simulations than this app sent today (run on the
          website, perhaps). They are charged to reserve, since nobody else here spent them.
        </p>
      )}
    </Panel>
  )
}

const laneName = (lane: string) => {
  const words = lane.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function Lanes({ q }: { q: QuotaToday }) {
  return (
    <Panel
      title="Lanes"
      description={`What this tells you: each lane's spend against what it may use now. A lane starts the day with its share; ${fmt.pct(q.dayElapsed, 0)} of the day has passed, so that much of every idle lane's share has been released to busy ones.`}
      bodyClassName="flex flex-col divide-y divide-hairline"
    >
      {q.lanes.map((lane) => (
        <LaneRow key={lane.lane} lane={lane} />
      ))}
    </Panel>
  )
}

function LaneRow({ lane }: { lane: QuotaLane }) {
  const cap = lane.effectiveCap
  return (
    <div className="grid grid-cols-1 gap-2 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] lg:items-center lg:gap-6">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-body font-medium text-ink">{laneName(lane.lane)}</span>
        <span className="text-body-compact text-pretty text-ink-subtle">{lane.about}</span>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <Progress
          value={cap ? lane.spent / cap : 0}
          label={`${laneName(lane.lane)}: spent of what it may use now`}
        />
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-body-compact text-ink-subtle">
          <Figure
            label="spent"
            value={`${fmt.int(lane.spent)} / ${fmt.int(cap)}`}
            tip="Simulations this lane spent today, of what it may use right now (its share plus quota idle lanes released to it)"
          />
          <Figure
            label="share"
            value={`${fmt.int(lane.planned)} (${fmt.pct(lane.share, 0)})`}
            tip="What the lane was promised at the start of BRAIN's day: its share of the budget"
          />
          <Figure
            label="queued"
            value={fmt.int(lane.queued)}
            tip="Waiting in the engine for this lane; it keeps a claim on the lane's share"
          />
          <Figure
            label="cache hits"
            value={fmt.int(lane.cached)}
            tip="Requests answered from earlier results today, at no cost"
          />
        </div>
      </div>
    </div>
  )
}

function Figure({ label, value, tip }: { label: string; value: ReactNode; tip: string }) {
  return (
    <Tooltip content={tip}>
      <span className="cursor-help">
        <span className="num text-ink">{value}</span> {label}
      </span>
    </Tooltip>
  )
}

function Hourly({ q }: { q: QuotaToday }) {
  const idle = q.spent === 0 && q.cached === 0 && q.queued === 0
  return (
    <Panel
      title="Hourly spend"
      description={`What this tells you: whether the day is paced or bursty. Hours are on your clock (${q.timezone}); the line is the ${fmt.int(q.pacing.maxPerHour)}-an-hour pacing cap.`}
      bodyClassName="p-4"
    >
      {idle ? (
        <Empty title="No simulations yet today">
          Run a task from any lab screen. Its simulations are charged to the reserve lane and show
          up here and above.
        </Empty>
      ) : (
        <HourBars hours={q.hourly} cap={q.pacing.maxPerHour} lastHour={q.pacing.lastHour} />
      )}
      {q.pacing.quietNow && (
        <p className="mt-3 text-body-compact text-ink-subtle">
          Quiet hours now ({q.pacing.quietHours.join(', ')}): nothing is sent until they end.
        </p>
      )}
    </Panel>
  )
}

function HourBars({ hours, cap, lastHour }: { hours: QuotaHour[]; cap: number; lastHour: number }) {
  const top = Math.max(cap, ...hours.map((h) => h.spent), 1)
  return (
    <figure aria-label="Simulations spent per hour today" className="flex min-w-0 flex-col gap-2">
      <div role="list" className="relative flex h-40 min-w-0 items-end gap-1">
        <div
          className="absolute inset-x-0 border-t border-dashed border-ink-tertiary"
          style={{ bottom: `${(cap / top) * 100}%` }}
          aria-hidden
        />
        {hours.map((h) => (
          <div
            key={h.hour}
            role="listitem"
            aria-label={`${h.hour}: ${h.spent} simulations`}
            title={`${h.hour}: ${fmt.int(h.spent)} simulations`}
            className="flex h-full min-w-0 flex-1 items-end"
          >
            <div
              className={cn('w-full rounded-xs', h.spent ? 'bg-primary' : 'bg-surface-3')}
              style={{ height: `${Math.max((h.spent / top) * 100, 1)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="flex min-w-0 justify-between text-caption text-ink-subtle">
        <span>{hours[0]?.hour}</span>
        <span>{hours.at(-1)?.hour}</span>
      </div>
      <figcaption className="text-body-compact text-ink-subtle">
        <span className="num text-ink">{fmt.int(lastHour)}</span> sent in the last 60 minutes, of{' '}
        {fmt.int(cap)} allowed.
      </figcaption>
    </figure>
  )
}
