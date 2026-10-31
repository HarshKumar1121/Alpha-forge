/** Template Studio: bind a template to a field set, judge what it renders, write it up.
 *  Free and offline — nothing here simulates or submits. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

// `PreviewRequest` is a name upstream's tools also use, so the generator namespaces both.
export type PreviewRequest = Schemas['alpha_harness__api__forge_studio__PreviewRequest']
export type PreviewResponse = Schemas['PreviewResponse']
export type RenderedAlpha = Schemas['RenderedAlpha']
export type EligibilityOut = Schemas['EligibilityOut']
export type DescriptionDraft = Schemas['DescriptionDraft']
export type MyTemplate = Schemas['MyTemplate']
export type MyTemplateVersion = Schemas['MyTemplateVersion']
export type LintResponse = Schemas['LintResponse']
export type ExportedTemplate = Schemas['ExportedTemplate']

const B = '/api/forge/studio'

export const studio = {
  preview: (body: PreviewRequest) => http.post<PreviewResponse>(`${B}/preview`, body),
  draft: (body: {
    expression: string
    templateId?: string | undefined
    fieldSetId?: number | undefined
  }) => http.post<DescriptionDraft>(`${B}/description/draft`, body),
  saveDescription: (body: {
    expression: string
    idea: string
    dataRationale: string
    operatorRationale: string
    templateId?: string | null
    scope?: string
  }) => http.put<DescriptionDraft>(`${B}/description`, body),

  mine: () => http.get<MyTemplate[]>(`${B}/my-templates`),
  create: (yamlText: string, note = '') =>
    http.post<MyTemplate>(`${B}/my-templates`, { yamlText, note }),
  update: (templateId: string, yamlText: string, note = '') =>
    http.put<MyTemplate>(`${B}/my-templates/${encodeURIComponent(templateId)}`, {
      yamlText,
      note,
    }),
  remove: (templateId: string) =>
    http.del<{ deleted: boolean }>(`${B}/my-templates/${encodeURIComponent(templateId)}`),
  versions: (templateId: string) =>
    http.get<MyTemplateVersion[]>(`${B}/my-templates/${encodeURIComponent(templateId)}/versions`),
  clone: (templateId: string, newId: string, name = '') =>
    http.post<MyTemplate>(`${B}/my-templates/clone`, { templateId, newId, name }),
  exported: (templateId: string) =>
    http.get<ExportedTemplate>(`${B}/my-templates/${encodeURIComponent(templateId)}/export`),
  lint: (yamlText: string) => http.post<LintResponse>(`${B}/lint`, { yamlText }),
}

/** A starting point for a template of your own: every key filled, nothing to look up. */
export const STARTER_YAML = `templates:
  - id: my_first_idea
    name: My first idea
    category: analyst/revision
    complexity: simple
    angle: change
    slots:
      X: {kind: field}
      W: {kind: window, values: [21, 63]}
      G: {kind: group, values: [industry, subindustry]}
    expr: group_rank(ts_delta($X, $W), $G)
    rationale: >-
      Say in a sentence why this should make money. The engine already cleans every field
      (ts_backfill, and vec_ for VECTOR fields), so slots are never wrapped here.
`
