/**
 * An embeddable dataset/field search + tray, for screens that need fields without leaving
 * the page they're on: pick an existing field set, or search the catalog directly (the same
 * `/api/catalog/fields` search the Data Explorer uses) and tick fields into a selection, all
 * scoped to the market the caller is already working in.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { catalog, type DataFieldRow, type FieldFilter } from '@/api/catalog'
import type { Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { forgeFieldColumns, useFieldExtras } from '@/screens/forge/data/extras'
import { SaveFieldSetDialog } from '@/screens/forge/fieldsets/save-dialog'
import { Button, Chips, ErrorNotice, Field, Input, Segmented } from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { type Column, DataTable } from '@/ui/table'
import { fieldSets } from './api'

const LIMIT = 50
const TYPES = ['MATRIX', 'VECTOR', 'GROUP']

// Lets a caller judge data quality before running anything against a field: same metadata
// BRAIN's own Data Explorer shows (frontend/src/screens/data/fields.tsx), plus crowding below.
// value_score is dataset-level only (DatasetRow) — BRAIN has no per-field value score to show.
const COLUMNS: Column<DataFieldRow>[] = [
  {
    key: 'dataset_id',
    header: 'Dataset',
    width: 'minmax(68px,0.8fr)',
    cell: (r) => <span className="num text-ink-muted">{r.dataset_id ?? '—'}</span>,
  },
  {
    key: 'field_id',
    header: 'Field',
    width: 'minmax(120px,1.4fr)',
    cell: (r) => <span className="num text-ink">{r.field_id}</span>,
  },
  {
    key: 'description',
    header: 'Description',
    width: 'minmax(96px,2fr)',
    cell: (r) => (
      <span className="truncate text-ink-muted" title={r.description ?? undefined}>
        {r.description ?? '—'}
      </span>
    ),
  },
  {
    key: 'category_id',
    header: 'Category',
    width: 'minmax(84px,1fr)',
    cell: (r) => (
      <span className="truncate text-ink-muted" title={r.category_name ?? undefined}>
        {r.category_name ?? '—'}
      </span>
    ),
  },
  {
    key: 'field_type',
    header: 'Type',
    width: 'minmax(60px,0.5fr)',
    align: 'right',
    cell: (r) => <span className="num text-ink-subtle">{r.field_type ?? '—'}</span>,
  },
  {
    key: 'coverage',
    header: 'Coverage',
    width: 'minmax(76px,0.6fr)',
    align: 'right',
    cell: (r) => fmt.pct(r.coverage),
  },
  {
    key: 'date_coverage',
    header: 'Date Coverage',
    width: 'minmax(76px,0.6fr)',
    align: 'right',
    cell: (r) => fmt.pct(r.date_coverage),
  },
  {
    key: 'user_count',
    header: 'Users',
    width: 'minmax(64px,0.4fr)',
    align: 'right',
    cell: (r) => fmt.int(r.user_count),
  },
  {
    key: 'alpha_count',
    header: 'Alphas',
    width: 'minmax(76px,0.4fr)',
    align: 'right',
    cell: (r) => fmt.int(r.alpha_count),
  },
]

export function FieldPicker({
  scope,
  selected,
  onChange,
  initialFieldSetId = null,
}: {
  scope: Scope
  /** The fields picked so far, across whichever mode built them. */
  selected: string[]
  /** The fields, and — only when they came straight from an existing field set, unedited —
   *  that set's id, so the caller can send the lighter `fieldSetId` instead of the fields
   *  themselves. `null` once the caller should treat the selection as picked on the spot. */
  onChange: (fieldIds: string[], fieldSetId: number | null) => void
  /** A field set to start from, e.g. a deep link from Template Studio's "Send to Pipelines". */
  initialFieldSetId?: number | null
}) {
  const [mode, setMode] = useState<'set' | 'search'>('set')
  const [fieldSetId, setFieldSetId] = useState<number | null>(initialFieldSetId)
  const [search, setSearch] = useState('')
  const [types, setTypes] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const term = useDebounced(search.trim(), 250)

  const sets = useQuery({ queryKey: ['forge-fieldsets'], queryFn: fieldSets.list })
  // Only sets saved for this exact market — a field id means different data elsewhere.
  const inScope = (sets.data ?? []).filter(
    (s) =>
      s.scope.region === scope.region &&
      s.scope.delay === scope.delay &&
      s.scope.universe === scope.universe &&
      s.scope.instrumentType === scope.instrumentType,
  )
  const detail = useQuery({
    queryKey: ['forge-fieldsets', fieldSetId],
    queryFn: () => fieldSets.get(fieldSetId as number),
    enabled: fieldSetId != null,
  })
  // biome-ignore lint/correctness/useExhaustiveDependencies: fires once per field-set load
  useEffect(() => {
    if (detail.data && fieldSetId != null) onChange(detail.data.fieldIds, fieldSetId)
  }, [detail.data, fieldSetId])

  const filter: FieldFilter = {
    ...(term ? { search: term } : {}),
    ...(types.length ? { field_types: types } : {}),
    limit: LIMIT,
  }
  const query = useQuery({
    queryKey: ['catalog', 'fields', 'picker', scope, filter],
    queryFn: ({ signal }) => catalog.fields(scope, filter, signal),
    enabled: mode === 'search',
  })
  const extras = useFieldExtras(scope, query.data?.results)
  const columns = [...COLUMNS, ...forgeFieldColumns(extras)]

  const ticked = new Set(selected)
  const toggle = (ids: string[], on: boolean) => {
    const next = new Set(ticked)
    for (const id of ids) {
      if (on) next.add(id)
      else next.delete(id)
    }
    onChange([...next], null)
  }

  return (
    <div className="flex flex-col gap-3">
      <Segmented
        label="Field source"
        value={mode}
        onChange={setMode}
        items={[
          { value: 'set', label: 'Existing field set' },
          { value: 'search', label: 'Search fields' },
        ]}
      />
      {mode === 'set' ? (
        <Field
          label="Field set"
          hint={
            inScope.length === 0
              ? 'No field sets saved for this market yet — search fields instead.'
              : undefined
          }
        >
          <Select
            label="Field set"
            className="w-full"
            value={fieldSetId != null ? String(fieldSetId) : null}
            items={inScope.map((s) => ({
              value: String(s.id),
              label: `${s.name} (${s.fieldIds.length})`,
            }))}
            onChange={(v) => setFieldSetId(Number(v))}
            disabled={inScope.length === 0}
          />
        </Field>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-full sm:w-64"
              placeholder="Search Field ID or Description"
              aria-label="Search field catalog"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Chips
              label="Field type"
              value={types}
              onChange={setTypes}
              items={TYPES.map((t) => ({ value: t, label: t }))}
            />
          </div>
          {query.isError ? (
            <ErrorNotice error={query.error} title="Could not search fields" />
          ) : (
            <DataTable
              label="Fields"
              rows={query.data?.results ?? []}
              columns={columns}
              rowKey={(r) => r.field_id}
              selected={ticked}
              onSelect={toggle}
              loading={query.isPending}
              maxHeight="40vh"
              empty={term ? 'No fields match.' : 'Type to search fields in this market.'}
            />
          )}
        </>
      )}
      {selected.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <Chips
            label="Selected fields"
            value={selected}
            onChange={(ids) => onChange(ids, null)}
            items={selected.map((id) => ({
              value: id,
              title: 'Remove this field',
              label: (
                <>
                  {id} <span className="text-ink-subtle">×</span>
                </>
              ),
            }))}
          />
          <Button variant="ghost" size="sm" onClick={() => setSaving(true)}>
            Save as field set
          </Button>
        </div>
      )}
      <SaveFieldSetDialog
        open={saving}
        onOpenChange={setSaving}
        scope={scope}
        fieldIds={selected}
        filter={filter}
        matching={query.data?.total}
      />
    </div>
  )
}
