/** The Correlation Submit Helper: a deadline-day check of self- and production correlation for
 *  a hand-picked set of alphas, plus a "submit only these" recommendation among the ones that
 *  pass. Read-only decision support — there is no submit call here or anywhere. */

import { http, qs } from '@/api/http'

const B = '/api/forge/submit-helper'

// Hand-written: the backend schemas are new enough that `generated.ts` has not been
// regenerated against a running server yet (see other Forge screens' api.ts files for the
// same convention when that happens).

export interface RecentAlpha {
  id: number
  alphaId: string
  pipeline: string
  alphaType: string
  createdAt: string
}

export interface CheckRequest {
  alphaIds: string[]
  threshold: number
}

export interface TaskStarted {
  taskId: string
}

export interface AlphaRow {
  alphaId: string
  selfCorrelation: number | null
  prodCorrelation: number | null
  lastChecked: string | null
  verdict: 'PASS' | 'FAIL' | 'UNKNOWN'
}

export interface SubmitHelperResult {
  threshold: number
  alphas: AlphaRow[]
  matrix: Record<string, Record<string, number>>
  recommended: string[]
  summary: string
}

export const submitHelper = {
  recent: (limit?: number) => http.get<RecentAlpha[]>(`${B}/recent${qs({ limit })}`),
  check: (body: CheckRequest) => http.post<TaskStarted>(`${B}/check`, body),
  result: () => http.get<SubmitHelperResult | null>(`${B}/result`),
}
