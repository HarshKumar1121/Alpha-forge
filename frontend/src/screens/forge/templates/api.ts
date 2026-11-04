/** Forge Template Library: read-only, free — browsing a template never spends a simulation. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

export type TemplateList = Schemas['ForgeTemplateList']
export type TemplateSummary = Schemas['ForgeTemplateSummary']
export type TemplateDetail = Schemas['ForgeTemplateDetail']
export type TemplateFit = Schemas['ForgeTemplateFit']
export type TemplateSlot = Schemas['ForgeTemplateSlot']
export type CompatibleTemplate = Schemas['CompatibleTemplate']
export type ScopeTemplate = Schemas['ScopeTemplate']
export type ScopeTemplates = Schemas['ScopeTemplates']

export interface CompatibleFieldsRequest {
  fieldIds: string[]
  region: string
  universe: string
  delay: number
  instrumentType?: string
}

export interface ScopeTemplatesRequest {
  region: string
  universe: string
  delay: number
  instrumentType?: string
  datasetCategory?: string | null
}

const B = '/api/forge/templates'

export const forgeTemplates = {
  list: () => http.get<TemplateList>(B),
  get: (id: string) => http.get<TemplateDetail>(`${B}/${encodeURIComponent(id)}`),
  compatible: (body: CompatibleFieldsRequest) =>
    http.post<{ templates: CompatibleTemplate[] }>(`${B}/compatible`, body),
  // forge: the Template Lab's selection-first flow — a market and a dataset category, no
  // fields picked yet, same eligibility check `/compatible` runs but against the whole catalog.
  byScope: ({ instrumentType = 'EQUITY', datasetCategory, ...rest }: ScopeTemplatesRequest) =>
    http.get<ScopeTemplates>(
      `${B}/by-scope${qs({ instrument_type: instrumentType, dataset_category: datasetCategory, ...rest })}`,
    ),
}
