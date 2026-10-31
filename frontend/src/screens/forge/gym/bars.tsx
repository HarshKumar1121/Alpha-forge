/**
 * Vertical bars around a zero line, for the few categorical figures on the tear sheet
 * (quintiles, IC decay, yearly Sharpe). Plain elements rather than a canvas, after the
 * Sparkline's reasoning: a handful of bars does not earn a chart library, and every bar keeps
 * its number as text so the colour is never the only carrier of the value.
 */

import { cn } from '@/lib/cn'
import { isNum } from '@/lib/format'

/** Fills, by what the bar is: a verdict (gain, loss, neutral) or a period (train, test). */
export const FILL = {
  profit: 'bg-pnl-positive',
  loss: 'bg-pnl-negative',
  neutral: 'bg-ink-tertiary',
  train: 'bg-status-running',
  test: 'bg-status-warning',
} as const

export interface Bar {
  key: string | number
  label: string
  value: number | null | undefined
  fill: keyof typeof FILL
  /** Read by screen readers and on hover: the bar in words. */
  title: string
}

export function Bars({
  bars,
  show,
  label,
}: {
  bars: Bar[]
  show: (v: number | null | undefined) => string
  label: string
}) {
  const values = bars.map((b) => b.value).filter(isNum)
  const max = Math.max(1e-12, ...values.map(Math.abs))
  const hasNegative = values.some((v) => v < 0)
  const hasPositive = values.some((v) => v > 0)
  // Where zero sits, as a share of the height from the top: the middle only when bars go both ways.
  const zero = hasNegative && hasPositive ? 0.5 : hasNegative ? 0 : 1

  return (
    <figure aria-label={label} className="flex min-w-0 flex-col gap-1">
      {/* The padding holds the figures printed past the tallest bars' ends. */}
      <div role="list" className="flex h-48 min-w-0 items-stretch gap-1.5 py-5">
        {bars.map((b) => {
          const v = isNum(b.value) ? b.value : 0
          const share = Math.abs(v) / max
          const top = v >= 0 ? zero * (1 - share) : zero
          const height = v >= 0 ? zero * share : (1 - zero) * share
          return (
            <div
              key={b.key}
              role="listitem"
              aria-label={b.title}
              title={b.title}
              className="relative min-w-0 flex-1"
            >
              <div
                className="absolute inset-x-0 border-t border-dotted border-ink-tertiary"
                style={{ top: `${zero * 100}%` }}
                aria-hidden
              />
              <div
                className={cn('absolute inset-x-[12%] rounded-xs', FILL[b.fill])}
                style={{ top: `${top * 100}%`, height: `${Math.max(height * 100, 0.5)}%` }}
                aria-hidden
              />
              <span
                className={cn(
                  'num absolute inset-x-0 truncate text-center text-caption text-ink-muted',
                  v >= 0 ? '-translate-y-full' : 'translate-y-0.5',
                )}
                style={{ top: `${(v >= 0 ? top : top + height) * 100}%` }}
                aria-hidden
              >
                {show(b.value)}
              </span>
            </div>
          )
        })}
      </div>
      <div className="flex min-w-0 gap-1.5" aria-hidden>
        {bars.map((b) => (
          <span
            key={b.key}
            className="num min-w-0 flex-1 truncate text-center text-caption text-ink-subtle"
          >
            {b.label}
          </span>
        ))}
      </div>
    </figure>
  )
}
