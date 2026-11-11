/**
 * Multi-Field Regression Lab: a Workbench panel that screens candidate fields against a target
 * with a real OLS fit — multicollinearity (VIF), a correlation heatmap, a split-half stability
 * check and a plain-language verdict — *before* any field reaches template binding (CLAUDE.md:
 * field screening happens before template binding, not after).
 *
 * `/prescreen` is free and runs as soon as 2+ fields are in the tray; `/run` spends the
 * `explore` lane (CLAUDE.md rule 3) and only fires when the user asks for it.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { type Scope, toScopeBody } from '@/api/types'
import { fmt } from '@/lib/format'
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Input,
  Metric,
  Notice,
  Panel,
  Segmented,
  Skeleton,
} from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { ChartFigure, EChart } from '../charts/echart'
import { type RegressionRunOut, regression } from './api'
import {
  coefficientBarOption,
  correlationHeatmapOption,
  standaloneVsControlledScatterOption,
} from './charts'
import { RegressionVerdict } from './verdict'

const MIN_FIELDS = 2
const MAX_FIELDS = 8

/** BRAIN's standard window: roughly the last four years, ending today. */
function defaultDateRange(): { start: string; end: string } {
  const end = new Date()
  const start = new Date(end)
  start.setFullYear(start.getFullYear() - 4)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { start: iso(start), end: iso(end) }
}

type TargetKind = 'vault' | 'gym' | 'field'

export function RegressionLabPanel({
  scope,
  fieldIds,
  onSurvivors,
  onPickSuggestion,
}: {
  scope: Scope
  fieldIds: string[]
  onSurvivors: (ids: string[]) => void
  /** Replace the field tray with a suggested combination's fields, one click. */
  onPickSuggestion: (ids: string[]) => void
}) {
  const [targetKind, setTargetKind] = useState<TargetKind>('field')
  const [targetFieldId, setTargetFieldId] = useState<string | null>(null)
  const [targetRef, setTargetRef] = useState('')
  const [dateRange, setDateRange] = useState(defaultDateRange)
  const [result, setResult] = useState<RegressionRunOut | null>(null)
  const [runError, setRunError] = useState<unknown>(null)
  const [running, setRunning] = useState(false)

  const candidateCount = fieldIds.length
  const inRange = candidateCount >= MIN_FIELDS && candidateCount <= MAX_FIELDS

  const suggestions = useQuery({
    queryKey: ['forge-regression-suggestions', scope],
    queryFn: () => regression.suggestions(scope),
  })

  const prescreen = useQuery({
    queryKey: ['forge-regression-prescreen', scope, fieldIds],
    queryFn: () => regression.prescreen(scope, fieldIds),
    enabled: inRange,
  })

  const ref = targetKind === 'field' ? (targetFieldId ?? '') : targetRef.trim()
  const canRun = inRange && ref.length > 0

  const runRegression = async () => {
    setRunError(null)
    setResult(null)
    setRunning(true)
    try {
      const out = await regression.run({
        scope: toScopeBody(scope),
        fieldIds,
        target: { kind: targetKind, ref },
        dateRange,
      })
      setResult(out)
    } catch (e) {
      setRunError(e)
    } finally {
      setRunning(false)
    }
  }

  const barOption = useMemo(() => (result ? coefficientBarOption(result.fields) : null), [result])
  const heatmapOption = useMemo(
    () => (result ? correlationHeatmapOption(result.corrMatrix) : null),
    [result],
  )
  const scatterOption = useMemo(
    () => (result ? standaloneVsControlledScatterOption(result.fields) : null),
    [result],
  )

  return (
    <Panel
      title="Regression Lab"
      description="What this tells you: before you bind these fields to a template, which ones actually carry independent, stable signal against a target — and which are redundant or noise. Runs train-period data only (CLAUDE.md rule 6)."
      actions={
        <Badge tone="outline">
          {candidateCount} candidate{candidateCount === 1 ? '' : 's'}
        </Badge>
      }
      bodyClassName="flex flex-col gap-4 p-4"
    >
      {suggestions.data && suggestions.data.length > 0 && (
        <Field
          label="Try a known-strong combination"
          hint="Loads a saved Data Nugget's fields straight into the tray below — a real combination you've already vetted, not an empty start."
        >
          <div className="flex flex-wrap gap-1.5">
            {suggestions.data.map((s) => (
              <Button
                key={s.fieldIds.join(',')}
                variant="secondary"
                size="sm"
                onClick={() => onPickSuggestion(s.fieldIds)}
              >
                {s.label} <Badge tone="outline">{s.category}</Badge>
              </Button>
            ))}
          </div>
        </Field>
      )}

      {!inRange && (
        <Notice tone="info" title="Pick 2–8 fields in the tray below first">
          The Regression Lab screens candidate fields against a target; it needs at least two and at
          most eight picked in the field tray.
        </Notice>
      )}

      {inRange && prescreen.isError && (
        <ErrorNotice error={prescreen.error} title="Could not run the free pre-screen" />
      )}
      {inRange && prescreen.isPending && <Skeleton className="h-8" label="Pre-screening fields" />}
      {inRange &&
        prescreen.data &&
        (prescreen.data.duplicatePairs.length > 0 ? (
          <Notice tone="warn" title="These look like the same measure twice">
            <ul className="ml-4 list-disc">
              {prescreen.data.duplicatePairs.map((p) => (
                <li key={`${p.fieldA}-${p.fieldB}`}>
                  <span className="num">
                    {p.fieldA} / {p.fieldB}
                  </span>
                  : {p.reason}
                </li>
              ))}
            </ul>
          </Notice>
        ) : null)}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Target" hint="What the candidate fields are regressed against.">
          <Segmented
            label="Target kind"
            value={targetKind}
            onChange={setTargetKind}
            items={[
              { value: 'field', label: 'Raw field' },
              { value: 'vault', label: 'Vault alpha' },
              { value: 'gym', label: 'Gym synthetic' },
            ]}
          />
        </Field>
        {targetKind === 'field' ? (
          <Field
            label="Target field"
            hint="A field id from the catalog, e.g. a forward-returns proxy."
          >
            <Select
              label="Target field"
              className="w-full"
              mono
              value={targetFieldId}
              onChange={setTargetFieldId}
              items={fieldIds.map((id) => ({ value: id, label: id }))}
              disabled={fieldIds.length === 0}
            />
          </Field>
        ) : (
          <Field
            label={targetKind === 'vault' ? 'Vault alpha id' : 'Gym template id'}
            hint={
              targetKind === 'vault'
                ? 'An existing alpha from your vault, regressed on its own returns.'
                : 'A free, synthetic Gym target — no real data cost to explore against.'
            }
          >
            <Input
              value={targetRef}
              onChange={(e) => setTargetRef(e.target.value)}
              placeholder={targetKind === 'vault' ? 'ALPHA123' : 'gym_template_id'}
            />
          </Field>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Start date" hint="Defaults to BRAIN's standard ~4-year window.">
          <Input
            type="date"
            value={dateRange.start}
            onChange={(e) => setDateRange((r) => ({ ...r, start: e.target.value }))}
          />
        </Field>
        <Field label="End date" hint="Overridable, same as any Pipelines run.">
          <Input
            type="date"
            value={dateRange.end}
            onChange={(e) => setDateRange((r) => ({ ...r, end: e.target.value }))}
          />
        </Field>
      </div>

      {Boolean(runError) && <ErrorNotice error={runError} title="Could not run the regression" />}

      <div className="flex justify-end">
        <Button
          variant="primary"
          size="sm"
          loading={running}
          disabled={!canRun}
          onClick={() => void runRegression()}
        >
          Run regression
        </Button>
      </div>

      {result && (
        <>
          {result.admitted < result.asked && (
            <Notice tone="warn" title="Only some fields were admitted">
              The explore lane admitted {result.admitted} of {result.asked} candidates today;{' '}
              {result.dropped.join(', ')} {result.dropped.length === 1 ? 'was' : 'were'} dropped.
            </Notice>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              boxed
              label="R²"
              value={fmt.pct(result.rSquared)}
              hint="How much of the target's movement these fields explain together."
            />
            <Metric
              boxed
              label="Adjusted R²"
              value={fmt.pct(result.adjRSquared)}
              hint="R² penalized for the number of fields — rises only if a field earns its place."
            />
            <Metric
              boxed
              label="Residual std error"
              value={fmt.ratio(result.residualStdError, 4)}
              hint="The typical size of what the fit still can't explain."
            />
            <Metric
              boxed
              label="Observations"
              value={fmt.int(result.nObs)}
              hint="Overlapping dates used in the fit."
            />
          </div>

          <RegressionVerdict
            summary={result.plainSummary}
            recommendation={result.recommendation}
            winner={result.winner}
            redundancyGroups={result.redundancyGroups}
          />

          {barOption && (
            <ChartFigure caption="each field's coefficient with its 95% confidence interval. Greyed-out bars cross zero — no reliable sign — one way or the other.">
              <EChart option={barOption} label="Coefficient by field with confidence interval" />
            </ChartFigure>
          )}
          {heatmapOption && (
            <ChartFigure caption="how correlated the chosen fields are with each other. Bright off-diagonal cells mean two fields are carrying much the same information.">
              <EChart option={heatmapOption} label="Correlation among chosen fields" />
            </ChartFigure>
          )}
          {scatterOption && (
            <ChartFigure caption="standalone correlation with the target (x) against the coefficient once the other fields are controlled for (y). Top-right: strong and independent — best fields. Bottom-right (or top-left): strong alone but redundant once others are added — drop it. Near zero on the y-axis: adds nothing once the others are in the model.">
              <EChart
                option={scatterOption}
                label="Standalone correlation vs controlled coefficient"
              />
            </ChartFigure>
          )}

          <div className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              disabled={result.recommendation.keep.length === 0}
              onClick={() => onSurvivors(result.recommendation.keep)}
            >
              Send surviving fields to Template Lab
            </Button>
          </div>
        </>
      )}
    </Panel>
  )
}
