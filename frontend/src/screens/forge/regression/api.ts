/** Multi-Field Regression Lab: a field-screening step that runs *before* template binding.
 *  `/prescreen` is free (duplicate-description check over catalog metadata already synced);
 *  `/run` spends the `explore` lane through the Quota Governor (CLAUDE.md rule 3) to fetch each
 *  candidate field's simulated series and runs OLS/VIF/stability over whatever got admitted. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import { type Scope, type ScopeBody, scopeQs } from '@/api/types'

type Schemas = components['schemas']

export type PrescreenOut = Schemas['PrescreenOut']
export type DuplicatePairOut = Schemas['DuplicatePairOut']
export type RegressionRunRequest = Schemas['RegressionRunRequest']
export type RegressionRunOut = Schemas['RegressionRunOut']
export type FieldStatOut = Schemas['FieldStatOut']
export type StabilityOut = Schemas['StabilityOut']
export type RecommendationOut = Schemas['RecommendationOut']
export type WinnerOut = Schemas['WinnerOut']
export type SuggestedSetOut = Schemas['SuggestedSetOut']
// Qualified: `FieldProfileOut` also names an unrelated shape in the Nugget layer's schemas, so
// openapi-typescript disambiguates both by their owning module.
export type FieldProfileOut = Schemas['alpha_harness__api__forge_regression__FieldProfileOut']

const B = '/api/forge/regression'

export const regression = {
  /** Zero lane spend: a pairwise `duplicate_reason()` check over the candidates' catalog rows. */
  prescreen: (scope: Scope, fieldIds: string[]) =>
    http.post<PrescreenOut>(`${B}/prescreen${scopeQs(scope)}`, { fieldIds }),
  /** Zero lane spend: known-good combinations from your own saved Data Nuggets in this scope. */
  suggestions: (scope: Scope) => http.get<SuggestedSetOut[]>(`${B}/suggestions${scopeQs(scope)}`),
  /** Spends the `explore` lane. May partially admit; `dropped`/`admitted` say by how much. */
  run: (payload: {
    scope: ScopeBody
    fieldIds: string[]
    target: { kind: 'vault' | 'gym' | 'field'; ref: string }
    dateRange?: { start: string; end: string } | null
  }) =>
    http.post<RegressionRunOut>(`${B}/run`, {
      scope: payload.scope,
      fieldIds: payload.fieldIds,
      target: payload.target,
      dateRange: payload.dateRange ?? null,
    }),
}
