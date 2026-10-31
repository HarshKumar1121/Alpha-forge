/**
 * Field sets: named selections of data fields, each tied to the market it was picked in.
 *
 * This is where a research idea starts. A template binds to a field set, so the set decides
 * which real fields fill its slots and which market they are simulated in.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { type Scope, scopeLabel } from '@/api/types'
import { DASH, fmt } from '@/lib/format'
import { describeFilter } from '@/screens/data/state'
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  LINK,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { Confirm, Sheet, Tooltip } from '@/ui/overlay'
import { type FieldSetOut, fieldSets } from '../data/api'
import { ForgeScopePicker, useForgeScope } from '../scope/picker'

export function FieldSetsScreen() {
  const [scope, setScope] = useForgeScope()
  const [openId, setOpenId] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['forge-fieldsets'], queryFn: fieldSets.list })

  return (
    <Page>
      <PageHeader
        title="Field sets"
        description="Named selections of real BRAIN fields, each with the market it was picked in. A template binds to a set: the set decides which fields fill its slots."
      />
      {list.isError && <ErrorNotice error={list.error} title="Could not load your field sets" />}
      {list.isPending && <Skeleton className="h-40" label="Loading field sets" />}
      {list.data?.length === 0 && (
        <Panel title="No field sets yet">
          <Empty title="Start from the data" icon={<PlusIcon />}>
            Open the{' '}
            <Link to="/data" className={LINK}>
              Data Explorer
            </Link>
            , narrow a market down to the fields behind one idea, tick them, and choose "Save as
            field set". Five to ten fields from one dataset is a good first set.
          </Empty>
        </Panel>
      )}
      {list.data && list.data.length > 0 && (
        <div className="flex flex-col gap-4">
          {Object.entries(groupByScope(list.data)).map(([label, sets]) => (
            <Panel
              key={label}
              title={<span className="num">{label}</span>}
              description={`What this tells you: the selections you saved in ${label}. Open one to see the fields it holds now.`}
              bodyClassName="flex flex-col divide-y divide-hairline p-0"
            >
              {sets.map((s) => (
                <SetRow key={s.id} set={s} onOpen={() => setOpenId(s.id)} />
              ))}
            </Panel>
          ))}
        </div>
      )}
      <Panel
        title="The market Forge is pointed at"
        description="What this tells you: which instrument type, region, delay and universe your account can run, straight from BRAIN's own simulation options."
      >
        <ForgeScopePicker scope={scope} onChange={setScope} showNeutralizations />
        <p className="mt-3 text-body-compact text-ink-subtle">
          Currently {scopeLabel(scope)}. Field sets are saved in the market the Data Explorer was
          showing when you saved them.
        </p>
      </Panel>
      <FieldSetSheet id={openId} onClose={() => setOpenId(null)} />
    </Page>
  )
}

const groupByScope = (sets: FieldSetOut[]): Record<string, FieldSetOut[]> => {
  const groups: Record<string, FieldSetOut[]> = {}
  for (const s of sets) {
    const label = scopeLabel(s.scope as Scope)
    const group = groups[label] ?? []
    group.push(s)
    groups[label] = group
  }
  return groups
}

function SetRow({ set, onOpen }: { set: FieldSetOut; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-body font-medium text-ink">{set.name}</span>
        <span className="block truncate text-body-compact text-ink-subtle">
          {set.notes || (set.kind === 'dynamic' ? 'A saved filter, re-run on every read.' : DASH)}
        </span>
      </span>
      <Tooltip
        content={
          set.kind === 'dynamic'
            ? 'Dynamic: the saved filter runs again on every read, so new BRAIN fields that match join the set.'
            : 'Manual: exactly the fields you ticked, frozen.'
        }
      >
        <Badge tone="outline">{set.kind}</Badge>
      </Tooltip>
      <span className="num text-body-compact text-ink-subtle">
        {set.kind === 'manual' ? `${fmt.int(set.fieldIds.length)} fields` : 'filter'}
      </span>
    </button>
  )
}

function FieldSetSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const client = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const detail = useQuery({
    queryKey: ['forge-fieldset', id],
    queryFn: () => fieldSets.get(id as number),
    enabled: id != null,
  })
  const d = detail.data

  const remove = useMutation({
    mutationFn: () => fieldSets.remove(id as number),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['forge-fieldsets'] })
      toast.success('Field set deleted')
      setConfirming(false)
      onClose()
    },
    onError: (e) => toast.error('Could not delete it', { description: errorMessage(e) }),
  })

  return (
    <Sheet
      open={id != null}
      onOpenChange={(open) => !open && onClose()}
      title={d?.name ?? 'Field set'}
      description={d ? `${scopeLabel(d.scope as Scope)} — ${d.kind}` : 'Loading…'}
      actions={
        d && (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
            <Trash2Icon aria-hidden />
            Delete
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {detail.isError && <ErrorNotice error={detail.error} title="Could not load this set" />}
        {detail.isPending && id != null && <Skeleton className="h-40" />}
        {d && (
          <>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Metric
                label="Fields"
                value={fmt.int(d.fields.length)}
                hint="Held in this market now"
              />
              <Metric
                label="Matching"
                value={fmt.int(d.total)}
                hint={
                  d.kind === 'dynamic'
                    ? 'What the filter matches today'
                    : 'Picks found in the catalog'
                }
              />
              <Metric
                label="Kind"
                value={d.kind}
                hint={d.kind === 'dynamic' ? 'A saved filter' : 'Frozen picks'}
              />
            </div>
            {d.notes && <p className="text-body text-pretty text-ink-muted">{d.notes}</p>}
            {d.kind === 'dynamic' && d.filter && (
              <p className="text-body-compact text-ink-subtle">
                Filter:{' '}
                {describeFilter(d.filter, d.scope.region).join(', ') || 'everything in this market'}
              </p>
            )}
            {d.missing.length > 0 && (
              <Notice
                tone="warn"
                title={`${fmt.int(d.missing.length)} fields are not in this market`}
              >
                <span className="num">{d.missing.join(', ')}</span> — either the market is not
                synced, or BRAIN no longer offers them here. A template bound to this set will skip
                them.
              </Notice>
            )}
            <section className="flex flex-col gap-2">
              <h3 className="text-title">Fields</h3>
              {d.fields.length === 0 ? (
                <Empty title="Nothing resolves here">
                  Sync this market in BRAIN › Sync, or widen the filter.
                </Empty>
              ) : (
                <ul className="flex flex-col divide-y divide-hairline">
                  {d.fields.map((f) => (
                    <li key={f.field_id} className="flex flex-col gap-0.5 py-2">
                      <span className="flex flex-wrap items-baseline gap-2">
                        <span className="num text-body text-ink">{f.field_id}</span>
                        <span className="num text-body-compact text-ink-subtle">
                          {f.dataset_id ?? DASH} · {f.field_type ?? DASH} · {fmt.pct(f.coverage, 0)}{' '}
                          coverage
                        </span>
                      </span>
                      <span className="text-body-compact text-pretty text-ink-subtle">
                        {f.description ?? DASH}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
      <Confirm
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete "${d?.name ?? ''}"?`}
        confirmLabel="Delete"
        danger
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        The selection is removed from this app. Nothing on BRAIN changes.
      </Confirm>
    </Sheet>
  )
}
