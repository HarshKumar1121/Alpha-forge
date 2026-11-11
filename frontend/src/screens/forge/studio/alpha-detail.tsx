/**
 * The shared "what is this rendered alpha" detail view: a nugget's rationale and claim if it was
 * bound to one, the full per-alpha-type eligibility with reasons, and the Power Pool description
 * drafter. Template Studio and Workbench's Template Lab both bind the same `studio.preview()`
 * response to real fields (forge:), so this lives once here instead of twice.
 */

import { Link } from '@tanstack/react-router'
import { DASH, fmt } from '@/lib/format'
import { Badge, Button, KV, Metric, Notice, Panel } from '@/ui/kit'
import { Sheet } from '@/ui/overlay'
import type { NuggetSummaryOut, RenderedAlpha } from './api'
import { DescriptionPanel } from './description'

/** How many of the rendered alphas each alpha type would accept — Template Studio's and
 *  Workbench's Template Lab both render the same `studio.preview()` response, so this is
 *  shared rather than built twice. */
export function FitSummary({
  counts,
  total,
  notes,
}: {
  counts: Record<string, number>
  total: number
  notes: string[]
}) {
  return (
    <Panel
      title="What these alphas qualify for"
      description="What this tells you: how many of the rendered alphas each alpha type would accept, judged by that type's own rules. BRAIN's own check is still the last word."
      bodyClassName="flex flex-col gap-3 p-4"
    >
      <div className="flex flex-wrap gap-4">
        {Object.entries(counts).map(([type, n]) => (
          <Metric
            key={type}
            boxed
            size="sm"
            label={type.replaceAll('_', ' ')}
            value={`${fmt.int(n)} / ${fmt.int(total)}`}
            tone={n ? 'profit' : 'neutral'}
            hint={n ? 'eligible here' : 'none eligible'}
          />
        ))}
      </div>
      {notes.map((note) => (
        <p key={note} className="text-body-compact text-ink-subtle">
          {note}
        </p>
      ))}
    </Panel>
  )
}

export function NuggetCard({ nugget }: { nugget: NuggetSummaryOut }) {
  return (
    <Notice tone="info" title={`Bound to the nugget "${nugget.name}"`}>
      <p className="text-body-compact text-ink">
        <span className="font-medium">Rationale: </span>
        {nugget.rationale}
      </p>
      <p className="mt-1 text-body-compact text-ink">
        <span className="font-medium">Claim: </span>
        {nugget.claim}
      </p>
    </Notice>
  )
}

export function AlphaSheet({
  alpha,
  templateId,
  fieldSetId,
  nuggetId,
  nugget,
  scope,
  onClose,
  onAdd,
}: {
  alpha: RenderedAlpha | null
  templateId: string | undefined
  fieldSetId: number | undefined
  nuggetId: string | undefined
  nugget: NuggetSummaryOut | null | undefined
  scope: string
  onClose: () => void
  /** Shown as "Add to candidates" when this sheet is opened from Workbench's own run tray. */
  onAdd?: () => void
}) {
  return (
    <Sheet
      open={alpha != null}
      onOpenChange={(open) => !open && onClose()}
      title="This alpha"
      description={alpha ? `${alpha.operatorCount} operators` : ''}
    >
      {alpha && (
        <div className="flex flex-col gap-4">
          {nugget && <NuggetCard nugget={nugget} />}
          <code className="num block rounded-md bg-canvas p-3 text-body-compact break-all text-ink">
            {alpha.expression}
          </code>
          <p className="text-body-compact text-ink-subtle">
            What happens next: this is a preview — nothing has been simulated.{' '}
            {onAdd
              ? 'Add it to the candidates below to run it through Workbench, or send it to Pipelines for the alpha-type-directed factory instead.'
              : 'Send it to Pipelines to actually run it against BRAIN through the Quota Governor.'}
          </p>
          <div className="flex flex-wrap gap-2">
            {onAdd && (
              <Button variant="primary" size="sm" onClick={onAdd}>
                Add to candidates
              </Button>
            )}
            {templateId != null && fieldSetId != null && (
              <Button
                variant={onAdd ? 'secondary' : 'primary'}
                size="sm"
                render={<Link to="/forge/pipelines" search={{ t: templateId, fs: fieldSetId }} />}
              >
                Send to Pipelines
              </Button>
            )}
          </div>
          <KV
            items={[
              [
                'Slots',
                Object.entries({ ...alpha.fields, ...alpha.params })
                  .map(([slot, value]) => `$${slot} = ${value}`)
                  .join(', ') || DASH,
              ],
              ['Data fields', alpha.dataFields.join(', ') || DASH],
              ['Datasets', alpha.datasets.join(', ') || DASH],
            ]}
          />

          <section className="flex flex-col gap-2">
            <h3 className="text-title">Alpha types</h3>
            {alpha.fitsAll ? (
              <div className="rounded-md border border-hairline p-3">
                <Badge tone="profit">fits every type</Badge>
              </div>
            ) : (
              alpha.fits.map((type) => (
                <div key={type} className="rounded-md border border-hairline p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone="profit">Eligible</Badge>
                    <span className="text-body font-medium text-ink">
                      {type.replaceAll('_', ' ')}
                    </span>
                  </div>
                </div>
              ))
            )}
            {Object.entries(alpha.eligibility).map(([type, verdict]) => (
              <div key={type} className="rounded-md border border-hairline p-3">
                <div className="flex items-center gap-2">
                  <Badge tone="muted">Not eligible</Badge>
                  <span className="text-body font-medium text-ink">
                    {type.replaceAll('_', ' ')}
                  </span>
                </div>
                {verdict.reasons.length > 0 && (
                  <ul className="mt-2 ml-4 list-disc text-body-compact text-ink-muted">
                    {verdict.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                )}
                {verdict.warnings.map((w) => (
                  <p key={w} className="mt-2 text-body-compact text-ink-subtle">
                    {w}
                  </p>
                ))}
              </div>
            ))}
          </section>

          {(alpha.fitsAll || alpha.fits.includes('power_pool')) && (
            <DescriptionPanel
              expression={alpha.expression}
              templateId={templateId}
              fieldSetId={fieldSetId}
              nuggetId={nuggetId}
              scope={scope}
            />
          )}
        </div>
      )}
    </Sheet>
  )
}
