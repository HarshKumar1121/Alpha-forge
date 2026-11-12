/** Select's pure rules, with no React and no network: which of the four fit states a template is
 *  in, what its fit line says, how big its search space is, and the Template Lab variables a
 *  hand-off writes. Kept apart from the screen so the rules can be tested directly. */

import type { DataFieldRow } from '@/api/catalog'
import type {
  FieldsVariable,
  ValuesVariable,
  VariableDef,
} from '@/screens/research-labs/template/api'
import type { FitSlot, FitTemplate } from './api'

/** The four fit states, in the order the screen groups them. */
export type FitState = 'fits' | 'notChecked' | 'needsMatch' | 'doesNotFit'

/** Which state a template is in. `note` comes first: a declined walk is not a verdict, so it
 *  must never read as "does not fit". Then `fits`. Then, when every field slot has a candidate
 *  but no joint binding exists, the pool's fields are each fine and only their pairing fails
 *  (`needsMatch`). Anything else has a slot with no candidate at all (`doesNotFit`). */
export function fitState(t: FitTemplate): FitState {
  if (t.note) return 'notChecked'
  if (t.fits) return 'fits'
  if (fieldSlots(t).every((s) => candidatesOf(s).length > 0)) return 'needsMatch'
  return 'doesNotFit'
}

export function fieldSlots(t: FitTemplate): FitSlot[] {
  return t.slots.filter((s) => s.kind === 'field')
}

export function candidatesOf(s: FitSlot): string[] {
  return s.candidates ?? []
}

/** How many pool fields could fill this template's field slots, summed: the sort key for the
 *  fitting group, so the template with the most room to choose comes first. */
export function candidateTotal(t: FitTemplate): number {
  return fieldSlots(t).reduce((n, s) => n + candidatesOf(s).length, 0)
}

export interface Groups {
  fits: FitTemplate[]
  notChecked: FitTemplate[]
  needsMatch: FitTemplate[]
  doesNotFit: FitTemplate[]
}

/** Split the templates into the four groups, the fitting one sorted by candidate count. */
export function groupTemplates(templates: FitTemplate[]): Groups {
  const groups: Groups = { fits: [], notChecked: [], needsMatch: [], doesNotFit: [] }
  for (const t of templates) {
    groups[fitState(t)].push(t)
  }
  groups.fits.sort((a, b) => candidateTotal(b) - candidateTotal(a))
  return groups
}

/** The one line under a template's name. Each state has its own claim, and the wording never
 *  blurs them. */
export function fitLine(t: FitTemplate): string {
  const state = fitState(t)
  if (state === 'notChecked') return t.note ?? 'Not checked.'
  if (state === 'fits') {
    const parts = fieldSlots(t).map((s) => {
      const n = candidatesOf(s).length
      return `$${s.name} ← ${n} field${n === 1 ? '' : 's'}`
    })
    return `Fills all ${fieldSlots(t).length} field slots from your pool — ${parts.join(', ')}.`
  }
  if (state === 'needsMatch') {
    return (
      t.pairingProblem ??
      "Each slot has candidates, but no combination of your fields satisfies the template's dataset and pairing rules together."
    )
  }
  const blocked = fieldSlots(t).find((s) => candidatesOf(s).length === 0)
  return blocked
    ? `$${blocked.name} has no match — ${blocked.problem ?? 'no pool field fits'}.`
    : ''
}

/** How many expressions the template would search: the product of each field slot's candidates
 *  and each window or group slot's values. `0` when a field slot has no candidate. */
export function searchSpace(t: FitTemplate): number {
  return t.slots.reduce((n, s) => {
    const size = s.kind === 'field' ? candidatesOf(s).length : (s.values ?? []).length
    return n * size
  }, 1)
}

/** The footer's sum, spelled out, e.g. "3 fields × 3 windows = 9 expressions". */
export function searchSpaceLabel(t: FitTemplate): string {
  const factors = t.slots.map((s) => {
    if (s.kind === 'field') {
      const n = candidatesOf(s).length
      return `${n} field${n === 1 ? '' : 's'}`
    }
    const n = (s.values ?? []).length
    return `${n} ${s.kind === 'window' ? 'window' : 'group'}${n === 1 ? '' : 's'}`
  })
  const count = searchSpace(t)
  return `${factors.join(' × ')} = ${count} expression${count === 1 ? '' : 's'}`
}

/** The Template Lab variables a hand-off writes, one per slot, in the shape the Data Explorer
 *  pick already writes. A field slot names exactly its candidates (so the preview searches those
 *  fields, not the whole dataset); a window or group slot lists its values. */
export function handOffVariables(
  t: FitTemplate,
  rows: ReadonlyMap<string, DataFieldRow>,
): Record<string, VariableDef> {
  const out: Record<string, VariableDef> = {}
  for (const s of t.slots) {
    if (s.kind === 'field') {
      const ids = candidatesOf(s)
      const datasets = new Set<string>()
      for (const id of ids) {
        const dataset = rows.get(id)?.dataset_id
        if (dataset) datasets.add(dataset)
      }
      const variable: FieldsVariable = {
        kind: 'fields',
        dataset_ids: [...datasets],
        filter: { field_ids: ids, field_types: s.type ? [s.type] : [] },
        vector_operators: s.type === 'VECTOR' && s.aggregate ? [s.aggregate] : [],
      }
      out[s.name] = variable
    } else {
      const variable: ValuesVariable = { kind: 'values', values: (s.values ?? []).join(', ') }
      out[s.name] = variable
    }
  }
  return out
}
