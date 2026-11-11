/**
 * Probe a real field's behaviour: 3 cheap simulations through the Quota Governor's `explore`
 * lane. Unlike the Gym itself, this reaches BRAIN and spends quota.
 */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import type { Scope } from '@/api/types'
import { scopeQs } from '@/api/types'

type Schemas = components['schemas']

export type ProbeResult = Schemas['ProbeResult']
export type ProbeAngle = Schemas['ProbeAngle']

const B = '/api/forge/probe'

export const forgeProbe = {
  field: (scope: Scope, fieldId: string) =>
    http.post<ProbeResult>(`${B}/field${scopeQs(scope)}`, { fieldId }),
}
