/** The Submittable Board: families from what already ran, grouped by day and alpha type, with
 *  why each one isn't submittable yet and a repair to queue. There is no submit call here or
 *  anywhere — submission stays manual on the BRAIN website. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

export type Board = Schemas['Board']
export type DayGroup = Schemas['DayGroup']
export type TypeGroup = Schemas['TypeGroup']
export type FamilyOut = Schemas['FamilyOut']
export type AlphaOut = Schemas['AlphaOut']
export type CheckOut = Schemas['CheckOut']
export type RepairOut = Schemas['RepairOut']
export type CorrelationOut = Schemas['CorrelationOut']
export type AddsOut = Schemas['AddsOut']
export type DescriptionOut = Schemas['DescriptionOut']
export type ChecklistItem = Schemas['ChecklistItem']
export type Missing = Schemas['Missing']
export type FetchRequest = Schemas['FetchRequest']
export type TaskStarted = Schemas['TaskStarted']
export type RepairRequest = Schemas['RepairRequest']
export type RepairQueued = Schemas['RepairQueued']

const B = '/api/forge/board'

export const board = {
  get: (days?: number) => http.get<Board>(`${B}${qs({ days })}`),
  fetch: (body: FetchRequest) => http.post<TaskStarted>(`${B}/fetch`, body),
  repair: (body: RepairRequest) => http.post<RepairQueued>(`${B}/repair`, body),
}
