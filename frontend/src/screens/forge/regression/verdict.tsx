/** The regression's plain-language verdict: the same "numbers in, prose out" convention the
 *  Analytics tear sheet's own verdict panel uses (`ts.plain_verdict`) — `plain_summary` and
 *  `recommendation.reason` are generated from the actual fit, not guessed by an LLM. */

import { Badge, Notice } from '@/ui/kit'
import type { RecommendationOut, WinnerOut } from './api'

export function RegressionVerdict({
  summary,
  recommendation,
  winner,
  redundancyGroups,
}: {
  summary: string
  recommendation: RecommendationOut
  winner: WinnerOut
  redundancyGroups: string[][]
}) {
  return (
    <div className="flex flex-col gap-3">
      <Notice
        tone={winner.fieldId ? 'info' : 'warn'}
        title={winner.fieldId ? 'Winner' : 'No winner yet'}
      >
        {winner.message}
      </Notice>
      <p className="text-body text-ink">{summary}</p>
      <p className="text-body-compact text-ink-subtle">{recommendation.reason}</p>
      {redundancyGroups.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-caption font-medium text-ink-muted">
            Moving together — keep only one
          </span>
          <ul className="ml-4 list-disc text-body-compact text-ink-subtle">
            {redundancyGroups.map((group) => (
              <li key={group.join('-')}>
                These {group.length} fields move together — keep only one:{' '}
                <span className="num">{group.join(', ')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-caption font-medium text-ink-muted">Keep</span>
        {recommendation.keep.length === 0 ? (
          <Badge tone="muted">none</Badge>
        ) : (
          recommendation.keep.map((id) => (
            <Badge key={id} tone="profit">
              {id}
            </Badge>
          ))
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-caption font-medium text-ink-muted">Drop</span>
        {recommendation.drop.length === 0 ? (
          <Badge tone="muted">none</Badge>
        ) : (
          recommendation.drop.map((id) => (
            <Badge key={id} tone="loss">
              {id}
            </Badge>
          ))
        )}
      </div>
    </div>
  )
}
