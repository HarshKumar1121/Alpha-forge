/**
 * Correlation Submit Helper: a deadline-day check for a hand-picked batch of alphas. For each
 * one it shows BRAIN's own self-correlation (vs your pool) and production correlation (vs
 * BRAIN's production universe) — kept as two separate numbers, never merged — a PASS/FAIL
 * verdict against this competition's own cutoff, and, when two or more alphas are checked, a
 * pairwise heatmap plus a "submit only these" recommendation for alphas that are secretly the
 * same bet. This is read-only decision support: nothing here submits an alpha; that stays a
 * manual step on the BRAIN website (CLAUDE.md rule 1).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Notice,
  Page,
  PageHeader,
  Panel,
  Progress,
  Skeleton,
  Textarea,
  type Tone,
} from '@/ui/kit'
import { type AlphaRow, type SubmitHelperResult, submitHelper } from './api'

const VERDICT_TONE: Record<AlphaRow['verdict'], Tone> = {
  PASS: 'profit',
  FAIL: 'loss',
  UNKNOWN: 'muted',
}

function parseIds(raw: string): string[] {
  return Array.from(
    new Set(
      raw
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  )
}

export function SubmitHelperScreen() {
  const [raw, setRaw] = useState('')
  const [threshold, setThreshold] = useState(0.7)
  const [checkTaskId, setCheckTaskId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const recentQuery = useQuery({
    queryKey: ['forge-submit-helper-recent'],
    queryFn: () => submitHelper.recent(50),
  })
  const resultQuery = useQuery({
    queryKey: ['forge-submit-helper-result'],
    queryFn: () => submitHelper.result(),
  })

  const tasksLive = useLive((s) => s.tasks)
  const checkTask = tasksLive?.tasks.find((t) => t.id === checkTaskId) ?? null
  const checking = checkTask != null && checkTask.state === 'running'

  const checkMutation = useMutation({
    mutationFn: () => submitHelper.check({ alphaIds: parseIds(raw), threshold }),
    onSuccess: (started) => setCheckTaskId(started.taskId),
  })

  useEffect(() => {
    if (checkTask && checkTask.state !== 'running') {
      void queryClient.invalidateQueries({ queryKey: ['forge-submit-helper-result'] })
      setCheckTaskId(null)
    }
  }, [checkTask, queryClient])

  const ids = parseIds(raw)

  return (
    <Page>
      <PageHeader
        title="Correlation Submit Helper"
        description="A last-mile check before a competition deadline: BRAIN's self- and production correlation for a batch of alphas, which pass this competition's cutoff, and whether any of the passers are secretly the same bet. Read-only decision support — nothing here submits anything; submission stays a manual step on the BRAIN website."
      />

      <Panel
        title="Alphas to check"
        description="What this tells you: paste alpha ids (comma or newline separated), or pick from recent pipeline alphas below, then set this competition's own production-correlation cutoff."
        bodyClassName="flex flex-col gap-4 p-4"
      >
        <Field label="Alpha ids" hint="Comma or newline separated, up to 20 at a time.">
          <Textarea
            rows={4}
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder={'ALPHA1\nALPHA2, ALPHA3'}
          />
        </Field>

        {recentQuery.data && recentQuery.data.length > 0 && (
          <div className="flex flex-col gap-2">
            <span className="text-body-compact text-ink-subtle">
              Or pick from recent pipeline alphas:
            </span>
            <div className="flex flex-wrap gap-2">
              {recentQuery.data.map((r) => (
                <Button
                  key={r.id}
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    setRaw((prev) => {
                      const ids2 = new Set(parseIds(prev))
                      ids2.add(r.alphaId)
                      return Array.from(ids2).join('\n')
                    })
                  }
                >
                  {r.alphaId}
                </Button>
              ))}
            </div>
          </div>
        )}

        <Field
          label="PC cutoff"
          hint="This competition's own pass/fail threshold on production correlation. Never assumed — set it per competition."
          className="w-40"
        >
          <Input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => {
              const n = Number(e.target.value)
              if (n > 0 && n <= 1) setThreshold(n)
            }}
          />
        </Field>

        <div>
          <Button disabled={checking || ids.length === 0} onClick={() => checkMutation.mutate()}>
            {checking ? 'Checking…' : `Check ${fmt.int(ids.length)} alpha(s)`}
          </Button>
        </div>
        {checkMutation.isError && (
          <ErrorNotice error={checkMutation.error} title="Could not start the check" />
        )}
      </Panel>

      {checkTask && checkTask.state === 'running' && (
        <Panel bodyClassName="flex flex-col gap-2 p-4">
          <Progress value={checkTask.progress ?? null} label="Checking" />
          <p className="text-body-compact text-ink-subtle">{checkTask.detail || 'Starting…'}</p>
        </Panel>
      )}

      {resultQuery.isError && (
        <ErrorNotice error={resultQuery.error} title="The last result could not load" />
      )}
      {resultQuery.isPending && <Skeleton className="h-48" label="Loading the last check" />}

      {resultQuery.data === null && !checking && (
        <Empty title="Nothing checked yet">
          Paste or pick alpha ids above, set this competition's cutoff, and run a check. Each
          correlation fetch is cached for an hour, so re-checking the same alphas soon after costs
          nothing more.
        </Empty>
      )}

      {resultQuery.data != null && <ResultBody result={resultQuery.data} />}
    </Page>
  )
}

function ResultBody({ result }: { result: SubmitHelperResult }) {
  const ranked = result.alphas // already lowest-self-correlation-first from the backend
  const recommended = new Set(result.recommended)

  return (
    <>
      <Notice tone="info" title="Summary">
        {result.summary}
      </Notice>

      <Panel
        title="Per-alpha correlation"
        description="What this tells you: self-correlation (vs your own pool, free local estimate on the Board but here BRAIN's own number) and production correlation (vs BRAIN's production universe) are always shown separately — never averaged into one figure. Verdict is PASS when production correlation is below this competition's own cutoff."
        bodyClassName="flex flex-col divide-y divide-hairline p-4"
      >
        {ranked.map((a) => (
          <AlphaCorrelationRow
            key={a.alphaId}
            alpha={a}
            threshold={result.threshold}
            recommended={recommended.has(a.alphaId)}
          />
        ))}
      </Panel>

      {Object.keys(result.matrix).length > 0 && (
        <Panel
          title="Pairwise correlation among the submitted alphas"
          description="What this tells you: how correlated each pair of your submitted alphas is with each other, from BRAIN's own self-correlation records. Darker cells are more correlated — two alphas above the mutual threshold are likely the same bet twice."
          bodyClassName="overflow-x-auto p-4"
        >
          <HeatmapTable matrix={result.matrix} ids={ranked.map((a) => a.alphaId)} />
        </Panel>
      )}
    </>
  )
}

function AlphaCorrelationRow({
  alpha,
  threshold,
  recommended,
}: {
  alpha: AlphaRow
  threshold: number
  recommended: boolean
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <code className="num min-w-0 break-all text-body-compact text-ink">{alpha.alphaId}</code>
      <div className="flex flex-wrap items-center gap-4 text-body-compact">
        <span className="text-ink-subtle">
          Self corr. <span className="num text-ink">{fmt.ratio(alpha.selfCorrelation)}</span>
        </span>
        <span className="text-ink-subtle">
          Prod corr. <span className="num text-ink">{fmt.ratio(alpha.prodCorrelation)}</span>{' '}
          <span className="text-ink-subtle">(cutoff {fmt.ratio(threshold)})</span>
        </span>
        <Badge tone={VERDICT_TONE[alpha.verdict]}>{alpha.verdict}</Badge>
        {recommended && <Badge tone="profit">Recommend submitting</Badge>}
        <span className="text-ink-subtle">
          last checked {alpha.lastChecked ? fmt.ago(alpha.lastChecked) : 'never'}
        </span>
      </div>
    </div>
  )
}

function heatColor(value: number): string {
  const a = Math.min(1, Math.abs(value))
  if (a > 0.5) return 'bg-loss-muted text-ink'
  if (a > 0.25) return 'bg-warn-muted text-ink'
  return 'bg-surface-2 text-ink-subtle'
}

function HeatmapTable({
  matrix,
  ids,
}: {
  matrix: Record<string, Record<string, number>>
  ids: string[]
}) {
  return (
    <table className="w-full border-collapse text-body-compact">
      <thead>
        <tr>
          <th className="p-2 text-left text-ink-subtle" />
          {ids.map((id) => (
            <th key={id} className="num p-2 text-left text-ink-subtle">
              {id}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {ids.map((rowId) => (
          <tr key={rowId}>
            <th className="num p-2 text-left text-ink-subtle">{rowId}</th>
            {ids.map((colId) => {
              if (rowId === colId) {
                return (
                  <td key={colId} className="num p-2 text-ink-subtle">
                    —
                  </td>
                )
              }
              const value = matrix[rowId]?.[colId] ?? matrix[colId]?.[rowId]
              return (
                <td
                  key={colId}
                  className={`num rounded-sm p-2 ${value != null ? heatColor(value) : 'text-ink-subtle'}`}
                >
                  {value != null ? fmt.ratio(value) : '—'}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
