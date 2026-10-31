/**
 * The scope picker every Forge screen uses: instrument type, region, delay and universe, all
 * from the account's own `OPTIONS /simulations` schema (`/api/forge/scopes`), never a
 * hardcoded list. One scope is shared by every Forge screen, so moving from the datasets to a
 * field set keeps the market you were looking at.
 *
 * Universes the local catalog has not downloaded are still offered (BRAIN can run them) but
 * marked, since their fields and datasets read empty until synced.
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useMemo } from 'react'
import { ApiError } from '@/api/http'
import type { Scope } from '@/api/types'
import { useScope } from '@/lib/scope'
import { Badge, LINK } from '@/ui/kit'
import { Select, Tooltip } from '@/ui/overlay'
import { forgeData, type ScopeTree, type UniverseNode } from '../data/api'

/** The scope shared by every Forge screen. */
export const useForgeScope = () => useScope('forge')

export function useScopeTree() {
  return useQuery({
    queryKey: ['forge-scopes'],
    queryFn: forgeData.scopes,
    staleTime: 10 * 60 * 1000,
    retry: (count, e) => !(e instanceof ApiError && e.status === 503) && count < 2,
  })
}

/** The legal choices at each level for the scope as it stands. */
function levels(tree: ScopeTree | undefined, scope: Scope) {
  const its = tree?.instrumentTypes ?? []
  const it = its.find((i) => i.value === scope.instrumentType)
  const regions = it?.regions ?? []
  const region = regions.find((r) => r.value === scope.region)
  const delays = region?.delays ?? []
  const delay = delays.find((d) => d.value === scope.delay)
  const universes = delay?.universes ?? []
  const universe = universes.find((u) => u.value === scope.universe)
  return { its, regions, delays, universes, universe }
}

/** The universe node the scope points at (its sync state and neutralizations), if legal. */
export function useScopeNode(scope: Scope): UniverseNode | undefined {
  const tree = useScopeTree()
  return useMemo(() => levels(tree.data, scope).universe, [tree.data, scope])
}

export function ForgeScopePicker({
  scope,
  onChange,
  showNeutralizations = false,
}: {
  scope: Scope
  onChange: (change: Partial<Scope>) => void
  /** List the neutralizations BRAIN offers in this scope under the pickers. */
  showNeutralizations?: boolean
}) {
  const tree = useScopeTree()
  const l = useMemo(() => levels(tree.data, scope), [tree.data, scope])

  // Keep the scope legal from the outside in: a region without the current delay, or a delay
  // without the current universe, moves to the first legal one rather than offering nothing.
  useEffect(() => {
    if (!tree.data) return
    const [it] = l.its
    if (it && !l.its.some((i) => i.value === scope.instrumentType))
      onChange({ instrumentType: it.value })
    else if (l.regions[0] && !l.regions.some((r) => r.value === scope.region))
      onChange({ region: l.regions[0].value })
    else if (l.delays[0] && !l.delays.some((d) => d.value === scope.delay))
      onChange({ delay: l.delays[0].value })
    else if (l.universes[0] && !l.universe) onChange({ universe: l.universes[0].value })
  }, [tree.data, l, scope, onChange])

  if (tree.isError) {
    return (
      <p className="text-body-compact text-ink-subtle">
        {tree.error instanceof ApiError && tree.error.status === 503
          ? 'Sign in to BRAIN to load the markets your account can run.'
          : 'Could not load your markets from BRAIN.'}
      </p>
    )
  }

  const universeItems = l.universes.map((u) => ({
    value: u.value,
    label: u.synced ? u.label : `${u.label} · not synced`,
  }))

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {l.its.length > 1 && (
          <Part label="Instrument">
            <Select
              label="Instrument type"
              items={l.its.map((i) => ({ value: i.value, label: i.label }))}
              value={scope.instrumentType}
              onChange={(value) => onChange({ instrumentType: value })}
            />
          </Part>
        )}
        <Part label="Region">
          <Select
            label="Region"
            mono
            disabled={!tree.data}
            items={
              l.regions.length
                ? l.regions.map((r) => ({ value: r.value, label: r.value }))
                : [{ value: scope.region, label: scope.region }]
            }
            value={scope.region}
            onChange={(value) => onChange({ region: value })}
          />
        </Part>
        <Part label="Delay">
          <Select
            label="Delay"
            mono
            disabled={!tree.data}
            items={
              l.delays.length
                ? l.delays.map((d) => ({ value: String(d.value), label: String(d.value) }))
                : [{ value: String(scope.delay), label: String(scope.delay) }]
            }
            value={String(scope.delay)}
            onChange={(value) => onChange({ delay: Number(value) })}
          />
        </Part>
        <Part label="Universe">
          <Select
            label="Universe"
            mono
            disabled={!tree.data}
            items={
              universeItems.length
                ? universeItems
                : [{ value: scope.universe, label: scope.universe }]
            }
            value={scope.universe}
            onChange={(value) => onChange({ universe: value })}
          />
        </Part>
        {l.universe && !l.universe.synced && (
          <Tooltip content="BRAIN can run this market, but its fields are not downloaded here yet, so datasets and fields read empty.">
            <Link to="/sync" className={LINK}>
              Sync this market
            </Link>
          </Tooltip>
        )}
      </div>
      {showNeutralizations && l.universe && l.universe.neutralizations.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <Tooltip content="What an alpha in this scope can be made neutral to, as BRAIN offers it here. Neutralizing removes that exposure, so the alpha bets on the stock and not its group.">
            <span className="cursor-help text-body-compact text-ink-subtle">Neutralizations</span>
          </Tooltip>
          {l.universe.neutralizations.map((n) => (
            <Badge key={n.value} tone="outline">
              {n.label}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

function Part({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-body-compact text-ink-subtle">{label}</span>
      {children}
    </span>
  )
}
