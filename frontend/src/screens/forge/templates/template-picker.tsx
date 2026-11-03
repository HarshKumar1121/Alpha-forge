/**
 * A template picker filtered to what the selected fields can actually bind to — reuses the
 * engine's own slot-binding check (`fits_fields`) via `POST /api/forge/templates/compatible`,
 * so it never offers a template that needs fields the user hasn't picked yet.
 */

import { useQuery } from '@tanstack/react-query'
import type { Scope } from '@/api/types'
import { Field } from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { forgeTemplates } from './api'

export function TemplatePicker({
  scope,
  fieldIds,
  value,
  onChange,
}: {
  scope: Scope
  /** The fields picked so far — only templates whose slots these could fill are offered. */
  fieldIds: string[]
  value: string | null
  onChange: (templateId: string) => void
}) {
  const compatible = useQuery({
    queryKey: ['forge-templates', 'compatible', scope, fieldIds],
    queryFn: () =>
      forgeTemplates.compatible({
        fieldIds,
        region: scope.region,
        universe: scope.universe,
        delay: scope.delay,
        instrumentType: scope.instrumentType,
      }),
    enabled: fieldIds.length > 0,
  })

  const items = (compatible.data?.templates ?? []).map((t) => ({
    value: t.id,
    label: `${t.name} (${t.complexity})`,
  }))

  const hint =
    fieldIds.length === 0
      ? 'Pick fields first — only templates they could fill are offered.'
      : compatible.isSuccess && items.length === 0
        ? 'No template in the library binds to these fields.'
        : 'Only templates whose field slots these fields could fill.'

  return (
    <Field label="Template" hint={hint}>
      <Select
        label="Template"
        className="w-full"
        value={value}
        onChange={onChange}
        items={items}
        disabled={fieldIds.length === 0 || items.length === 0}
      />
    </Field>
  )
}
