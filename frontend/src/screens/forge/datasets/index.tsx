/**
 * One dataset in one scope: what it covers, how crowded it is against what BRAIN pays for it,
 * and everything you have already done there.
 *
 * The question this screen answers before you spend a simulation: is this data worth working,
 * and have I worked it already?
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearch } from '@tanstack/react-router'
import { useMemo } from 'react'
import type { Scope } from '@/api/types'
import { DASH, fmt, isNum } from '@/lib/format'
import { multiplier } from '@/screens/data/state'
import { color } from '@/screens/pool/pnl-chart'
import {
  Badge,
  Empty,
  ErrorNotice,
  KV,
  LINK,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { Tooltip } from '@/ui/overlay'
import { ChartFigure, chartBase, EChart } from '../charts/echart'
import { type DatasetPage, forgeData } from '../data/api'

export function DatasetScreen() {
  const { datasetId } = useParams({ from: '/forge/datasets/$datasetId' })
  const scope = useSearch({ from: '/forge/datasets/$datasetId' }) as Scope
  const query = useQuery({
    queryKey: ['forge-dataset', scope, datasetId],
    queryFn: () => forgeData.dataset(scope, datasetId),
  })
  const page = query.data

  return (
    <Page>
      <PageHeader
        title={<span className="num">{datasetId}</span>}
        description={
          page
            ? `${page.dataset.name ?? datasetId} — ${page.scope.replaceAll('/', ' · ')}`
            : 'Loading this dataset…'
        }
        actions={
          <Link to="/data" className={LINK}>
            Back to Data Explorer
          </Link>
        }
      />
      {query.isError && <ErrorNotice error={query.error} title="Could not load this dataset" />}
      {!page && !query.isError && <Skeleton className="h-64" label="Loading the dataset" />}
      {page && (
        <>
          <Headline page={page} />
          <div className="grid gap-4 xl:grid-cols-2">
            <Coverage page={page} />
            <Scatter page={page} />
          </div>
          <History page={page} scope={scope} />
        </>
      )}
    </Page>
  )
}

function Headline({ page }: { page: DatasetPage }) {
  const d = page.dataset
  return (
    <Panel
      title="What BRAIN says about this dataset"
      description="What this tells you: how much of the market this data covers, what it pays, and how many people are already using it."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
        <Metric
          label="Coverage"
          value={fmt.pct(d.coverage, 0)}
          hint="Share of the universe with a value"
        />
        <Metric
          label="Fields"
          value={fmt.int(d.fieldCount)}
          hint="Data fields in this dataset here"
        />
        <Metric
          label="Value score"
          value={isNum(d.valueScore) ? fmt.ratio(d.valueScore, 1) : DASH}
          hint="BRAIN's own estimate of how useful the data is"
        />
        <Metric
          label="Pyramid ×"
          value={multiplier(d.accountMultiplier ?? d.pyramidMultiplier)}
          hint={
            page.pyramid.alphaCount == null
              ? 'Multiplier listed on the dataset'
              : `${fmt.int(page.pyramid.alphaCount)} of ${page.pyramid.needed} alphas submitted in this pyramid this quarter`
          }
          tone={page.pyramid.lit ? 'profit' : 'neutral'}
        />
        <Metric
          label="Crowding"
          value={d.crowding == null ? DASH : fmt.int(d.crowding * 100)}
          hint="0–100 against every dataset in this market"
          tone={(d.crowding ?? 0) > 0.8 ? 'warn' : 'neutral'}
        />
        <Metric
          label="Users · alphas"
          value={`${fmt.compact(d.userCount)} · ${fmt.compact(d.alphaCount)}`}
          hint="Researchers who used it, and alphas built on it, platform-wide"
        />
      </div>
      {d.description && <p className="text-body text-pretty text-ink-muted">{d.description}</p>}
      <div className="flex flex-wrap items-center gap-1">
        {page.fieldTypes.map((t) => (
          <Tooltip
            key={t.fieldType}
            content={
              t.fieldType === 'MATRIX'
                ? 'MATRIX: a value per instrument per day — what most operators expect.'
                : t.fieldType === 'VECTOR'
                  ? 'VECTOR: several values per instrument per day; needs an aggregating operator first.'
                  : t.fieldType === 'GROUP'
                    ? 'GROUP: a label to group by (sector, country), not a value to rank.'
                    : 'Field type as BRAIN reports it.'
            }
          >
            <Badge tone="outline">
              {t.fieldType} · {fmt.int(t.fields)}
            </Badge>
          </Tooltip>
        ))}
      </div>
    </Panel>
  )
}

function Coverage({ page }: { page: DatasetPage }) {
  const option = useMemo(() => {
    const base = chartBase()
    return {
      ...base,
      grid: { ...base.grid, left: 40 },
      tooltip: { ...base.tooltip, trigger: 'item' },
      xAxis: {
        type: 'category',
        data: page.coverageBins.map(
          (b) => `${Math.round(b.low * 100)}–${Math.round(b.high * 100)}%`,
        ),
        name: 'Instrument coverage',
        nameLocation: 'middle',
        nameGap: 28,
        ...base.axis,
        splitLine: { show: false },
        axisLabel: { ...base.axis.axisLabel, interval: 1, fontSize: 10 },
      },
      yAxis: { type: 'value', name: 'Fields', minInterval: 1, ...base.axis },
      series: [
        {
          type: 'bar',
          data: page.coverageBins.map((b) => b.fields),
          itemStyle: { color: color('color-link'), borderRadius: [2, 2, 0, 0] },
        },
      ],
    }
  }, [page.coverageBins])

  const empty = page.coverageBins.every((b) => b.fields === 0)
  return (
    <Panel title="Coverage of its fields">
      {empty ? (
        <Empty title="No fields downloaded for this dataset here">
          Download this market in BRAIN › Sync to see how much of the universe each field covers.
        </Empty>
      ) : (
        <ChartFigure caption="how many of this dataset's fields have values for most of the universe. Fields bunched at the left are thin: an alpha built on them trades few stocks, whatever its Sharpe.">
          <EChart option={option} label="Fields by instrument coverage" />
        </ChartFigure>
      )}
    </Panel>
  )
}

function Scatter({ page }: { page: DatasetPage }) {
  const option = useMemo(() => {
    const base = chartBase()
    const points = page.scatter.filter((p) => p.crowding != null && p.multiplier != null)
    const split = (current: boolean) =>
      points
        .filter((p) => p.current === current)
        .map((p) => [p.crowding, p.multiplier, p.datasetId, p.name ?? p.datasetId])
    return {
      ...base,
      tooltip: {
        ...base.tooltip,
        trigger: 'item',
        formatter: (p: { value: (string | number)[] }) =>
          `${p.value[2]}<br/>crowding ${Math.round(Number(p.value[0]) * 100)} · pays ×${Number(p.value[1]).toFixed(1)}`,
      },
      xAxis: {
        type: 'value',
        name: 'Crowding',
        nameLocation: 'middle',
        nameGap: 28,
        min: 0,
        max: 1,
        ...base.axis,
      },
      yAxis: { type: 'value', name: 'Pyramid ×', scale: true, ...base.axis },
      series: [
        {
          type: 'scatter',
          symbolSize: 7,
          data: split(false),
          itemStyle: { color: color('color-ink-tertiary', 0.8) },
        },
        {
          type: 'scatter',
          symbolSize: 13,
          data: split(true),
          itemStyle: { color: color('color-link') },
        },
      ],
    }
  }, [page.scatter])

  const enough = page.scatter.some((p) => p.crowding != null && p.multiplier != null)
  return (
    <Panel title="Crowding against what it pays">
      {!enough ? (
        <Empty title="Nothing to compare yet">
          This needs the market's datasets and your pyramid multipliers. Sync the market, and sign
          in to BRAIN so it can report your multipliers.
        </Empty>
      ) : (
        <ChartFigure caption="where this dataset (the large dot) sits among the market's others. Up and to the left is the good corner: BRAIN pays more there and fewer people are working it.">
          <EChart option={option} label="Crowding against pyramid multiplier" />
        </ChartFigure>
      )}
    </Panel>
  )
}

function History({ page, scope }: { page: DatasetPage; scope: Scope }) {
  const h = page.history
  const worked = h.alphas > 0 || h.sims > 0
  return (
    <Panel
      title="My history in this dataset"
      description={`What this tells you: what you have already tried here, across every universe of ${scope.region} D${scope.delay}, and how much of it BRAIN accepted.`}
      bodyClassName="flex flex-col gap-4 p-4"
    >
      {!worked ? (
        <Empty title="You have not worked this dataset yet">
          Pick a few of its fields in the Data Explorer, save them as a field set, and bind a
          template to it in the Template Library.
        </Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
            <Metric
              label="Simulations"
              value={fmt.int(h.sims)}
              hint="Sent to BRAIN from here, this region and delay"
            />
            <Metric
              label="Alphas"
              value={fmt.int(h.alphas)}
              hint="In your vault, reading this data"
            />
            <Metric
              label="Checked"
              value={fmt.int(h.checked)}
              hint="Alphas BRAIN has decided: passed every check, or failed one"
            />
            <Metric
              label="Pass rate"
              value={h.checked ? fmt.pct(h.passRate, 0) : DASH}
              hint={
                h.checked
                  ? `${h.passed} of ${h.checked} passed every BRAIN check`
                  : 'Nothing decided yet'
              }
              tone={h.checked && (h.passRate ?? 0) > 0 ? 'profit' : 'neutral'}
            />
            <Metric
              label="Families started"
              value={fmt.int(h.structuralFamilies)}
              hint="Distinct field combinations you have tried here (structural; PnL families arrive with the Submittable Board)"
            />
          </div>
          {h.byUniverse.length > 0 && (
            <KV
              className="text-body-compact"
              items={h.byUniverse.map((u) => [
                <span key={u.universe} className="num">
                  {u.universe}
                </span>,
                `${fmt.int(u.alphas)} alphas · ${fmt.int(u.sims)} sims · ${
                  u.checked ? `${fmt.pct(u.passRate, 0)} passed` : 'none decided'
                }`,
              ])}
            />
          )}
          <Notice tone="info">
            Selection and ranking use train metrics only. A pass rate counts BRAIN's own verdict on
            an alpha, never a local guess.
          </Notice>
        </>
      )}
    </Panel>
  )
}
