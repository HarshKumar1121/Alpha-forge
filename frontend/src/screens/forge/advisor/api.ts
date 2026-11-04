/** The Run Advisor: a before/after synthesis over field probes, Nugget pairing, template
 *  stats, the Board's checks/repairs and the tear sheet — read-only, spends nothing. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import type { Scope } from '@/api/types'

type Schemas = components['schemas']

export type PreflightOut = Schemas['PreflightOut']
export type PostRunOut = Schemas['PostRunOut']

export interface PreflightRequest {
  templateId: string
  fieldIds: string[]
  region: string
  universe: string
  delay: number
  instrumentType?: string
}

const B = '/api/forge/advisor'

export const advisor = {
  preflight: ({ instrumentType = 'EQUITY', ...rest }: PreflightRequest) =>
    http.post<PreflightOut>(`${B}/preflight`, { instrumentType, ...rest }),
  postrun: (alphaId: string) => http.get<PostRunOut>(`${B}/postrun/${encodeURIComponent(alphaId)}`),
}

/** Convenience for a Template Lab/Workbench caller already holding a `Scope`. */
export const preflightFromScope = (
  scope: Scope,
  templateId: string,
  fieldIds: string[],
): ReturnType<typeof advisor.preflight> =>
  advisor.preflight({
    templateId,
    fieldIds,
    region: scope.region,
    universe: scope.universe,
    delay: scope.delay,
    instrumentType: scope.instrumentType,
  })
