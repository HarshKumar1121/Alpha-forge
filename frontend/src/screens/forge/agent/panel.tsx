/**
 * The Agent panel: a chat with the AI agent, embeddable on any Forge screen. It reads whatever
 * `context` the host screen hands it (e.g. which trial or alpha is open) so the agent can answer
 * about what the user is actually looking at. The agent may read Forge's own history and
 * propose — a hypothesis, a pipeline run, a Court promotion — but it never submits, and nothing
 * it proposes spends anything until a human approves it from the relevant screen's own proposal
 * list (CLAUDE.md rule 5). This panel shows what it read and proposed, not an approve button of
 * its own, so "it spent something" is never a thing a chat turn alone can cause.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlusIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '@/api/http'
import { fmt } from '@/lib/format'
import { Button, Empty, ErrorNotice, Notice, Panel, Spinner, Textarea } from '@/ui/kit'
import { Select } from '@/ui/overlay'
import {
  AGENT_ROLES,
  type AgentChatMessage,
  type AgentChatReply,
  type AgentRole,
  agent,
} from './api'

export function AgentPanel({ context = {} }: { context?: Record<string, unknown> }) {
  const queryClient = useQueryClient()
  const [threadId, setThreadId] = useState<number | null>(null)
  const [role, setRole] = useState<AgentRole>('researcher')
  const [text, setText] = useState('')
  const [last, setLast] = useState<AgentChatReply | null>(null)
  const scroller = useRef<HTMLElement>(null)

  const threads = useQuery({
    queryKey: ['forge-agent', 'threads'],
    queryFn: () => agent.chatThreads(),
  })
  const thread = useQuery({
    queryKey: ['forge-agent', 'thread', threadId],
    queryFn: () => agent.chatThread(threadId as number),
    enabled: threadId !== null,
  })

  const say = useMutation({
    mutationFn: (message: string) =>
      agent.say({ text: message, thread_id: threadId, role, context }),
    onSuccess: (reply) => {
      setLast(reply)
      setText('')
      void queryClient.invalidateQueries({ queryKey: ['forge-agent', 'threads'] })
      if (reply.threadId !== threadId) setThreadId(reply.threadId)
      void queryClient.invalidateQueries({ queryKey: ['forge-agent', 'thread', reply.threadId] })
    },
  })

  const messages = threadId !== null ? (thread.data?.messages ?? []) : []
  const lastAssistantId = messages.findLast((m) => m.role === 'assistant')?.id

  // biome-ignore lint/correctness/useExhaustiveDependencies: pin the view to the newest turn
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [messages.length, say.isPending])

  const threadItems = [
    { value: 'new', label: 'New conversation' },
    ...(threads.data ?? []).map((t) => ({ value: String(t.id), label: t.title })),
  ]

  const canSend = !!text.trim() && !say.isPending
  const send = () => canSend && say.mutate(text.trim())

  return (
    <Panel
      title="Agent"
      description="Reads what you've already run and proposes next steps. It never submits an alpha, and anything it proposes spends nothing until you approve it yourself."
      actions={
        <>
          <Select
            label="Role"
            items={AGENT_ROLES}
            value={role}
            onChange={setRole}
            disabled={say.isPending}
          />
          <Select
            label="Conversation"
            className="max-w-48"
            items={threadItems}
            value={threadId !== null ? String(threadId) : 'new'}
            onChange={(v) => setThreadId(v === 'new' ? null : Number(v))}
            disabled={say.isPending}
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setThreadId(null)
              setLast(null)
            }}
          >
            <PlusIcon aria-hidden />
            New
          </Button>
        </>
      }
      bodyClassName="flex flex-col gap-3"
    >
      {thread.isError && (
        <ErrorNotice title="Could not open this conversation" error={thread.error} />
      )}

      <section
        ref={scroller}
        aria-label="Agent messages"
        className="flex h-64 max-h-[50vh] flex-col gap-3 overflow-auto rounded-md border border-hairline bg-canvas p-3"
      >
        {messages.length === 0 && !say.isPending && (
          <Empty title="Ask it anything about your research" className="m-auto">
            It can list your hypotheses, lessons and past runs, explain a check, and propose a
            hypothesis or a pipeline run for you to approve.
          </Empty>
        )}
        {messages.map((m) => (
          <Turn key={m.id} message={m} reply={m.id === lastAssistantId ? last : null} />
        ))}
        {say.isPending && (
          <p className="flex items-center gap-2 text-body-compact text-ink-subtle">
            <Spinner className="size-3.5" />
            Thinking…
          </p>
        )}
      </section>

      {say.isError && (
        <Notice tone="error" title="The agent could not answer">
          {errorMessage(say.error)}
        </Notice>
      )}

      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <Textarea
          aria-label="Message the agent"
          placeholder="Ask about your hypotheses, lessons or past runs"
          value={text}
          disabled={say.isPending}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              send()
            }
          }}
        />
        <Button
          variant="primary"
          type="submit"
          size="sm"
          className="self-end"
          loading={say.isPending}
          disabled={!canSend}
        >
          Ask
        </Button>
      </form>
    </Panel>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[85%] self-end rounded-lg bg-surface-2 px-3 py-2 text-body break-words whitespace-pre-wrap text-ink">
      {text}
    </div>
  )
}

function Turn({ message, reply }: { message: AgentChatMessage; reply: AgentChatReply | null }) {
  if (message.role === 'user') return <UserBubble text={message.text} />
  const toolCalls = reply?.toolCalls ?? []
  return (
    <div className="flex max-w-[92%] flex-col gap-1.5">
      <p className="text-body text-pretty break-words whitespace-pre-wrap text-ink-muted">
        {message.text}
      </p>
      {toolCalls.length > 0 && (
        <div className="rounded-sm border border-hairline-subtle bg-surface-1 p-2 text-body-compact text-ink-subtle">
          <span className="text-ink">What it read or proposed</span>
          <ul className="ml-4 list-disc">
            {toolCalls.map((call, i) => (
              <li key={i}>{JSON.stringify(call)}</li>
            ))}
          </ul>
        </div>
      )}
      {reply && (
        <p className="num text-body-compact text-ink-subtle">
          {reply.role} · {reply.model} · {fmt.int(reply.usage['totalTokens'] ?? null)} tokens
        </p>
      )}
    </div>
  )
}
