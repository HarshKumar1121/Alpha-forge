/**
 * Select: pick fields in one market, see which templates those fields can actually fill, and hand
 * the chosen template and its fields into Template Lab. It runs nothing: a run stays Template
 * Lab's own call, so Tasks and the Results Breakdown work exactly as before.
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useRef, useState } from 'react'
import { catalog, type DataFieldRow } from '@/api/catalog'
import type { Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { nuggets } from '@/screens/forge/nuggets/api'
import { ForgeScopePicker, useForgeScope } from '@/screens/forge/scope/picker'
import { forgeQuota } from '@/screens/forge/today/api'
import { useTemplateLab } from '@/screens/research-labs/template/state'
import { Button, Empty, ErrorNotice, Panel } from '@/ui/kit'
import { type FitTemplate, POOL_CAP, selectFit } from './api'
import {
  candidatesOf,
  fieldSlots,
  fitState,
  groupTemplates,
  handOffVariables,
  searchSpace,
  searchSpaceLabel,
} from './model'
import { TemplateGroups } from './template-cards'

export function SelectScreen() {
  // The redirect from `/forge/studio` and Template Library's "Bind to field set" both land here
  // with a template (`t`) and a field set (`fs`) to start from.
  const { t: initialTemplate, fs: initialFieldSet } = useSearch({ from: '/forge/select' })
  const [scope, setScope] = useForgeScope()
  const [fieldIds, setFieldIds] = useState<string[]>([])
  const [templateId, setTemplateId] = useState<string | null>(initialTemplate ?? null)
  const [notice, setNotice] = useState<string | null>(null)
  const [handOffError, setHandOffError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  // The market as of this render, so a hand-off that resolves after a market change can tell.
  const scopeKey = `${scope.instrumentType}|${scope.region}|${scope.delay}|${scope.universe}`
  const scopeKeyRef = useRef(scopeKey)
  scopeKeyRef.current = scopeKey

  // Sorted, so the same pool in another order is one cache entry, not two.
  const pool = useMemo(() => [...fieldIds].sort(), [fieldIds])

  const quota = useQuery({ queryKey: ['forge-quota'], queryFn: forgeQuota.today })
  const reserve = quota.data?.lanes.find((l) => l.lane === 'reserve')

  const fit = useQuery({
    queryKey: ['forge-select', 'fit', scope, pool],
    queryFn: () => selectFit.fit(scope, pool),
    enabled: pool.length > 0,
  })
  const pairs = useQuery({
    queryKey: ['forge-select', 'pairs', scope, pool],
    queryFn: () => nuggets.suggest(scope, pool),
    enabled: pool.length >= 2,
  })

  const groups = useMemo(() => groupTemplates(fit.data?.templates ?? []), [fit.data])
  const selected: FitTemplate | null = fit.data?.templates.find((t) => t.id === templateId) ?? null
  const selectedState = selected ? fitState(selected) : null
  // A template that fits, or whose check was declined (the card then says so), may be handed off.
  // One that does not fit, or whose fields cannot be paired, has no joint binding to hand over.
  // Template Lab's market is EQUITY only, and its draft cannot carry another instrument type, so a
  // hand-off from any other market would simulate the wrong fields. Refuse it, and say why.
  const equityOnly = scope.instrumentType === 'EQUITY'
  const canHandOff = equityOnly && (selectedState === 'fits' || selectedState === 'notChecked')

  /** A pick past the cap is refused, not truncated: the user should see why. */
  function pick(ids: string[]) {
    if (ids.length > POOL_CAP) {
      setNotice(
        `Select is for a deliberate pick, up to ${POOL_CAP} fields. For whole-dataset sweeps, bind a dataset in Template Lab.`,
      )
      return
    }
    setNotice(null)
    setFieldIds(ids)
  }

  /** Changing the market clears the pool and the template choice: a field id means different
   *  data in another market, so nothing picked there can carry over. */
  function changeScope(change: Partial<Scope>) {
    const moved = (Object.keys(change) as (keyof Scope)[]).some((k) => change[k] !== scope[k])
    setScope(change)
    if (moved) {
      setFieldIds([])
      setTemplateId(null)
      setNotice(null)
    }
  }

  async function openInTemplateLab(template: FitTemplate) {
    const lab = useTemplateLab.getState()
    if (
      lab.dirty &&
      !window.confirm(`Template Lab has unsaved changes to '${lab.name}'. Replace them?`)
    ) {
      return
    }
    setBusy(true)
    setHandOffError(null)
    const startedIn = scopeKey
    try {
      // The fit response has candidate ids only; the datasets come from the catalog, one search.
      const ids = [...new Set(fieldSlots(template).flatMap(candidatesOf))]
      const page = ids.length
        ? await catalog.fields(scope, { field_ids: ids, limit: POOL_CAP })
        : { results: [] as DataFieldRow[] }
      if (scopeKeyRef.current !== startedIn) {
        // The market changed while the fields loaded: the pool was cleared, so do not open the
        // old market in Template Lab.
        return
      }
      const rows = new Map(page.results.map((r) => [r.field_id, r]))
      useTemplateLab
        .getState()
        .open(null, template.name, template.expr, handOffVariables(template, rows))
      useTemplateLab.setState({
        region: scope.region,
        delay: scope.delay,
        universes: [scope.universe],
        picking: null,
        view: 'code',
        dirty: true,
      })
      navigate({ to: '/labs/template' })
    } catch (error) {
      setHandOffError(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel
        title="Market"
        description="What this tells you: the region, delay and universe every number below is read in. A field id means different data in another market."
        actions={
          quota.data ? (
            <span
              className="text-body-compact text-ink-muted"
              title="Template Lab runs are charged to the reserve lane (lanes.yaml default_lane)."
            >
              Today: {fmt.int(quota.data.spent)} of {fmt.int(quota.data.limit)} used
              {reserve ? ` · reserve lane ${fmt.int(reserve.remaining)} left` : ''}
            </span>
          ) : null
        }
      >
        <ForgeScopePicker scope={scope} onChange={changeScope} />
      </Panel>

      <Panel
        title="Fields"
        description="What this tells you: how crowded each field is and how often your own checked alphas on it have passed BRAIN. Pick up to 50 fields to form the pool."
        actions={
          <span className="flex items-center gap-2">
            <span className="text-body-compact text-ink-muted">
              {fieldIds.length} of {POOL_CAP} picked
            </span>
            {fieldIds.length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setFieldIds([])
                  setTemplateId(null)
                  setNotice(null)
                }}
              >
                Clear
              </Button>
            ) : null}
          </span>
        }
      >
        <FieldPicker
          scope={scope}
          selected={fieldIds}
          onChange={(ids) => pick(ids)}
          initialFieldSetId={initialFieldSet ?? null}
        />
        {notice ? (
          <p className="mt-2 text-body-compact text-pnl-negative-text" role="status">
            {notice}
          </p>
        ) : null}
        {pool.length >= 2 ? (
          <div
            className="mt-3 text-body-compact text-ink-muted"
            title="Advisory only: nothing is blocked by this check."
          >
            {pairs.isError ? (
              <ErrorNotice error={pairs.error} title="Pairing check failed" />
            ) : pairs.data && pairs.data.suggestions.length > 0 ? (
              <ul className="flex flex-col gap-1">
                {pairs.data.suggestions.map((s) => (
                  <li key={`${s.fieldA}|${s.fieldB}`}>
                    ⚠ {s.fieldA} and {s.fieldB}: {s.reason}
                  </li>
                ))}
              </ul>
            ) : pairs.isSuccess ? (
              <span>All pairs look sound.</span>
            ) : null}
          </div>
        ) : null}
      </Panel>

      <Panel
        title="Templates your pool can fill"
        description="What this tells you: each template's field slots, matched against the pool. A template that fits says which pool fields fill it; the others say why not."
      >
        {pool.length === 0 ? (
          <Empty title="Add fields above">
            Templates are ranked by what those fields can fill. Pick at least one field to start.
          </Empty>
        ) : fit.isError ? (
          <ErrorNotice error={fit.error} title="Could not check the templates" />
        ) : fit.isPending ? (
          <p className="text-body-compact text-ink-muted">
            Checking the library against your pool…
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {fit.data?.missing.length ? (
              <p className="text-body-compact text-ink-muted">
                Not in this market, so left out: {fit.data.missing.join(', ')}.
              </p>
            ) : null}
            {fit.data?.errors.length ? (
              <p className="text-body-compact text-pnl-negative-text">
                Your templates that no longer parse: {fit.data.errors.join('; ')}
              </p>
            ) : null}
            {fit.data && fit.data.templates.length === 0 ? (
              <Empty title="No templates in the library yet">
                Built-in templates ship in config/forge/templates. Add your own on the My Templates
                screen, then come back to see what your pool can fill.
              </Empty>
            ) : (
              <TemplateGroups groups={groups} selectedId={templateId} onSelect={setTemplateId} />
            )}
          </div>
        )}
      </Panel>

      {selected ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-hairline p-3">
          <span
            className="text-body-compact text-ink-muted"
            title="The product of every slot's count: pool fields per field slot, values per window or group slot. This is the number of expressions Template Lab would search."
          >
            Search space: {searchSpaceLabel(selected)}
          </span>
          <span className="ml-auto flex items-center gap-2">
            {!equityOnly ? (
              <span className="text-body-compact text-ink-subtle">
                Template Lab runs EQUITY only. Switch the market to hand off.
              </span>
            ) : null}
            {selectedState === 'notChecked' ? (
              <span className="text-body-compact text-ink-subtle">
                Not verified: the check was declined.
              </span>
            ) : null}
            <Button
              variant="primary"
              disabled={!canHandOff || busy || searchSpace(selected) === 0}
              loading={busy}
              onClick={() => void openInTemplateLab(selected)}
            >
              Open in Template Lab →
            </Button>
          </span>
          {handOffError ? (
            <ErrorNotice error={handOffError} title="Could not open Template Lab" />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
