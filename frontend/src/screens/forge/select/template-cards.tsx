/** Select's template picker: every template read against the pool, in four groups. Each card
 *  states its fit in one line; a declined walk, a pairing problem and a missing field each read
 *  differently, so no card claims more than the check actually found. */

import type { ReactNode } from 'react'
import { Badge, Button } from '@/ui/kit'
import type { FitTemplate } from './api'
import { type FitState, fitLine, fitState, type Groups } from './model'

const STATE_LABEL: Record<FitState, { text: string; tone: 'profit' | 'muted' | 'warn' | 'loss' }> =
  {
    fits: { text: 'Fits', tone: 'profit' },
    notChecked: { text: 'Not checked', tone: 'muted' },
    needsMatch: { text: 'Needs matching fields', tone: 'warn' },
    doesNotFit: { text: "Doesn't fit yet", tone: 'loss' },
  }

const STATE_TIP: Record<FitState, string> = {
  fits: 'Every field slot has a pool field that can fill it, and the fields can be paired.',
  notChecked:
    'The check was declined for this many field slots and pool fields. Nothing was verified.',
  needsMatch:
    'Each slot has candidates, but the candidate fields cannot be paired the way the template needs. No single field is wrong.',
  doesNotFit: 'At least one field slot has no pool field that can fill it.',
}

/** The four groups, in the order they show: fitting open, the rest collapsed under a divider. */
const SECTIONS: { key: keyof Groups; title: string; open: boolean; hint: string }[] = [
  {
    key: 'fits',
    title: 'Fits',
    open: true,
    hint: 'Templates whose slots your pool can fill, most candidate fields first.',
  },
  {
    key: 'notChecked',
    title: 'Not checked',
    open: false,
    hint: 'The check was declined (too many combinations). Choosing one is your call.',
  },
  {
    key: 'needsMatch',
    title: 'Needs matching fields',
    open: false,
    hint: 'Every slot has candidates, but the pairing rules fail for the pool as a whole.',
  },
  {
    key: 'doesNotFit',
    title: "Doesn't fit yet",
    open: false,
    hint: 'A slot has no pool field that can fill it. The line says which one and why.',
  },
]

export function TemplateGroups({
  groups,
  selectedId,
  onSelect,
}: {
  groups: Groups
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div className="flex flex-col gap-3">
      {SECTIONS.map((section) => {
        const list = groups[section.key]
        if (list.length === 0) return null
        return (
          <details key={section.key} open={section.open} className="group">
            <summary className="cursor-pointer text-body text-ink-muted" title={section.hint}>
              {section.title} ({list.length})
            </summary>
            <div className="mt-2 flex flex-col gap-2">
              {list.map((t) => (
                <TemplateCard
                  key={t.id}
                  template={t}
                  selected={t.id === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </details>
        )
      })}
    </div>
  )
}

export function TemplateCard({
  template,
  selected,
  onSelect,
}: {
  template: FitTemplate
  selected: boolean
  onSelect: (id: string) => void
}) {
  const state = fitState(template)
  const label = STATE_LABEL[state]
  return (
    <div
      className={`flex flex-col gap-2 rounded-md border p-3 ${
        selected ? 'border-hairline-strong bg-surface-2' : 'border-hairline'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{template.name}</span>
        <Badge tone="neutral" title="Where this template comes from">
          {template.source === 'builtin' ? 'Built-in' : 'Mine'}
        </Badge>
        <Badge tone="muted" title="The dataset category this template is about">
          {template.datasetCategory}
        </Badge>
        <Badge tone="muted" title="Simple templates suit Power Pool best">
          {template.complexity}
        </Badge>
        <Badge tone="muted" title="Which probe this template expresses">
          {template.angle}
        </Badge>
        <span className="ml-auto">
          <Button
            size="sm"
            variant={selected ? 'primary' : 'secondary'}
            aria-pressed={selected}
            onClick={() => onSelect(template.id)}
          >
            {selected ? 'Selected' : 'Select'}
          </Button>
        </span>
      </div>
      <code className="font-mono text-body-compact text-ink-muted">{template.expr}</code>
      <FitLine state={state} label={label} tip={STATE_TIP[state]}>
        {fitLine(template)}
      </FitLine>
      {template.rationale ? (
        <details className="text-body-compact text-ink-subtle">
          <summary className="cursor-pointer">why this shape</summary>
          <p className="mt-1">{template.rationale}</p>
        </details>
      ) : null}
    </div>
  )
}

function FitLine({
  state,
  label,
  tip,
  children,
}: {
  state: FitState
  label: { text: string; tone: 'profit' | 'muted' | 'warn' | 'loss' }
  tip: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <span title={tip}>
        <Badge tone={label.tone}>
          {state === 'fits' ? '✓ ' : ''}
          {label.text}
        </Badge>
      </span>
      <p className="text-body-compact text-ink-muted">{children}</p>
    </div>
  )
}
