/**
 * The description BRAIN asks for before it takes a Power Pool alpha.
 *
 * The draft is written here from the template's rationale and BRAIN's own description of each
 * field and operator — a starting point, not an answer. It is yours to edit and save, and no
 * code path submits it: pasting it into BRAIN is a thing you do, on BRAIN's website.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CopyIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { Badge, Button, Field, Notice, Panel, Skeleton, Textarea } from '@/ui/kit'
import { type DescriptionDraft, studio } from './api'

export function DescriptionPanel({
  expression,
  templateId,
  fieldSetId,
  nuggetId,
  scope,
}: {
  expression: string
  templateId: string | undefined
  fieldSetId: number | undefined
  nuggetId: string | undefined
  scope: string
}) {
  const client = useQueryClient()
  const draft = useQuery({
    queryKey: ['forge-studio', 'description', expression, nuggetId],
    queryFn: () => studio.draft({ expression, templateId, fieldSetId, nuggetId }),
  })

  const [parts, setParts] = useState<Pick<
    DescriptionDraft,
    'idea' | 'dataRationale' | 'operatorRationale'
  > | null>(null)

  // The draft arrives after the panel opens, and a later alpha brings a different one.
  useEffect(() => {
    if (draft.data)
      setParts({
        idea: draft.data.idea,
        dataRationale: draft.data.dataRationale,
        operatorRationale: draft.data.operatorRationale,
      })
  }, [draft.data])

  const save = useMutation({
    mutationFn: () =>
      studio.saveDescription({
        expression,
        idea: parts?.idea ?? '',
        dataRationale: parts?.dataRationale ?? '',
        operatorRationale: parts?.operatorRationale ?? '',
        templateId: templateId ?? null,
        scope,
      }),
    onSuccess: (saved) => {
      client.setQueryData(['forge-studio', 'description', expression], saved)
      toast.success('Description saved', {
        description: 'Kept with this expression. Nothing is submitted.',
      })
    },
    onError: (e) => toast.error('Could not save it', { description: errorMessage(e) }),
  })

  const copy = async () => {
    const text = [
      `Idea: ${parts?.idea ?? ''}`,
      `Rationale for data used: ${parts?.dataRationale ?? ''}`,
      `Rationale for operators used: ${parts?.operatorRationale ?? ''}`,
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast.success('Copied in BRAIN’s template', {
        description: 'Paste it into the Power Pool description box on BRAIN.',
      })
    } catch {
      toast.error('Could not write to the clipboard.')
    }
  }

  const thin = draft.data?.thin ?? []

  return (
    <Panel
      title="Power Pool description"
      description="What this tells you: BRAIN wants three headings, each with real text, before it takes a Power Pool alpha. This is a draft from the template's reasoning and BRAIN's own field descriptions — edit it until it says what you actually think."
      actions={
        draft.data?.edited ? <Badge tone="profit">Saved</Badge> : <Badge tone="muted">Draft</Badge>
      }
      bodyClassName="flex flex-col gap-3 p-4"
    >
      {draft.isPending && <Skeleton className="h-40" label="Drafting" />}
      {parts && (
        <>
          {(draft.data?.nuggetRationale || draft.data?.nuggetClaim) && (
            <Notice tone="info" title="From the nugget this alpha was bound to">
              {draft.data.nuggetRationale && (
                <p className="text-body-compact text-ink">
                  <span className="font-medium">Rationale: </span>
                  {draft.data.nuggetRationale}
                </p>
              )}
              {draft.data.nuggetClaim && (
                <p className="mt-1 text-body-compact text-ink">
                  <span className="font-medium">Claim: </span>
                  {draft.data.nuggetClaim}
                </p>
              )}
            </Notice>
          )}
          <Field label="Idea" hint="What the alpha bets on, in plain words.">
            <Textarea
              rows={3}
              value={parts.idea}
              onChange={(e) => setParts({ ...parts, idea: e.target.value })}
            />
          </Field>
          <Field label="Rationale for data used" hint="Why this data should carry that signal.">
            <Textarea
              rows={4}
              value={parts.dataRationale}
              onChange={(e) => setParts({ ...parts, dataRationale: e.target.value })}
            />
          </Field>
          <Field
            label="Rationale for operators used"
            hint="Why each operator is the right way to read it."
          >
            <Textarea
              rows={4}
              value={parts.operatorRationale}
              onChange={(e) => setParts({ ...parts, operatorRationale: e.target.value })}
            />
          </Field>

          {thin.length > 0 && (
            <Notice tone="warn" title="BRAIN wants more here">
              {thin.join(', ')} {thin.length === 1 ? 'is' : 'are'} still shorter than BRAIN accepts.
              Say what you actually believe rather than padding it — a reviewer reads this.
            </Notice>
          )}

          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
              Save description
            </Button>
            <Button variant="ghost" onClick={() => void copy()}>
              <CopyIcon aria-hidden />
              Copy for BRAIN
            </Button>
          </div>
        </>
      )}
    </Panel>
  )
}
