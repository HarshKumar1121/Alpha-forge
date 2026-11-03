/** The AI agent: it may read Forge's own history and propose a hypothesis, a pipeline run or
 *  (from the Workshop) a Court promotion — never submit, and it spends only from the `reserve`
 *  lane, only after a human clicks Approve (CLAUDE.md rule 5). This client backs both the
 *  Workshop screen and the reusable Agent panel embedded on other screens. */

import type { components } from '@/api/generated'
import { http, qs } from '@/api/http'

type Schemas = components['schemas']

export type Hypothesis = Schemas['Hypothesis']
export type ProposeHypothesisRequest = Schemas['ProposeHypothesis']
export type Lesson = Schemas['Lesson']
export type RecordLessonRequest = Schemas['RecordLesson']
export type Failure = Schemas['alpha_harness__api__forge_agent__Failure']
export type PipelineRunSummary = Schemas['PipelineRunSummary']
export type Proposal = Schemas['Proposal']
export type ProposePipelineRunRequest = Schemas['ProposePipelineRun']
export type ToolSchema = Schemas['ToolSchema']

export type AgentChatMessage = Schemas['alpha_harness__api__forge_agent__ChatMessageOut']
export type AgentChatThread = Schemas['alpha_harness__api__forge_agent__ChatThreadOut']
export type AgentChatThreadSummary = Schemas['alpha_harness__api__forge_agent__ChatThreadSummary']
export type AgentChatReply = Schemas['alpha_harness__api__forge_agent__ChatReply']

/** The roles `forge/agent/chat.py` knows, each with its own system prompt. */
export type AgentRole = 'researcher' | 'critic' | 'analyst' | 'tutor'

export const AGENT_ROLES: { value: AgentRole; label: string }[] = [
  { value: 'researcher', label: 'Researcher' },
  { value: 'critic', label: 'Critic' },
  { value: 'analyst', label: 'Analyst' },
  { value: 'tutor', label: 'Tutor' },
]

export interface AgentChatSayRequest {
  text: string
  thread_id?: number | null
  role?: AgentRole
  model?: string | null
  /** Whatever the embedding screen knows that the agent couldn't otherwise see, e.g. `{screen, trialId}`. */
  context?: Record<string, unknown>
}

const B = '/api/forge/agent'

export const agent = {
  tools: () => http.get<ToolSchema[]>(`${B}/tools`),
  hypotheses: (status?: string) => http.get<Hypothesis[]>(`${B}/hypotheses${qs({ status })}`),
  proposeHypothesis: (body: ProposeHypothesisRequest) =>
    http.post<Hypothesis>(`${B}/hypotheses`, body),
  lessons: () => http.get<Lesson[]>(`${B}/lessons`),
  recordLesson: (body: RecordLessonRequest) => http.post<Lesson>(`${B}/lessons`, body),
  failures: (days = 30) => http.get<Failure[]>(`${B}/failures${qs({ days })}`),
  runs: (limit = 20) => http.get<PipelineRunSummary[]>(`${B}/runs${qs({ limit })}`),
  proposals: (status?: string) => http.get<Proposal[]>(`${B}/proposals${qs({ status })}`),
  proposePipelineRun: (body: ProposePipelineRunRequest) =>
    http.post<Proposal>(`${B}/proposals`, body),
  /** The only route here that spends anything — the reserve lane, once a human calls it. */
  approve: (id: number) => http.post<Proposal>(`${B}/proposals/${id}/approve`),
  reject: (id: number) => http.post<Proposal>(`${B}/proposals/${id}/reject`),
  weeklySummary: () => http.post<Lesson>(`${B}/weekly-summary`),
  chatThreads: () => http.get<AgentChatThreadSummary[]>(`${B}/chat/threads`),
  chatThread: (id: number) => http.get<AgentChatThread>(`${B}/chat/threads/${id}`),
  deleteChatThread: (id: number) => http.del<void>(`${B}/chat/threads/${id}`),
  say: (body: AgentChatSayRequest) => http.post<AgentChatReply>(`${B}/chat`, body),
}
