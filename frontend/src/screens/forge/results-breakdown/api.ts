/** Results Breakdown for one finished Template Lab task: pass rate & Sharpe by slice, why
 *  alphas failed and what's fixable, and a suggested submit order by correlation. Reads only
 *  what the task already produced; nothing here reaches BRAIN. Queueing a repair still goes
 *  through the existing Submittable Board repair route and its Quota Governor admission. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

export type Breakdown = Schemas['Breakdown']
export type SliceRow = Schemas['SliceRow']
export type FailureCount = Schemas['FailureCount']
export type NearMiss = Schemas['NearMiss']
export type SubmitCandidate = Schemas['SubmitCandidate']
export type Staged = Schemas['Staged']

export type GroupBy = 'field' | 'dataset' | 'setting'
export type SettingName = 'neutralization' | 'decay' | 'universe'

const B = '/api/forge/breakdown'

export const breakdown = {
  get: (
    taskId: number,
    params: { groupBy?: GroupBy; settingName?: SettingName; minN?: number } = {},
  ) =>
    http.get<Breakdown>(
      `${B}/${taskId}${qs({
        group_by: params.groupBy,
        setting_name: params.settingName,
        min_n: params.minN,
      })}`,
    ),
  stage: (taskId: number, alphaId: string) =>
    http.post<Staged>(`${B}/${taskId}/alphas/${alphaId}/stage`),
}
