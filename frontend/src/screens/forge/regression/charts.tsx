/**
 * Chart builders for the Regression Lab, on top of the shared Forge ECharts primitives
 * (`../charts/echart.tsx`). Two series types the shared bundle does not register are needed
 * here — a `custom` series (for the coefficient error-bar whiskers) and a `heatmap` series with
 * `visualMap` (for the correlation matrix) — and since no other Forge chart uses either yet,
 * they're registered locally against this module's own `echarts/core` import rather than
 * widening `echart.tsx`'s shared `echarts.use([...])` for a single consumer. If a second
 * consumer shows up, promote this registration into `echart.tsx` instead of repeating it.
 */

import { CustomChart, HeatmapChart } from 'echarts/charts'
import { VisualMapComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { color } from '@/screens/pool/pnl-chart'
import type { ChartOption } from '../charts/echart'
import { chartBase } from '../charts/echart'
import type { FieldStatOut } from './api'

echarts.use([CustomChart, HeatmapChart, VisualMapComponent])

/** Bar + whisker chart: each field's coefficient, greyed out when its 95% interval straddles
 * zero (no reliable sign), green when it's positive and significant, red when negative and
 * significant — direction readable at a glance, not just from bar position. */
export function coefficientBarOption(fields: FieldStatOut[]): ChartOption {
  const base = chartBase()
  const ids = fields.map((f) => f.fieldId)
  const zeroCrossing = (f: FieldStatOut) => f.ciLow <= 0 && 0 <= f.ciHigh
  const positiveColor = color('color-pnl-positive')
  const negativeColor = color('color-pnl-negative')
  const greyColor = color('color-ink-tertiary', 0.4)
  const barColor = (f: FieldStatOut) =>
    zeroCrossing(f) ? greyColor : f.coef >= 0 ? positiveColor : negativeColor

  return {
    ...base,
    tooltip: {
      ...base.tooltip,
      trigger: 'axis',
      formatter: (params: unknown) => {
        const p = (Array.isArray(params) ? params[0] : params) as { dataIndex: number }
        const f = fields[p.dataIndex]
        if (!f) return ''
        return `${f.fieldId}<br/>coef ${f.coef.toFixed(4)} [${f.ciLow.toFixed(4)}, ${f.ciHigh.toFixed(4)}]<br/>p ${f.pValue.toFixed(3)}`
      },
    },
    xAxis: { type: 'category', data: ids, ...base.axis },
    yAxis: { type: 'value', name: 'Coefficient', ...base.axis },
    series: [
      {
        type: 'bar',
        name: 'Coefficient',
        barWidth: 24,
        data: fields.map((f) => ({
          value: f.coef,
          itemStyle: { color: barColor(f) },
        })),
      },
      {
        type: 'custom',
        name: 'Confidence interval',
        renderItem: (
          _params: unknown,
          api: {
            value: (dim: number) => number
            coord: (point: [number, unknown]) => [number, number]
            size: (dims: [number, number]) => [number, number]
            style: () => Record<string, unknown>
          },
        ) => {
          const idx = Number(api.value(0))
          const lo = Number(api.value(1))
          const hi = Number(api.value(2))
          const topPoint = api.coord([idx, hi])
          const bottomPoint = api.coord([idx, lo])
          const capHalf = 6
          const strokeColor = color('color-ink-muted')
          return {
            type: 'group',
            children: [
              {
                type: 'line',
                shape: { x1: topPoint[0], y1: topPoint[1], x2: bottomPoint[0], y2: bottomPoint[1] },
                style: { stroke: strokeColor, lineWidth: 1.5 },
              },
              {
                type: 'line',
                shape: {
                  x1: topPoint[0] - capHalf,
                  y1: topPoint[1],
                  x2: topPoint[0] + capHalf,
                  y2: topPoint[1],
                },
                style: { stroke: strokeColor, lineWidth: 1.5 },
              },
              {
                type: 'line',
                shape: {
                  x1: bottomPoint[0] - capHalf,
                  y1: bottomPoint[1],
                  x2: bottomPoint[0] + capHalf,
                  y2: bottomPoint[1],
                },
                style: { stroke: strokeColor, lineWidth: 1.5 },
              },
            ],
          }
        },
        encode: { x: 0 },
        data: fields.map((f, i) => [i, f.ciLow, f.ciHigh]),
        z: 10,
      },
    ],
  }
}

/** Pairwise correlation among the chosen X fields themselves — redundancy made visible before
 * even reading the VIF numbers. */
export function correlationHeatmapOption(
  matrix: Record<string, Record<string, number>>,
): ChartOption {
  const base = chartBase()
  const ids = Object.keys(matrix)
  const cells: [number, number, number][] = []
  ids.forEach((a, i) => {
    ids.forEach((b, j) => {
      cells.push([j, i, Number((matrix[a]?.[b] ?? 0).toFixed(3))])
    })
  })

  return {
    ...base,
    tooltip: {
      ...base.tooltip,
      position: 'top',
      formatter: (p: { value: [number, number, number] }) =>
        `${ids[p.value[1]]} × ${ids[p.value[0]]}<br/>corr ${p.value[2].toFixed(3)}`,
    },
    grid: { ...base.grid, left: 90, bottom: 60 },
    xAxis: { type: 'category', data: ids, splitArea: { show: true }, ...base.axis },
    yAxis: { type: 'category', data: ids, splitArea: { show: true }, ...base.axis },
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      show: false,
      inRange: {
        color: [color('color-pnl-negative'), color('color-surface-3'), color('color-link')],
      },
    },
    series: [
      {
        type: 'heatmap',
        data: cells,
        label: {
          show: true,
          color: color('color-ink'),
          formatter: (p: { value: [number, number, number] }) => p.value[2].toFixed(2),
        },
      },
    ],
  }
}

/** Standalone correlation with the target (x) against the coefficient once every other chosen
 * field is controlled for (y). A field that drops toward the diagonal/zero once others are
 * added is redundant; one that stays strong is carrying independent signal. */
export function standaloneVsControlledScatterOption(fields: FieldStatOut[]): ChartOption {
  const base = chartBase()
  return {
    ...base,
    tooltip: {
      ...base.tooltip,
      trigger: 'item',
      formatter: (p: { value: (string | number)[] }) =>
        `${p.value[2]}<br/>standalone corr ${Number(p.value[0]).toFixed(3)} · controlled coef ${Number(p.value[1]).toFixed(4)}`,
    },
    xAxis: {
      type: 'value',
      name: 'Standalone correlation',
      nameLocation: 'middle',
      nameGap: 28,
      min: -1,
      max: 1,
      ...base.axis,
    },
    yAxis: { type: 'value', name: 'Coefficient (controlled)', scale: true, ...base.axis },
    series: [
      {
        type: 'scatter',
        symbolSize: 10,
        data: fields.map((f) => [f.standaloneCorr, f.coef, f.fieldId]),
        itemStyle: { color: color('color-link') },
      },
      {
        type: 'line',
        showSymbol: false,
        data: [
          [-1, -1],
          [1, 1],
        ],
        lineStyle: { type: 'dashed', color: color('color-ink-tertiary') },
        tooltip: { show: false },
      },
    ],
  }
}
