/** The Workbench's own field tray: a scope-keyed scratch selection, separate from named
 *  Field Sets. Market bar and field search reuse `forgeData`/`FieldPicker`; this is just the
 *  persistence the tray needs. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'
import { type Scope, scopeQs } from '@/api/types'

type Schemas = components['schemas']

export type TrayOut = Schemas['TrayOut']
export type RunCandidateIn = Schemas['RunCandidateIn']
export type RunStarted = Schemas['alpha_harness__api__forge_workbench__RunStarted']
export type RunResult = Schemas['RunResult']
export type RunItemOut = Schemas['RunItemOut']

const B = '/api/forge/workbench'

export const workbench = {
  getTray: (scope: Scope) => http.get<TrayOut>(`${B}/tray${scopeQs(scope)}`),
  setTray: (scope: Scope, fieldIds: string[]) =>
    http.put<TrayOut>(`${B}/tray${scopeQs(scope)}`, { fieldIds }),
  run: (scope: Scope, candidates: RunCandidateIn[]) =>
    http.post<RunStarted>(`${B}/run${scopeQs(scope)}`, { candidates }),
  getRun: (taskId: string) => http.get<RunResult>(`${B}/run/${encodeURIComponent(taskId)}`),
}
