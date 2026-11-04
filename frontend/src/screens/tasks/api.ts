/** Tasks: what the labs added. Only the Tasks tab runs them. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

/** IDLE: not started. QUEUED: run, waiting for its cores to fit in the free slots. */
export type TaskStatus = Schemas['StudyStatus']
// forge: a lab Study's id is a number; a Forge run's id (Pipelines/Workbench task id, or a
// Workshop trial id) is projected here as a string by screens/forge/tasks-bridge.ts, so every
// Forge simulation can appear in this same list (CLAUDE.md: tag each with its source).
export type LabTask = Omit<Schemas['LabTask'], 'id'> & {
  id: number | string
  /** Which Forge screen produced this row. Absent (or `'lab'`) for an ordinary Study row. */
  source?: 'lab' | 'forge-pipeline' | 'forge-workbench' | 'forge-workshop'
}
export type LabTasks = Omit<Schemas['LabTasks'], 'tasks'> & { tasks: LabTask[] }
export type RankedAlpha = Omit<Schemas['RankedAlpha'], 'settings'> & {
  settings: { universe?: string; neutralization?: string; [key: string]: unknown } | null
}

/** A submittable Alpha from any task, with the task that found it. */
export type TaskAlpha = Omit<Schemas['TaskAlpha'], 'settings'> & {
  settings: RankedAlpha['settings']
}

export type PowerPoolCorrelation = Schemas['PowerPoolCorrelation']
export type PowerPoolRow = Schemas['PowerPoolRow']

const B = '/api/lab-tasks'

/** The most Alphas one task's results come back with: the backend's own ceiling. */
export const TOP_LIMIT = 5000

export const labTasks = {
  list: () => http.get<LabTasks>(B),
  /** Spends simulation quota. */
  runAll: () => http.post<LabTasks>(`${B}/run-all`),
  /** Spends simulation quota. Also resumes a paused task. */
  run: (id: number) => http.post<LabTask>(`${B}/${id}/run`),
  pause: (id: number) => http.post<LabTask>(`${B}/${id}/pause`),
  stop: (id: number) => http.post<LabTask>(`${B}/${id}/stop`),
  /** A blank name clears it. */
  rename: (id: number, name: string) => http.put<LabTask>(`${B}/${id}/name`, { name }),
  change: (id: number, body: { cores?: number; simulations?: number }) =>
    http.patch<LabTask>(`${B}/${id}`, body),
  remove: (id: number) => http.del<Schemas['TaskRemoved']>(`${B}/${id}`),
  top: (id: number, limit = 50) => http.get<RankedAlpha[]>(`${B}/${id}/top${qs({ limit })}`),
  /** Every Alpha from every task that nothing refuses: each check PASS, WARNING or PENDING. */
  submittable: () => http.get<TaskAlpha[]>(`${B}/submittable`),
  /** Measured locally against the submitted Power Pool, from the PnL already stored. */
  powerPoolFor: (alphaIds: string[]) =>
    http.post<PowerPoolCorrelation>(`${B}/power-pool-correlation`, { alphaIds }),
  /** Greedy submission, best After-Cost Sharpe first, worked out without submitting. */
  powerPoolOrder: (alphaIds: string[]) =>
    http.post<Schemas['SubmissionOrder']>(`${B}/power-pool-order`, { alphaIds }),
  /** Downloads PnL, then turnover for the Alphas that satisfy Power Pool Correlation. */
  powerPoolWorkflow: (alphaIds: string[]) =>
    http.post<Schemas['WorkflowStarted']>(`${B}/power-pool-workflow`, { alphaIds }),
  /** Asks BRAIN, in this order, for each Production Correlation it has not given yet. */
  prodCorrelation: (alphaIds: string[]) =>
    http.post<Schemas['WorkflowStarted']>(`${B}/prod-correlation`, { alphaIds }),
  /** Stops that check; the answers already back stay. */
  stopProdCorrelation: () => http.post<void>(`${B}/prod-correlation/stop`),
  stopPowerPoolWorkflow: () => http.post<void>(`${B}/power-pool-workflow/stop`),
}
