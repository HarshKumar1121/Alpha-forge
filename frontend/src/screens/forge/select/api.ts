/** Select's one new call: every template read against the pool (`POST /api/forge/templates/fit`).
 *  Everything else on the screen reuses an existing client (fields, scope, quota, nuggets). */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import type { Scope } from '@/api/types'

type Schemas = components['schemas']

export type FitTemplates = Schemas['FitTemplates']
export type FitTemplate = Schemas['FitTemplate']
export type FitSlot = Schemas['FitSlot']

/** The backend refuses more than this (`FitTemplatesRequest.field_ids`), and so does the screen. */
export const POOL_CAP = 50

export const selectFit = {
  fit: (scope: Scope, fieldIds: string[]) =>
    http.post<FitTemplates>('/api/forge/templates/fit', {
      fieldIds,
      region: scope.region,
      universe: scope.universe,
      delay: scope.delay,
      instrumentType: scope.instrumentType,
    }),
}
