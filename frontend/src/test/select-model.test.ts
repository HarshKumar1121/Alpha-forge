/** Select's rules, tested without a screen: the four fit states, the fit line, the search space
 *  and what a hand-off writes into Template Lab. The backend's own rules have their own tests. */

import { describe, expect, it } from 'vitest'
import type { DataFieldRow } from '@/api/catalog'
import type { FitSlot, FitTemplate } from '@/screens/forge/select/api'
import {
  fitLine,
  fitState,
  groupTemplates,
  handOffVariables,
  searchSpace,
  searchSpaceLabel,
} from '@/screens/forge/select/model'

function slot(over: Partial<FitSlot> & Pick<FitSlot, 'name' | 'kind'>): FitSlot {
  return {
    type: null,
    archetype: [],
    aggregate: null,
    values: [],
    candidates: [],
    problem: null,
    ...over,
  }
}

function template(over: Partial<FitTemplate> & Pick<FitTemplate, 'id' | 'slots'>): FitTemplate {
  return {
    name: over.id,
    source: 'builtin',
    category: 'analyst/x',
    datasetCategory: 'analyst',
    complexity: 'simple',
    angle: 'level',
    rationale: '',
    expr: 'rank($X)',
    fits: true,
    note: null,
    pairingProblem: null,
    ...over,
  }
}

describe('fitState', () => {
  it('reads a declined walk as not checked, never as a failed fit', () => {
    const t = template({
      id: 'big',
      fits: false,
      note: '3 field slots over 40 fields is too many combinations to check here.',
      slots: [slot({ name: 'X', kind: 'field', candidates: ['a'] })],
    })
    expect(fitState(t)).toBe('notChecked')
  })

  it('reads a template whose slots all bind as fits', () => {
    const t = template({ id: 'ok', slots: [slot({ name: 'X', kind: 'field', candidates: ['a'] })] })
    expect(fitState(t)).toBe('fits')
  })

  it('reads every slot having candidates but no joint binding as needs matching fields', () => {
    const t = template({
      id: 'pair',
      fits: false,
      pairingProblem:
        'none of your fields has one of these words in its id (mean), as slot Y needs',
      slots: [
        slot({ name: 'X', kind: 'field', candidates: ['a'] }),
        slot({ name: 'Y', kind: 'field', candidates: ['b'] }),
      ],
    })
    expect(fitState(t)).toBe('needsMatch')
  })

  it("reads a slot with no candidate as doesn't fit yet", () => {
    const t = template({
      id: 'vec',
      fits: false,
      slots: [
        slot({
          name: 'X',
          kind: 'field',
          candidates: [],
          problem: 'field close is MATRIX; this slot needs a VECTOR field',
        }),
      ],
    })
    expect(fitState(t)).toBe('doesNotFit')
    expect(fitLine(t)).toBe(
      '$X has no match — field close is MATRIX; this slot needs a VECTOR field.',
    )
  })
})

describe('fitLine', () => {
  it('shows the pairing reason for needs matching fields, not the stopgap', () => {
    const t = template({
      id: 'pair',
      fits: false,
      pairingProblem:
        'none of your fields has one of these words in its id (mean), as slot Y needs',
      slots: [
        slot({ name: 'X', kind: 'field', candidates: ['a'] }),
        slot({ name: 'Y', kind: 'field', candidates: ['b'] }),
      ],
    })
    expect(fitLine(t)).toBe(
      'none of your fields has one of these words in its id (mean), as slot Y needs',
    )
  })

  it('falls back to the generic stopgap only when no pairing reason is set', () => {
    const t = template({
      id: 'pair',
      fits: false,
      pairingProblem: null,
      slots: [slot({ name: 'X', kind: 'field', candidates: ['a'] })],
    })
    expect(fitLine(t)).toMatch(/no combination of your fields satisfies/)
  })

  it('counts each field slot in the fits line', () => {
    const t = template({
      id: 'ok',
      slots: [
        slot({ name: 'X', kind: 'field', candidates: ['a', 'b', 'c'] }),
        slot({ name: 'Y', kind: 'field', candidates: ['d'] }),
      ],
    })
    expect(fitLine(t)).toBe('Fills all 2 field slots from your pool — $X ← 3 fields, $Y ← 1 field.')
  })
})

describe('groupTemplates', () => {
  it('sorts the fitting group by candidate count, most first, and files each state apart', () => {
    const few = template({
      id: 'few',
      slots: [slot({ name: 'X', kind: 'field', candidates: ['a'] })],
    })
    const many = template({
      id: 'many',
      slots: [slot({ name: 'X', kind: 'field', candidates: ['a', 'b', 'c'] })],
    })
    const declined = template({ id: 'declined', fits: false, note: 'too many', slots: [] })
    const groups = groupTemplates([few, declined, many])
    expect(groups.fits.map((t) => t.id)).toEqual(['many', 'few'])
    expect(groups.notChecked.map((t) => t.id)).toEqual(['declined'])
    expect(groups.needsMatch).toEqual([])
    expect(groups.doesNotFit).toEqual([])
  })
})

describe('the search space', () => {
  it('multiplies each field slot by its candidates and each window by its values', () => {
    const t = template({
      id: 'space',
      slots: [
        slot({ name: 'X', kind: 'field', candidates: ['a', 'b'] }),
        slot({ name: 'W', kind: 'window', values: [21, 63, 126] }),
      ],
    })
    expect(searchSpace(t)).toBe(6)
    expect(searchSpaceLabel(t)).toBe('2 fields × 3 windows = 6 expressions')
  })

  it('is zero when a field slot has no candidate', () => {
    const t = template({
      id: 'none',
      fits: false,
      slots: [slot({ name: 'X', kind: 'field', candidates: [] })],
    })
    expect(searchSpace(t)).toBe(0)
  })
})

describe('handOffVariables', () => {
  const rows = new Map<string, DataFieldRow>([
    ['anl4_eps_mean', { field_id: 'anl4_eps_mean', dataset_id: 'analyst4' } as DataFieldRow],
    ['anl4_rev_mean', { field_id: 'anl4_rev_mean', dataset_id: 'analyst4' } as DataFieldRow],
    ['fnd6_eps', { field_id: 'fnd6_eps', dataset_id: 'fundamental6' } as DataFieldRow],
  ])

  it('names exactly the candidate fields, with their unique datasets', () => {
    const t = template({
      id: 'x',
      slots: [
        slot({
          name: 'X',
          kind: 'field',
          candidates: ['anl4_eps_mean', 'anl4_rev_mean', 'fnd6_eps'],
        }),
      ],
    })
    expect(handOffVariables(t, rows)).toEqual({
      X: {
        kind: 'fields',
        dataset_ids: ['analyst4', 'fundamental6'],
        filter: { field_ids: ['anl4_eps_mean', 'anl4_rev_mean', 'fnd6_eps'], field_types: [] },
        vector_operators: [],
      },
    })
  })

  it('reduces a vector slot with its aggregate and keeps a window slot as its values', () => {
    const t = template({
      id: 'x',
      slots: [
        slot({
          name: 'V',
          kind: 'field',
          type: 'VECTOR',
          aggregate: 'vec_sum',
          candidates: ['fnd6_eps'],
        }),
        slot({ name: 'W', kind: 'window', values: [21, 63] }),
      ],
    })
    const vars = handOffVariables(t, rows)
    expect(vars['V']).toMatchObject({ kind: 'fields', vector_operators: ['vec_sum'] })
    expect(vars['W']).toEqual({ kind: 'values', values: '21, 63' })
  })
})
