/**
 * Data Nuggets: 2+ fields, a written rationale and a falsifiable claim, saved so a template
 * can bind to them instead of raw fields.
 *
 * The pairing suggestion engine says which pairs look structurally sane (same dataset,
 * mean/std and similar statistic pairs) — advisory only. The rationale and the claim are
 * always the user's own words: nothing here writes them.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { type Scope, scopeLabel } from '@/api/types'
import { DASH, fmt } from '@/lib/format'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
  Textarea,
} from '@/ui/kit'
import { Confirm, Dialog, Sheet } from '@/ui/overlay'
import { type NuggetOut, nuggets } from './api'

export function NuggetsScreen() {
  const [scope, setScope] = useForgeScope()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const list = useQuery({ queryKey: ['forge-nuggets', 'list'], queryFn: () => nuggets.list() })

  return (
    <Page>
      <PageHeader
        title="Data Nuggets"
        description="A handful of related fields, with the economic reasoning behind pairing them written down — not just the fields a template binds to, but why they belong together."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <PlusIcon aria-hidden />
            New nugget
          </Button>
        }
      />
      {list.isError && <ErrorNotice error={list.error} title="Could not load your nuggets" />}
      {list.isPending && <Skeleton className="h-40" label="Loading nuggets" />}
      {list.data?.length === 0 && (
        <Panel title="No nuggets yet">
          <Empty title="Start from a pair of related fields" icon={<PlusIcon />}>
            A consensus estimate and its own standard deviation, an actual and its estimate, a
            numerator and its denominator — two or more fields that measure one idea together.
            Template Studio can bind a template to a nugget instead of a raw field set.
          </Empty>
        </Panel>
      )}
      {list.data && list.data.length > 0 && (
        <Panel
          title="Your nuggets"
          description="What this tells you: every nugget you have saved. Open one to see its fields and resolved data treatment."
          bodyClassName="flex flex-col divide-y divide-hairline p-0"
        >
          {list.data.map((n) => (
            <NuggetRow key={n.nuggetId} nugget={n} onOpen={() => setOpenId(n.nuggetId)} />
          ))}
        </Panel>
      )}
      <Panel
        title="The market Forge is pointed at"
        description="What this tells you: which instrument type, region, delay and universe a new nugget is built in."
      >
        <ForgeScopePicker scope={scope} onChange={setScope} />
        <p className="mt-3 text-body-compact text-ink-subtle">
          Currently {scopeLabel(scope)}. A nugget is tied to the market it was built in, because a
          field id means different data elsewhere.
        </p>
      </Panel>
      <NewNuggetDialog open={creating} onOpenChange={setCreating} scope={scope} />
      <NuggetSheet nuggetId={openId} onClose={() => setOpenId(null)} />
    </Page>
  )
}

function NuggetRow({ nugget, onOpen }: { nugget: NuggetOut; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-body font-medium text-ink">{nugget.name}</span>
        <span className="block truncate text-body-compact text-ink-subtle">
          {nugget.claim || DASH}
        </span>
      </span>
      <span className="num text-body-compact text-ink-subtle">
        {scopeLabel(nugget.scope as Scope)}
      </span>
      <Badge tone="outline">{fmt.int(nugget.fieldIds.length)} fields</Badge>
    </button>
  )
}

function NewNuggetDialog({
  open,
  onOpenChange,
  scope,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  scope: Scope
}) {
  const client = useQueryClient()
  const [name, setName] = useState('')
  const [fieldIds, setFieldIds] = useState<string[]>([])
  const [rationale, setRationale] = useState('')
  const [claim, setClaim] = useState('')

  const suggest = useQuery({
    queryKey: ['forge-nuggets', 'suggest', scope, fieldIds],
    queryFn: () => nuggets.suggest(scope, fieldIds),
    enabled: fieldIds.length >= 2,
  })

  const reset = () => {
    setName('')
    setFieldIds([])
    setRationale('')
    setClaim('')
  }

  const save = useMutation({
    mutationFn: () =>
      nuggets.create({ name, scope, fieldIds, rationale, claim, allowDuplicates: false }),
    onSuccess: (made) => {
      void client.invalidateQueries({ queryKey: ['forge-nuggets'] })
      toast.success(`Saved "${made.name}"`, { description: scopeLabel(scope) })
      reset()
      onOpenChange(false)
    },
    onError: (e) => toast.error('Could not save the nugget', { description: errorMessage(e) }),
  })

  const ready = name.trim() && fieldIds.length >= 2 && rationale.trim() && claim.trim()

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
      title="New nugget"
      description={`2 or more fields, in ${scopeLabel(scope)}, with the reasoning for pairing them written down.`}
      className="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!ready}
            onClick={() => save.mutate()}
          >
            Save nugget
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" hint="How you will recognise it later, e.g. “EPS mean and std”.">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Fields" hint="At least 2, at most 8 — a handful of related measures.">
          <FieldPicker scope={scope} selected={fieldIds} onChange={(ids) => setFieldIds(ids)} />
        </Field>
        {fieldIds.length >= 2 && (
          <section className="flex flex-col gap-2">
            <h3 className="text-title">What the pairing engine sees</h3>
            {suggest.isPending && <Skeleton className="h-16" label="Checking pairs" />}
            {suggest.data?.suggestions.length === 0 && (
              <p className="text-body-compact text-ink-subtle">
                No structurally related pair found among these fields — that is fine, a nugget can
                still be a deliberate pairing. Say why in the rationale.
              </p>
            )}
            {suggest.data?.suggestions.map((s) => (
              <div
                key={`${s.fieldA}-${s.fieldB}`}
                className="flex items-center gap-2 rounded-md border border-hairline p-2 text-body-compact"
              >
                <Badge tone={s.valid ? 'profit' : 'muted'}>
                  {s.valid ? 'Looks sane' : 'Caution'}
                </Badge>
                <span className="num text-ink">
                  {s.fieldA} / {s.fieldB}
                </span>
                <span className="text-ink-subtle">{s.reason}</span>
              </div>
            ))}
          </section>
        )}
        <Field label="Rationale" hint="What each field measures, and why they belong together.">
          <Textarea rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} />
        </Field>
        <Field
          label="Claim"
          hint="A falsifiable claim: what this predicts, and what would be wrong."
        >
          <Textarea rows={3} value={claim} onChange={(e) => setClaim(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  )
}

function NuggetSheet({ nuggetId, onClose }: { nuggetId: string | null; onClose: () => void }) {
  const client = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const detail = useQuery({
    queryKey: ['forge-nugget', nuggetId],
    queryFn: () => nuggets.detail(nuggetId as string),
    enabled: nuggetId != null,
  })
  const d = detail.data

  const remove = useMutation({
    mutationFn: () => nuggets.remove(nuggetId as string),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['forge-nuggets'] })
      toast.success('Nugget deleted')
      setConfirming(false)
      onClose()
    },
    onError: (e) => toast.error('Could not delete it', { description: errorMessage(e) }),
  })

  return (
    <Sheet
      open={nuggetId != null}
      onOpenChange={(open) => !open && onClose()}
      title={d?.name ?? 'Nugget'}
      description={d ? scopeLabel(d.scope as Scope) : 'Loading…'}
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
        {detail.isError && <ErrorNotice error={detail.error} title="Could not load this nugget" />}
        {detail.isPending && nuggetId != null && <Skeleton className="h-40" />}
        {d && (
          <>
            <Metric label="Fields" value={fmt.int(d.fieldIds.length)} hint="Held in this nugget" />
            <section className="flex flex-col gap-1">
              <h3 className="text-title">Rationale</h3>
              <p className="text-body text-pretty text-ink-muted">{d.rationale}</p>
            </section>
            <section className="flex flex-col gap-1">
              <h3 className="text-title">Claim</h3>
              <p className="text-body text-pretty text-ink-muted">{d.claim}</p>
            </section>
            {d.tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {d.tags.map((t) => (
                  <Badge key={t} tone="outline">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
            <section className="flex flex-col gap-2">
              <h3 className="text-title">Fields and their data treatment</h3>
              <ul className="flex flex-col divide-y divide-hairline">
                {d.fieldIds.map((id) => {
                  const f = d.fields.find((row) => row.fieldId === id)
                  return (
                    <li key={id} className="flex flex-col gap-0.5 py-2">
                      <span className="flex flex-wrap items-baseline gap-2">
                        <span className="num text-body text-ink">{id}</span>
                        <span className="num text-body-compact text-ink-subtle">
                          {f?.datasetId ?? DASH} · {f?.fieldType ?? DASH}
                        </span>
                      </span>
                      <span className="num text-body-compact text-ink-subtle">
                        {d.resolvedTreatment[id] ?? DASH}
                      </span>
                      {f?.description && (
                        <span className="text-body-compact text-pretty text-ink-subtle">
                          {f.description}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
            <Notice tone="info" title="Using this nugget">
              Template Studio can bind a template's field slots to this nugget instead of a raw
              field set — each field keeps the treatment shown above.
            </Notice>
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
        The nugget is removed. Nothing on BRAIN changes.
      </Confirm>
    </Sheet>
  )
}
