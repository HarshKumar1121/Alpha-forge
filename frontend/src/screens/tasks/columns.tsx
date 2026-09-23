/** Columns the Tasks list and a task's results screen both show, so they cannot drift apart. */

import { DASH } from '@/lib/format'
import type { RankedAlpha } from '@/screens/tasks/api'
import type { Column } from '@/ui/table'

/** After-Cost Sharpe's header. The figure is scaled to ten years of data, which the name alone
 *  does not say, so the header does on hover. */
export const AFTER_COST_HEADER = (
  <span title="After-cost t-stat ÷ √10: the Sharpe after 5 bps trading costs, times √(years of data ÷ 10). Every Alpha is normalized to 10 years, so fewer years of data score lower.">
    After-Cost Sharpe
  </span>
)

/**
 * How an Alpha is held to its instruments' liquidity. BRAIN refuses Max Trade and Max
 * Position both ON, so the two settings are one three-way choice and read better as one
 * column than as two columns of ON/OFF.
 */
export function investability(r: RankedAlpha): string {
  if (r.settings?.['maxTrade'] === 'ON') return 'Max Trade'
  if (r.settings?.['maxPosition'] === 'ON') return 'Max Position'
  return 'None'
}

export const INVESTABILITY: Column<RankedAlpha> = {
  key: 'investability',
  header: 'Investability',
  width: 'minmax(108px,1fr)',
  sortable: true,
  cell: (r) => <span className="num truncate">{investability(r)}</span>,
}

export const FAILED_CHECKS: Column<RankedAlpha> = {
  key: 'failed',
  header: 'Checks Failed',
  width: 'minmax(140px,1.6fr)',
  sortable: true,
  cell: (r) =>
    r.failedChecks.length === 0 ? (
      <span className="text-ink-subtle">{DASH}</span>
    ) : (
      <span className="truncate" title={r.failedChecks.join(', ')}>
        {r.failedChecks.map((name, i) => (
          <span
            key={name}
            // A check that failed without refusing the Alpha is shown, because it did fail,
            // but not in the colour that means "this is why you cannot submit".
            className={r.refusedBy.includes(name) ? 'text-pnl-negative' : 'text-ink-subtle'}
          >
            {i > 0 && ', '}
            {name}
          </span>
        ))}
      </span>
    ),
}

export const DELAY: Column<RankedAlpha> = {
  key: 'delay',
  header: 'Delay',
  width: 'minmax(64px,0.6fr)',
  sortable: true,
  cell: (r) => (
    <span className="num">{r.settings?.['delay'] == null ? DASH : `D${r.settings['delay']}`}</span>
  ),
}
