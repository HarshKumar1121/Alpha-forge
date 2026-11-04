/** Pipelines: plan preview (free), then a real run against BRAIN through the Quota Governor.
 *  Nothing here submits — a run ends at candidates, checks and correlations. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type PipelineOut = Schemas['PipelineOut']
export type PlanRequest = Schemas['PlanRequest']
export type PlanPreview = Schemas['PlanPreview']
export type RunRequest = Schemas['alpha_harness__api__forge_pipelines__RunRequest']
export type RunStarted = Schemas['alpha_harness__api__forge_pipelines__RunStarted']
export type RunReport = Schemas['RunReport']
export type CandidateOut = Schemas['CandidateOut']
export type RejectedCandidate = Schemas['RejectedCandidate']
export type QuotaAdmission = Schemas['QuotaAdmission']
// forge: codegen drops the bare `RunSummary` name once Workbench's own `RunSummary` class also
// exists, qualifying both by module path.
export type RunSummary = Schemas['alpha_harness__api__forge_pipelines__RunSummary']
export type InlineFields = Schemas['InlineFields']
export type VerdictOut = Schemas['VerdictOut']

const B = '/api/forge/pipelines'

export const pipelines = {
  list: () => http.get<PipelineOut[]>(B),
  preview: (body: PlanRequest) => http.post<PlanPreview>(`${B}/preview`, body),
  run: (body: RunRequest) => http.post<RunStarted>(`${B}/run`, body),
  getRun: (taskId: string) => http.get<RunReport>(`${B}/runs/${encodeURIComponent(taskId)}`),
  listRuns: () => http.get<RunSummary[]>(`${B}/runs`),
}
