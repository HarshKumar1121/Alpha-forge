/** The Osmosis Allocator: read-only, from PnL and BRAIN figures stored locally. The points on it
 *  are what the user types into BRAIN by hand. Nothing here submits or calls BRAIN. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

export type OsmosisSummary = Schemas['OsmosisSummary']
export type OsmosisSummaryRow = Schemas['OsmosisSummaryRow']
export type OsmosisScopeView = Schemas['OsmosisScopeView']
export type OsmosisRow = Schemas['OsmosisRow']
export type OsmosisExcluded = Schemas['OsmosisExcluded']
export type OsmosisMethod = Schemas['OsmosisMethod']
export type OsmosisChartPoint = Schemas['OsmosisChartPoint']
export type OsmosisCurvePoint = Schemas['OsmosisCurvePoint']

const P = '/api/forge/osmosis'

export const osmosis = {
  /** Every scope at a glance: alpha count, recommended N, method and combined Sharpe. */
  summary: () => http.get<OsmosisSummary>(`${P}/summary`),
  /** One scope in full. Omit ``n`` and ``method`` for the recommendation and the default. */
  scope: (region: string, delay: number, n?: number | null, method?: string | null) =>
    http.get<OsmosisScopeView>(
      `${P}/scope${qs({ region, delay, n: n ?? undefined, method: method ?? undefined })}`,
    ),
}
