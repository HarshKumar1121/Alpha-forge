/**
 * Gym Playground: write an expression, run it on the synthetic Gym market, read the tear sheet.
 * Free and instant enough to learn on — nothing here reaches BRAIN or spends quota.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { DumbbellIcon, PlayIcon } from 'lucide-react'
import { type KeyboardEvent, useMemo, useState } from 'react'
import { DEFAULT_TRUNCATION, MAX_DECAY, MAX_TEST_PERIOD_YEARS } from '@/api/platform'
import { neutralizationLabel } from '@/lib/neutralization'
import { clamp, truncationOf } from '@/screens/research-labs/simulation-settings'
import { TemplateEditor } from '@/screens/research-labs/template/editor'
import type { OperatorDoc } from '@/screens/research-labs/template/fast-expression'
import { MOD } from '@/shell/desktop'
import { Button, Empty, ErrorNotice, Field, Input, Kbd, Page, PageHeader, Panel } from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { forgeGym, type GymRequest } from './api'
import { TearSheet } from './tear-sheet'

type Neutralization = GymRequest['neutralization']
type Universe = GymRequest['universe']

const NEUTRALIZATIONS: Neutralization[] = ['NONE', 'MARKET', 'SECTOR', 'INDUSTRY', 'SUBINDUSTRY']
const UNIVERSES: Universe[] = ['TOP3000', 'TOP1000', 'TOP500', 'TOP200']

/** Starting points named by what the data is, not by what it does: finding out is the exercise. */
const EXAMPLES = [
  { expression: 'rank(ts_backfill(anl_revision, 21))', what: 'A sparse analyst field' },
  { expression: '-ts_sum(returns, 5)', what: "Last week's return, flipped" },
  { expression: 'rank(news_sentiment)', what: 'Daily news sentiment' },
  { expression: 'rank(book_to_price)', what: 'A quarterly valuation ratio' },
  { expression: 'rank(alt_7)', what: 'An alternative-data feed' },
]

/** Field names the editor's hover should explain; the Gym has no catalog of its own in BRAIN's. */
const ALT_FIELDS = /^alt_\d+$/

export function GymPlaygroundScreen() {
  // Seeded from ?expr= (the Template Library's "Try on Gym"); yours to edit from then on.
  const { expr } = useSearch({ from: '/forge/gym' })
  const [expression, setExpression] = useState(expr ?? '')
  const [decay, setDecay] = useState('0')
  const [truncation, setTruncation] = useState(String(DEFAULT_TRUNCATION))
  const [neutralization, setNeutralization] = useState<Neutralization>('INDUSTRY')
  const [universe, setUniverse] = useState<Universe>('TOP3000')
  const [testYears, setTestYears] = useState('2')

  const catalog = useQuery({
    queryKey: ['forge-gym', 'catalog'],
    queryFn: forgeGym.catalog,
    staleTime: Number.POSITIVE_INFINITY,
  })
  // The Gym's own operators, so the editor flags what the local engine cannot run.
  const reference = useMemo<OperatorDoc[]>(
    () =>
      (catalog.data?.operators ?? []).map((name) => ({
        name,
        category: 'Gym',
        definition: `${name}()`,
        description: 'Available in the Gym’s local engine.',
      })),
    [catalog.data],
  )

  const run = useMutation({
    mutationFn: forgeGym.simulate,
    // Shown in place, under the editor, rather than as a passing toast.
    meta: { inline: true },
  })

  const truncationValue = truncationOf(truncation)
  const ready = expression.trim().length > 0 && truncationValue !== null && !run.isPending
  const submit = () => {
    if (!ready) return
    run.mutate({
      expression: expression.trim(),
      decay: clamp(decay, MAX_DECAY),
      neutralization,
      universe,
      truncation: truncationValue,
      testYears: clamp(testYears, MAX_TEST_PERIOD_YEARS),
    })
  }
  // Captured before the editor, so ⌘↵ runs instead of inserting a line.
  const onKeyDownCapture = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      e.stopPropagation()
      submit()
    }
  }

  return (
    <Page>
      <PageHeader
        title="Gym Playground"
        description="Run an expression on the Gym: a synthetic market of 400 stocks over ten years, with real effects and pure noise planted in it. Free — nothing here reaches BRAIN or spends a simulation."
      />

      <Panel
        title="Expression"
        description="Fast Expression, as on BRAIN, with the Gym's own fields and operators."
        actions={
          <Button variant="primary" onClick={submit} disabled={!ready} loading={run.isPending}>
            <PlayIcon className="size-3.5" aria-hidden />
            Run
            <Kbd>{MOD}↵</Kbd>
          </Button>
        }
      >
        <div className="flex flex-col gap-4" onKeyDownCapture={onKeyDownCapture}>
          <TemplateEditor
            value={expression}
            label="Expression"
            reference={reference}
            presets={{}}
            variables={{}}
            infos={new Map()}
            problems={[]}
            onChange={setExpression}
          />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Field label="Decay" hint="Average the signal over this many days.">
              <Input
                type="number"
                min={0}
                max={MAX_DECAY}
                step={1}
                value={decay}
                onChange={(e) => setDecay(e.target.value)}
              />
            </Field>
            <Field label="Neutralization" hint="Bet within these groups, not on them.">
              <Select
                label="Neutralization"
                value={neutralization}
                onChange={setNeutralization}
                items={NEUTRALIZATIONS.map((value) => ({
                  value,
                  label: neutralizationLabel(value),
                }))}
              />
            </Field>
            <Field label="Universe" hint="Scaled to the Gym's 400 stocks.">
              <Select
                label="Universe"
                mono
                value={universe}
                onChange={setUniverse}
                items={UNIVERSES.map((value) => ({ value, label: value }))}
              />
            </Field>
            <Field
              label="Truncation"
              hint="Most of the book one stock may hold."
              error={truncationValue === null ? 'A number from 0 to 1.' : undefined}
            >
              <Input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={truncation}
                aria-invalid={truncationValue === null}
                onChange={(e) => setTruncation(e.target.value)}
              />
            </Field>
            <Field label="Test years" hint="The last years, held out from research.">
              <Input
                type="number"
                min={0}
                max={MAX_TEST_PERIOD_YEARS}
                step={1}
                value={testYears}
                onChange={(e) => setTestYears(e.target.value)}
              />
            </Field>
          </div>
          {run.isError && (
            <ErrorNotice error={run.error} title="The Gym could not run this expression" />
          )}
        </div>
      </Panel>

      {run.data ? (
        <TearSheet sheet={run.data} />
      ) : (
        <Panel>
          <Empty
            icon={<DumbbellIcon />}
            title={run.isPending ? 'Running on the Gym…' : 'Write an expression, then press Run'}
          >
            {run.isPending ? (
              'The first run builds the market, which takes a few seconds.'
            ) : (
              <Start
                onPick={setExpression}
                fields={catalog.data?.fields.filter((f) => !ALT_FIELDS.test(f.name)) ?? []}
              />
            )}
          </Empty>
        </Panel>
      )}
    </Page>
  )
}

/** What to try first, and what the Gym has to work with. */
function Start({
  onPick,
  fields,
}: {
  onPick: (expression: string) => void
  fields: { name: string; kind: string; description: string }[]
}) {
  return (
    <div className="flex max-w-3xl flex-col items-center gap-4">
      <p>
        Pick a starting point, run it, and read the tear sheet: does it work, in which years, and
        would it survive in large caps? Then change the settings and run it again.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((ex) => (
          <Button
            key={ex.expression}
            size="sm"
            onClick={() => onPick(ex.expression)}
            title={ex.what}
          >
            <span className="num">{ex.expression}</span>
          </Button>
        ))}
      </div>
      {fields.length > 0 && (
        <p className="text-pretty">
          Fields:{' '}
          {fields.map((f, i) => (
            <span key={f.name} title={f.description}>
              <span className="num text-ink-muted">{f.name}</span>
              {i < fields.length - 1 ? ', ' : ''}
            </span>
          ))}
          , and <span className="num text-ink-muted">alt_1</span>…
          <span className="num text-ink-muted">alt_20</span> (alternative data).
        </p>
      )}
    </div>
  )
}
