/** Forge Quota Governor: today's BRAIN simulations, lane by lane. Read-only — spends nothing. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type QuotaToday = Schemas['QuotaToday']
export type QuotaLane = Schemas['QuotaLane']
export type QuotaHour = Schemas['QuotaHour']
export type QuotaEstimateRequest = Schemas['QuotaEstimateRequest']
export type QuotaEstimate = Schemas['QuotaEstimate']

const B = '/api/forge/quota'

export const forgeQuota = {
  today: () => http.get<QuotaToday>(B),
  estimate: (body: QuotaEstimateRequest) => http.post<QuotaEstimate>(`${B}/estimate`, body),
}
