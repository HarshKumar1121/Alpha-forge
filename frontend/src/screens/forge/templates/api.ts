/** Forge Template Library: read-only, free — browsing a template never spends a simulation. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type TemplateList = Schemas['ForgeTemplateList']
export type TemplateSummary = Schemas['ForgeTemplateSummary']
export type TemplateDetail = Schemas['ForgeTemplateDetail']
export type TemplateFit = Schemas['ForgeTemplateFit']
export type TemplateSlot = Schemas['ForgeTemplateSlot']
export type CompatibleTemplate = Schemas['CompatibleTemplate']

export interface CompatibleFieldsRequest {
  fieldIds: string[]
  region: string
  universe: string
  delay: number
  instrumentType?: string
}

const B = '/api/forge/templates'

export const forgeTemplates = {
  list: () => http.get<TemplateList>(B),
  get: (id: string) => http.get<TemplateDetail>(`${B}/${encodeURIComponent(id)}`),
  compatible: (body: CompatibleFieldsRequest) =>
    http.post<{ templates: CompatibleTemplate[] }>(`${B}/compatible`, body),
}
