/**
 * Forge Analytics: the real tear sheet and factor attribution for one BRAIN alpha, built from
 * its own recorded `pnl`, `turnover` and `yearly-stats`. Read-only — a GET against recordsets
 * BRAIN already has, never a simulation.
 */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type TearSheetOut = Schemas['TearSheetOut']
export type FactorsOut = Schemas['FactorsOut']

const B = '/api/forge/analytics'

export const forgeAnalytics = {
  tearSheet: (alphaId: string) =>
    http.get<TearSheetOut>(`${B}/tear-sheet/${encodeURIComponent(alphaId)}`),
  factors: (alphaId: string) => http.get<FactorsOut>(`${B}/factors/${encodeURIComponent(alphaId)}`),
}
