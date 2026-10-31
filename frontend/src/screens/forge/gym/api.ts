/** Forge Gym: simulate on the synthetic market. Free — nothing here reaches BRAIN. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type GymRequest = Schemas['GymRequest']
export type GymTearSheet = Schemas['GymTearSheet']
export type GymCatalog = Schemas['GymCatalog']
export type GymBlock = Schemas['Block']

const B = '/api/forge/gym'

export const forgeGym = {
  simulate: (body: GymRequest) => http.post<GymTearSheet>(`${B}/simulate`, body),
  catalog: () => http.get<GymCatalog>(`${B}/catalog`),
}
