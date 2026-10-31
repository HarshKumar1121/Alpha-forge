/** Forge data: real scopes, datasets with crowding and my history, and field sets. Read-only
 *  except field sets, which live in the local database only. */

import type { FieldFilter } from '@/api/catalog'
import type { components } from '@/api/generated'
import { http } from '@/api/http'
import { type Scope, scopeQs } from '@/api/types'

type Schemas = components['schemas']

export type ScopeTree = Schemas['ScopeTree']
export type InstrumentNode = Schemas['InstrumentNode']
export type RegionNode = Schemas['RegionNode']
export type UniverseNode = Schemas['UniverseNode']
export type ForgeDataset = Schemas['ForgeDataset']
export type ForgeDatasets = Schemas['ForgeDatasets']
export type MyHistory = Schemas['MyHistory']
export type FieldExtras = Schemas['FieldExtras']
export type DatasetPage = Schemas['DatasetPage']
export type FieldSetOut = Schemas['FieldSetOut']
export type FieldSetDetail = Schemas['FieldSetDetail']

/** What a field set is written as; the filter is the Data Explorer's own. */
export interface FieldSetIn {
  name: string
  scope: Scope
  kind: 'manual' | 'dynamic'
  fieldIds?: string[]
  filter?: FieldFilter | null
  notes?: string
}

const B = '/api/forge'

export const forgeData = {
  scopes: () => http.get<ScopeTree>(`${B}/scopes`),
  datasets: (s: Scope) => http.get<ForgeDatasets>(`${B}/data/datasets${scopeQs(s)}`),
  dataset: (s: Scope, id: string) =>
    http.get<DatasetPage>(`${B}/data/datasets/${encodeURIComponent(id)}${scopeQs(s)}`),
  fieldExtras: (s: Scope, fieldIds: string[]) =>
    http.post<FieldExtras>(`${B}/data/fields/extras${scopeQs(s)}`, { fieldIds }),
}

export const fieldSets = {
  list: () => http.get<FieldSetOut[]>(`${B}/fieldsets`),
  get: (id: number) => http.get<FieldSetDetail>(`${B}/fieldsets/${id}`),
  create: (body: FieldSetIn) => http.post<FieldSetOut>(`${B}/fieldsets`, body),
  update: (id: number, body: FieldSetIn) => http.put<FieldSetOut>(`${B}/fieldsets/${id}`, body),
  remove: (id: number) => http.del<{ deleted: boolean }>(`${B}/fieldsets/${id}`),
}

/** A pass rate with the count behind it, e.g. "67% of 3". */
export const passRate = (m: MyHistory, pct: (v: number | null) => string): string =>
  m.checked ? `${pct(m.passRate ?? null)} of ${m.checked}` : '—'
