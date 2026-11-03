/** Data Nuggets: 2+ fields, a written rationale and a falsifiable claim, saved so a template
 *  can bind to them instead of raw fields (see Template Studio's "bind to a nugget"). */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'
import type { Scope } from '@/api/types'

type Schemas = components['schemas']

export type NuggetOut = Schemas['NuggetOut']
export type NuggetDetail = Schemas['NuggetDetail']
export type PairingSuggestionOut = Schemas['PairingSuggestionOut']
export type SuggestResponse = Schemas['SuggestResponse']

const B = '/api/forge/nuggets'

export const nuggets = {
  list: (filter?: { region?: string; universe?: string }) =>
    http.get<NuggetOut[]>(`${B}${qs(filter ?? {})}`),
  detail: (nuggetId: string) => http.get<NuggetDetail>(`${B}/${encodeURIComponent(nuggetId)}`),
  suggest: (scope: Scope, fieldIds: string[]) =>
    http.post<SuggestResponse>(`${B}/suggest`, { scope, fieldIds }),
  create: (body: {
    name: string
    scope: Scope
    fieldIds: string[]
    rationale: string
    claim: string
    tags?: string[]
    allowDuplicates?: boolean
  }) => http.post<NuggetOut>(B, body),
  remove: (nuggetId: string) =>
    http.del<{ deleted: boolean }>(`${B}/${encodeURIComponent(nuggetId)}`),
}
