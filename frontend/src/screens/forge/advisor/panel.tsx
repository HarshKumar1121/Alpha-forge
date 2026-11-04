/** Run Advisor panels: a plain-language reading before a run (field behaviour, pairing, track
 *  record) and after one lands (verdict plus one concrete next step). Pure synthesis over what
 *  other Forge screens already computed — this never simulates anything on its own. */

import { useQuery } from '@tanstack/react-query'
import type { Scope } from '@/api/types'
import { ErrorNotice, Notice, Skeleton } from '@/ui/kit'
import { advisor, type PostRunOut, preflightFromScope } from './api'

/** What this tells you: field behaviour, any pairing concern and how this template has scored
 * on data like this before — before you spend a simulation on it. */
export function PreflightAdvisor({
  scope,
  templateId,
  fieldIds,
}: {
  scope: Scope
  templateId: string | null
  fieldIds: string[]
}) {
  const enabled = Boolean(templateId) && fieldIds.length > 0
  const preflight = useQuery({
    queryKey: ['forge-advisor', 'preflight', scope, templateId, fieldIds],
    queryFn: () => preflightFromScope(scope, templateId as string, fieldIds),
    enabled,
  })

  if (!enabled) return null
  if (preflight.isPending) return <Skeleton className="h-16" label="Reading these fields" />
  if (preflight.isError) {
    return <ErrorNotice error={preflight.error} title="Could not read these fields" />
  }

  const { paragraph, warnings } = preflight.data
  return (
    <Notice tone={warnings.length > 0 ? 'warn' : 'info'} title="Before you run this">
      <p>{paragraph}</p>
    </Notice>
  )
}

const ACTION_TONE: Record<PostRunOut['actionKind'], 'info' | 'warn' | 'error'> = {
  repair: 'warn',
  field_swap: 'warn',
  close: 'error',
  wait: 'info',
  fetch: 'info',
  ready: 'info',
}

/** What this tells you: train/test Sharpe, factor exposure and the Board's check status tied
 * into one verdict, and the single next thing worth doing about it. */
export function PostrunAdvisor({ alphaId }: { alphaId: string }) {
  const postrun = useQuery({
    queryKey: ['forge-advisor', 'postrun', alphaId],
    queryFn: () => advisor.postrun(alphaId),
  })

  if (postrun.isPending) return <Skeleton className="h-16" label="Reading this run" />
  if (postrun.isError) {
    return <ErrorNotice error={postrun.error} title="Could not read this alpha yet" />
  }

  const { verdict, action, actionKind } = postrun.data
  return (
    <Notice tone={ACTION_TONE[actionKind]} title="What this run means">
      <p>{verdict}</p>
      <p className="mt-1 font-medium text-ink">{action}</p>
    </Notice>
  )
}
