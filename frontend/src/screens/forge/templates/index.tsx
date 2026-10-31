/**
 * Template Library: every typed-slot template in config/forge/templates, with the alpha types
 * it can satisfy (computed by the linter, not labelled) and a one-click try on the free Gym.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { DatabaseIcon, DumbbellIcon, LibraryIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/cn'
import { fmt } from '@/lib/format' // forge: for the per-template statistics line
import { fieldSets } from '@/screens/forge/data/api'
import { ColumnHint } from '@/screens/forge/data/extras'
import { type RenderedAlpha, studio } from '@/screens/forge/studio/api'
import { TemplateEditor } from '@/screens/research-labs/template/editor'
import { InfoButton, InfoSection } from '@/ui/info'
import {
  Badge,
  Button,
  Chips,
  Empty,
  ErrorNotice,
  Field,
  KV,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
  type Tone,
} from '@/ui/kit'
import { Select, Sheet, Tooltip } from '@/ui/overlay'
import { SplitPane } from '@/ui/panels'
import { type Column, DataTable } from '@/ui/table'
import {
  forgeTemplates,
  type TemplateDetail,
  type TemplateFit,
  type TemplateSlot,
  type TemplateSummary,
} from './api'

const COMPLEXITIES = ['simple', 'medium', 'complex'] as const

/** "fundamental/value-quality" -> "fundamental": the family is what you filter by. */
const family = (category: string) => category.split('/')[0] ?? category

export function TemplateLibraryScreen() {
  const { t: selected } = useSearch({ from: '/forge/templates' })
  const navigate = useNavigate({ from: '/forge/templates' })
  const select = (id: string) => navigate({ search: { t: id } })

  const [families, setFamilies] = useState<string[]>([])
  const [complexities, setComplexities] = useState<string[]>([])
  const [fitTypes, setFitTypes] = useState<string[]>([])

  const list = useQuery({
    queryKey: ['forge-templates', 'list'],
    queryFn: forgeTemplates.list,
    staleTime: 5 * 60 * 1000,
  })
  const templates = list.data?.templates ?? []
  const familyItems = useMemo(
    () => [...new Set(templates.map((t) => family(t.category)))].sort(),
    [templates],
  )
  const shown = templates.filter(
    (t) =>
      (families.length === 0 || families.includes(family(t.category))) &&
      (complexities.length === 0 || complexities.includes(t.complexity)) &&
      fitTypes.every((type) => t.fits[type]?.ok),
  )
  const filtered = families.length + complexities.length + fitTypes.length > 0
  const current = selected ?? shown[0]?.id

  return (
    <Page>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            Template Library
            <InfoButton title="Template Library" description="Ideas with typed slots.">
              <AboutTemplates />
            </InfoButton>
          </span>
        }
        description="Economic ideas written once with typed slots, filled with fields that behave the right way. Browsing is free; Try on Gym runs one on the synthetic market, also free."
      />

      {list.data && list.data.errors.length > 0 && (
        <Notice tone="warn" title={`${list.data.errors.length} template entries did not load`}>
          <p>
            Fix these in <code>config/forge/templates/</code>; the rest of the library loaded
            normally.
          </p>
          <ul className="mt-1 list-disc pl-5">
            {list.data.errors.map((e, i) => (
              <li key={i} className="font-mono text-body-compact">
                {e.file}
                {e.templateId ? `: ${e.templateId}` : ''}: {e.message}
              </li>
            ))}
          </ul>
        </Notice>
      )}

      <SplitPane id="forge-templates" first={{ default: 380, min: 280, max: 560 }}>
        <Panel
          title="Templates"
          description={
            list.data
              ? `${shown.length} of ${templates.length} shown`
              : 'Loading the library and checking every template against every alpha type.'
          }
          bodyClassName="flex flex-col gap-3"
        >
          {list.isError ? (
            <ErrorNotice title="Could not load the template library" error={list.error} />
          ) : !list.data ? (
            <Skeleton className="h-96" label="Loading templates" />
          ) : (
            <>
              <div className="flex flex-col gap-2">
                <Chips
                  label="Category"
                  items={familyItems.map((f) => ({ value: f, label: f }))}
                  value={families}
                  onChange={setFamilies}
                />
                <Chips
                  label="Complexity"
                  items={COMPLEXITIES.map((c) => ({ value: c, label: c }))}
                  value={complexities}
                  onChange={setComplexities}
                />
                <Chips
                  label="Fits alpha type"
                  items={list.data.alphaTypes.map((a) => ({
                    value: a,
                    label: a,
                    title: `Only templates whose filled expression passes the ${a} lint`,
                  }))}
                  value={fitTypes}
                  onChange={setFitTypes}
                />
              </div>
              {shown.length === 0 ? (
                <Empty title={filtered ? 'No template matches every filter' : 'No templates yet'}>
                  {filtered ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setFamilies([])
                        setComplexities([])
                        setFitTypes([])
                      }}
                    >
                      Clear filters
                    </Button>
                  ) : (
                    'Add a YAML file under config/forge/templates/ with typed slots and a rationale.'
                  )}
                </Empty>
              ) : (
                <ul className="-m-1 flex max-h-[70vh] flex-col gap-0.5 overflow-auto p-1">
                  {shown.map((t) => (
                    <TemplateRow
                      key={t.id}
                      t={t}
                      active={t.id === current}
                      onSelect={() => select(t.id)}
                    />
                  ))}
                </ul>
              )}
            </>
          )}
        </Panel>

        {current ? (
          <TemplateDetailPanel id={current} />
        ) : (
          <Panel title="Template">
            <Empty title="Pick a template" icon={<LibraryIcon />}>
              Its idea, slots, a rendered example and which alpha types it fits show up here.
            </Empty>
          </Panel>
        )}
      </SplitPane>
    </Page>
  )
}

function TemplateRow({
  t,
  active,
  onSelect,
}: {
  t: TemplateSummary
  active: boolean
  onSelect: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? 'true' : undefined}
        className={cn(
          'flex w-full flex-col gap-1 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-2',
          active && 'bg-surface-2',
        )}
      >
        <span className={cn('truncate text-body', active ? 'text-ink' : 'text-ink-muted')}>
          {t.name}
        </span>
        <span className="flex min-w-0 items-center gap-2 text-body-compact text-ink-subtle">
          <span className="truncate">{t.category}</span>
          <Badge tone="outline" title="How many moving parts the idea has">
            {t.complexity}
          </Badge>
          {/* forge: a template you wrote, rather than one from config/forge/templates. */}
          {t.source === 'user' && (
            <Badge tone="neutral" title="You wrote this one; built-ins are read-only">
              yours
            </Badge>
          )}
          <span className="num ml-auto shrink-0" title="Operators BRAIN counts in the example">
            {t.operatorCount} ops
          </span>
        </span>
        {/* forge: what it has actually produced, per Phase 6's pipelines. */}
        <TemplateStatsLine stats={t.stats} />
      </button>
    </li>
  )
}

/** What a template has produced so far, or nothing at all when it has not been run. */
function TemplateStatsLine({ stats }: { stats: TemplateSummary['stats'] }) {
  if (!stats || !stats.sims) return null
  return (
    <span
      className="flex min-w-0 items-center gap-2 text-body-compact text-ink-subtle"
      title={`Across ${stats.cuts} dataset/type/market cuts: ${stats.sims} simulations, ${stats.passed} of ${stats.checked} decided alphas passed every BRAIN check`}
    >
      <span className="num">{fmt.int(stats.sims)} sims</span>
      {stats.checked > 0 && <span className="num">{fmt.pct(stats.passRate, 0)} pass</span>}
      {stats.medianTrainSharpe != null && (
        <span className="num">med Sharpe {fmt.ratio(stats.medianTrainSharpe)}</span>
      )}
    </span>
  )
}

function TemplateDetailPanel({ id }: { id: string }) {
  const detail = useQuery({
    queryKey: ['forge-templates', 'detail', id],
    queryFn: () => forgeTemplates.get(id),
    staleTime: 5 * 60 * 1000,
    meta: { inline: true },
  })
  if (detail.isError)
    return (
      <Panel title="Template">
        <ErrorNotice title={`Could not load template ${id}`} error={detail.error} />
      </Panel>
    )
  if (!detail.data)
    return (
      <Panel title="Template">
        <Skeleton className="h-96" label="Loading template" />
      </Panel>
    )
  const d = detail.data
  return (
    <Panel
      title={d.name}
      description={
        <span className="font-mono">
          {d.id} · {d.category}
        </span>
      }
      actions={
        <span className="flex items-center gap-2">
          <BindToFieldSet d={d} />
          <TryOnGym d={d} />
        </span>
      }
      bodyClassName="flex flex-col gap-5"
    >
      <Section title="The idea" caption="Why this should predict returns at all.">
        <p className="max-w-3xl text-body text-pretty text-ink-muted">{d.rationale}</p>
      </Section>

      <Section
        title="Slots"
        caption="The blanks a field set fills. Every field is cleaned first (ts_backfill, and a vec_ aggregate for VECTOR fields), so the template never does it itself."
      >
        <ul className="flex flex-col gap-1.5">
          {d.slots.map((s) => (
            <li key={s.name} className="flex gap-3 text-body">
              <span className="w-8 shrink-0 font-mono text-ink">${s.name}</span>
              <span className="text-ink-muted">{describeSlot(s)}</span>
            </li>
          ))}
        </ul>
        {d.needs.length > 0 && (
          <p className="text-body-compact text-ink-subtle">
            Also uses <span className="font-mono">{d.needs.join(', ')}</span> directly.
          </p>
        )}
        {d.onlyIf && (
          <p className="text-body-compact text-ink-subtle">
            Only proposed for fields the profiler flags{' '}
            <span className="font-mono">{d.onlyIf}</span>.
          </p>
        )}
      </Section>

      <Section
        title="Rendered example"
        caption="The template filled with stand-in fields (x_field…) at its default windows and groups: exactly what BRAIN would parse."
      >
        <TemplateEditor
          value={d.example}
          label="Rendered example"
          readOnly
          reference={[]}
          presets={{}}
          variables={{}}
          infos={new Map()}
          problems={[]}
          onChange={() => {}}
        />
        <KV
          items={[
            [
              <Tooltip
                key="o"
                content="BRAIN's operator count; Power Pool allows at most 8. ts_backfill is not counted."
              >
                <span>Operators</span>
              </Tooltip>,
              d.operatorCount,
            ],
            [
              <Tooltip
                key="f"
                content="Distinct data fields, stand-ins included. Power Pool allows at most 3."
              >
                <span>Fields</span>
              </Tooltip>,
              d.fields.length,
            ],
            [
              <Tooltip
                key="d"
                content="How deeply operators nest. Past 8 is a common sign of an overfit expression."
              >
                <span>Nesting depth</span>
              </Tooltip>,
              d.depth,
            ],
            [
              <Tooltip
                key="l"
                content="Every window in the expression, in trading days (21 ≈ a month, 252 ≈ a year)."
              >
                <span>Lookbacks</span>
              </Tooltip>,
              d.lookbacks.length ? d.lookbacks.join(', ') : '—',
            ],
          ]}
        />
      </Section>

      <Section
        title="Alpha-type fit"
        caption="Which alpha types the filled template passes the lint for, filled as favourably as its slots allow. Computed from the alpha_types YAML, not labelled."
      >
        <div className="flex flex-wrap gap-2">
          {Object.entries(d.fits).map(([type, fit]) => (
            <FitBadge key={type} type={type} fit={fit} />
          ))}
        </div>
        <FitReasons fits={d.fits} />
      </Section>
    </Panel>
  )
}

function Section({
  title,
  caption,
  children,
}: {
  title: string
  caption: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-title text-ink">{title}</h2>
        <p className="max-w-3xl text-body-compact text-pretty text-ink-subtle">{caption}</p>
      </div>
      {children}
    </section>
  )
}

/** A fit, in words as well as colour: the mark and the screen-reader text say the same thing. */
function fitState(fit: TemplateFit): [Tone, string, string] {
  if (!fit.ok) return ['loss', '✗', "doesn't fit"]
  return fit.flippedOk ? ['profit', '✓', 'fits'] : ['warn', '!', 'fits un-flipped only']
}

function FitBadge({ type, fit }: { type: string; fit: TemplateFit }) {
  const [tone, mark, word] = fitState(fit)
  const why = fit.ok
    ? fit.flippedOk
      ? 'Passes the lint, also with the signal flipped.'
      : 'Passes as written; flipping the sign adds an operator and breaks a limit.'
    : fit.errors.join('; ')
  return (
    <Badge tone={tone} title={[why, ...fit.warnings].join(' ')}>
      <span aria-hidden>{mark}</span>
      {type}
      <span className="sr-only">: {word}</span>
    </Badge>
  )
}

/** Why a type does not fit, spelled out: the lesson is in the reason, not the red. */
function FitReasons({ fits }: { fits: Record<string, TemplateFit> }) {
  const failing = Object.entries(fits).filter(([, f]) => !(f.ok && f.flippedOk))
  if (failing.length === 0)
    return <p className="text-body-compact text-ink-subtle">Fits every alpha type, either sign.</p>
  return (
    <ul className="flex flex-col gap-1 text-body-compact text-ink-muted">
      {failing.map(([type, f]) => (
        <li key={type}>
          <span className="font-mono text-ink">{type}</span> {fitState(f)[2]}:{' '}
          {f.ok
            ? 'flipping the sign adds one operator, which breaks a limit.'
            : f.errors.join('; ')}
        </li>
      ))}
    </ul>
  )
}

const head = (text: string, hint: string) => <ColumnHint text={text} hint={hint} />

/**
 * Binds this template to a real field set and previews what it actually renders — eligibility
 * decided by `brain/platform.py` and the alpha-type YAMLs, not a guess. Nothing here simulates.
 */
function BindToFieldSet({ d }: { d: TemplateDetail }) {
  const [open, setOpen] = useState(false)
  const [fieldSetId, setFieldSetId] = useState<number | null>(null)

  const sets = useQuery({
    queryKey: ['forge-fieldsets'],
    queryFn: fieldSets.list,
    enabled: open,
  })
  const preview = useQuery({
    queryKey: ['forge-studio', 'preview', d.id, fieldSetId],
    queryFn: () =>
      studio.preview({ templateId: d.id, fieldSetId: fieldSetId as number, limit: 50 }),
    enabled: open && fieldSetId != null,
  })
  const p = preview.data

  const columns: Column<RenderedAlpha>[] = [
    {
      key: 'expression',
      header: 'Expression',
      width: 'minmax(260px,2fr)',
      cell: (a) => (
        <code className="num block break-all text-body-compact text-ink">{a.expression}</code>
      ),
    },
    {
      key: 'eligible',
      header: head(
        'Eligible for',
        'Alpha types this rendered alpha currently qualifies for, judged by that type’s own rules.',
      ),
      width: 'minmax(160px,1fr)',
      cell: (a) => (
        <span className="flex flex-wrap gap-1">
          {a.fits.length > 0 ? (
            a.fits.map((type) => (
              <Badge key={type} tone="profit">
                {type.replaceAll('_', ' ')}
              </Badge>
            ))
          ) : (
            <Badge tone="muted">no type fits</Badge>
          )}
        </span>
      ),
    },
    {
      key: 'reasons',
      header: head(
        'Not eligible',
        'Why each alpha type this alpha misses actually rejected it, straight from the lint.',
      ),
      width: 'minmax(240px,2fr)',
      cell: (a) => {
        const missed = Object.values(a.eligibility).filter((e) => !e.ok)
        if (missed.length === 0) return <span className="text-ink-subtle">—</span>
        return (
          <ul className="flex flex-col gap-0.5 text-body-compact text-ink-muted">
            {missed.map((e) => (
              <li key={e.alphaType}>
                <Badge tone="muted">Not eligible</Badge>{' '}
                <span className="font-mono text-ink">{e.alphaType.replaceAll('_', ' ')}</span>:{' '}
                {e.reasons.join('; ') || 'rejected'}
              </li>
            ))}
          </ul>
        )
      },
    },
  ]

  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        <DatabaseIcon aria-hidden />
        Bind to field set
      </Button>
      <Sheet
        open={open}
        onOpenChange={setOpen}
        title="Bind to field set"
        description={`See the real alphas ${d.name} renders on a field set, and which alpha types each one qualifies for. Free: nothing here is simulated.`}
      >
        <div className="flex flex-col gap-4">
          <Field label="Field set" hint="Its market decides which fields and groups exist.">
            <Select
              label="Field set"
              items={(sets.data ?? []).map((s) => ({
                value: String(s.id),
                label: `${s.name} — ${s.scope.region} D${s.scope.delay} ${s.scope.universe}`,
              }))}
              value={fieldSetId == null ? null : String(fieldSetId)}
              onChange={(value) => setFieldSetId(Number(value))}
            />
          </Field>

          {sets.data?.length === 0 && (
            <Empty title="No field sets yet">
              Make one in the Data Explorer: narrow a market to the fields behind an idea, tick
              them, and choose "Save as field set".
            </Empty>
          )}

          {preview.isError && <ErrorNotice error={preview.error} title="Could not render this" />}
          {preview.isPending && fieldSetId != null && (
            <Skeleton className="h-48" label="Rendering alphas" />
          )}

          {p && p.blockers.length > 0 && (
            <Notice tone="warn" title="This template cannot run on this field set">
              <ul className="ml-4 list-disc">
                {p.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </Notice>
          )}
          {p?.missingFields.length ? (
            <Notice tone="info" title="Some of the set's fields are not in this market">
              <span className="num">{p.missingFields.join(', ')}</span> — they are skipped.
            </Notice>
          ) : null}

          {p && p.rendered.length > 0 && (
            <DataTable
              label="Rendered alphas"
              rows={p.rendered}
              columns={columns}
              rowKey={(a) => a.expression}
              rowHeight={56}
              empty="Nothing rendered for this field set."
            />
          )}
        </div>
      </Sheet>
    </>
  )
}

function TryOnGym({ d }: { d: TemplateDetail }) {
  const expr = d.gym.expression
  if (!expr)
    return (
      <span className="flex items-center gap-2">
        <span className="max-w-[24rem] text-body-compact text-ink-subtle">
          Not on the Gym: {d.gym.unavailableReason ?? 'unknown reason'}
        </span>
        <Button size="sm" disabled>
          <DumbbellIcon aria-hidden />
          Try on Gym
        </Button>
      </span>
    )
  const filled = Object.entries(d.gym.fields)
    .map(([slot, field]) => `$${slot} = ${field}`)
    .join(', ')
  return (
    <Tooltip
      content={`Opens the Gym Playground with ${filled || 'the template'} at default windows. Free.`}
    >
      <Button size="sm" variant="primary" render={<Link to="/forge/gym" search={{ expr }} />}>
        <DumbbellIcon aria-hidden />
        Try on Gym
      </Button>
    </Tooltip>
  )
}

function describeSlot(s: TemplateSlot): string {
  const values = s.values ?? []
  const archetype = s.archetype ?? []
  if (s.kind === 'window') return `A lookback in trading days, one of ${values.join(', ')}.`
  if (s.kind === 'group') return `A peer group to compare within: ${values.join(' or ')}.`
  const parts = [s.type === 'VECTOR' ? 'A VECTOR field' : 'A data field']
  if (archetype.length) parts.push(`that behaves ${archetype.join(' or ')}`)
  if (s.sameDatasetAs) parts.push(`from the same dataset as $${s.sameDatasetAs}`)
  if (s.differentDatasetFrom) parts.push(`from a different dataset than $${s.differentDatasetFrom}`)
  let out = `${parts.join(' ')}.`
  if (s.type === 'VECTOR') out += ` Reduced with ${s.aggregate} before use.`
  if (s.hint) out += ` ${s.hint}`
  return out
}

function AboutTemplates() {
  return (
    <>
      <InfoSection title="Typed slots">
        <p>
          A template is one economic idea with blanks. A field slot says what kind of field fits:
          how it behaves (slow fundamentals, fast sentiment…) and which dataset it may come from.
          Window and group slots allow only economic values, so the idea is tested rather than its
          parameters mined.
        </p>
      </InfoSection>
      <InfoSection title="Computed fit">
        <p>
          Fit badges come from filling the slots with stand-ins and running the same lint the Forge
          runs before every simulation, against every alpha type YAML. A template labelled “simple”
          that has nine operators will show it does not fit Power Pool.
        </p>
      </InfoSection>
      <InfoSection title="Try on Gym">
        <p>
          The Gym fill uses Gym fields whose measured behaviour matches each slot. A template that
          needs something the Gym lacks (VECTOR fields, vwap, regression_neut) says so instead.
        </p>
      </InfoSection>
    </>
  )
}
