/**
 * "Save as field set" from the Data Explorer: the ticked fields, or the filter itself.
 *
 * A manual set freezes the fields you picked. A dynamic set saves the filter and re-runs it on
 * every read, so fields BRAIN adds to the market later join the set by themselves.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import type { FieldFilter } from '@/api/catalog'
import { errorMessage } from '@/api/http'
import { type Scope, scopeLabel } from '@/api/types'
import { fmt } from '@/lib/format'
import { Button, Field, Input, Notice, Segmented, Textarea } from '@/ui/kit'
import { Dialog } from '@/ui/overlay'
import { fieldSets } from '../data/api'

export function SaveFieldSetDialog({
  open,
  onOpenChange,
  scope,
  fieldIds,
  filter,
  matching,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  scope: Scope
  /** The ticked fields, if any. */
  fieldIds: string[]
  /** The filter the table is showing, saved by a dynamic set. */
  filter: FieldFilter
  /** How many fields that filter matches right now. */
  matching: number | undefined
}) {
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [kind, setKind] = useState<'manual' | 'dynamic'>(fieldIds.length ? 'manual' : 'dynamic')
  const client = useQueryClient()

  const save = useMutation({
    mutationFn: () =>
      fieldSets.create({
        name,
        scope,
        kind,
        ...(kind === 'manual' ? { fieldIds } : { filter }),
        notes,
      }),
    onSuccess: (made) => {
      void client.invalidateQueries({ queryKey: ['forge-fieldsets'] })
      toast.success(`Saved "${made.name}"`, {
        description: `${scopeLabel(scope)} — find it under Forge › Field sets.`,
      })
      setName('')
      setNotes('')
      onOpenChange(false)
    },
    onError: (e) => toast.error('Could not save the field set', { description: errorMessage(e) }),
  })

  const nothing = kind === 'manual' && fieldIds.length === 0
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Save as field set"
      description={`A field set is tied to its market, because a field id means different data elsewhere. This one: ${scopeLabel(scope)}.`}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!name.trim() || nothing}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" hint="How you will recognise it later, e.g. “USA TOP3000 analyst”.">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field
          label="What to save"
          hint={
            kind === 'manual'
              ? `The ${fmt.int(fieldIds.length)} fields ticked in the table, frozen as they are.`
              : 'The filter as it stands. Re-run on every read, so new BRAIN fields that match join the set.'
          }
        >
          <Segmented
            label="Kind"
            value={kind}
            onChange={setKind}
            items={[
              {
                value: 'manual',
                label: `Ticked fields (${fmt.int(fieldIds.length)})`,
                disabled: fieldIds.length === 0,
              },
              { value: 'dynamic', label: `This filter (${fmt.int(matching ?? null)} now)` },
            ]}
          />
        </Field>
        <Field
          label="Notes"
          hint="The idea behind the selection, so the set still makes sense in a month."
        >
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </Field>
        {nothing && (
          <Notice tone="warn" title="Nothing ticked">
            Tick some fields in the table, or save the filter instead.
          </Notice>
        )}
      </div>
    </Dialog>
  )
}
