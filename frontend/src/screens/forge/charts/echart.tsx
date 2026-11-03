/**
 * ECharts for Forge's heatmaps, scatters, bars and rolling-window lines. The main cumulative
 * PnL curve stays on lightweight-charts (`PortfolioChart`); this is for the smaller analytical
 * charts around it.
 *
 * Only the chart types and components in use are registered, so the bundle carries those and
 * nothing else. Colours come from the theme tokens through `color()`, the same bridge the PnL
 * charts use, because a canvas cannot read CSS variables.
 */

import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import type { EChartsCoreOption } from 'echarts/core'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { type ReactNode, useEffect, useRef } from 'react'
import { cn } from '@/lib/cn'
import { color } from '@/screens/pool/pnl-chart'

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  MarkLineComponent,
  CanvasRenderer,
])

export type ChartOption = EChartsCoreOption

/** Axis, grid and tooltip styling every Forge chart shares. */
export function chartBase() {
  const subtle = color('color-ink-subtle')
  const hairline = color('color-hairline')
  return {
    animation: false,
    textStyle: { color: subtle, fontFamily: 'inherit' },
    grid: { left: 48, right: 16, top: 16, bottom: 40 },
    tooltip: {
      backgroundColor: color('color-surface-3'),
      borderColor: color('color-hairline-strong'),
      textStyle: { color: color('color-ink'), fontSize: 12 },
    },
    axis: {
      axisLine: { lineStyle: { color: hairline } },
      axisTick: { lineStyle: { color: hairline } },
      splitLine: { lineStyle: { color: hairline, opacity: 0.5 } },
      axisLabel: { color: subtle },
      nameTextStyle: { color: subtle },
    },
  }
}

/** One ECharts canvas that follows its box's size and is disposed with the component. */
export function EChart({
  option,
  className,
  label,
}: {
  option: ChartOption
  className?: string
  label: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const instance = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chart.current = instance
    const resize = new ResizeObserver(() => instance.resize())
    resize.observe(ref.current)
    return () => {
      resize.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={ref} role="img" aria-label={label} className={cn('h-64 w-full', className)} />
}

/** A chart with the one line every chart owes its reader: what it tells you. */
export function ChartFigure({ caption, children }: { caption: ReactNode; children: ReactNode }) {
  return (
    <figure className="flex min-w-0 flex-col gap-2">
      {children}
      <figcaption className="text-body-compact text-pretty text-ink-subtle">
        What this tells you: {caption}
      </figcaption>
    </figure>
  )
}
