/** The Workshop: challenge a hypothesis against your own history (free), put it on trial in the
 *  Hypothesis Court (spends the `validate` lane), and promote survivors into a proposal —
 *  which spends nothing until approved from `/forge/agent`'s proposal list (CLAUDE.md rule 5).
 *  `TrialOut.case`, once a trial is `done`, is `Court.Case.to_dict()` — a plain `asdict()`, so
 *  its keys stay snake_case even though everything else here is camelCase. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type Evidence = Schemas['Evidence']
export type EvidenceRequest = Schemas['EvidenceRequest']
export type Finding = Schemas['Finding']
export type TrialOut = Schemas['TrialOut']
export type TrialCreate = Schemas['TrialCreate']
export type Promote = Schemas['Promote']

export interface TestResult {
  kind: string
  field: string
  passed: boolean | null
  severity: number
  value: number | null
  detail: string
  claim: string
}

/** `forge/research/court.py`'s `Case`, verbatim — the shape behind `TrialOut.case`. */
export interface CourtCase {
  hypothesis: Record<string, unknown>
  seeds: Record<string, string>
  results: TestResult[]
  verdicts: Record<string, string>
  verdict: string
  critique: string
  refinement: string
  spent: number
  promoted: Record<string, unknown> | null
}

export interface PromoteResult {
  proposal_id: number
  status: string
  payload: { trial_id: number; fields: string[]; budget: number }
  rationale: string
}

const B = '/api/forge/workshop'

export const workshop = {
  evidence: (body: EvidenceRequest) => http.post<Evidence>(`${B}/evidence`, body),
  createTrial: (body: TrialCreate) => http.post<TrialOut>(`${B}/trials`, body),
  listTrials: () => http.get<TrialOut[]>(`${B}/trials`),
  getTrial: (id: number) => http.get<TrialOut>(`${B}/trials/${id}`),
  promote: (id: number, body: Promote) =>
    http.post<PromoteResult>(`${B}/trials/${id}/promote`, body),
}
