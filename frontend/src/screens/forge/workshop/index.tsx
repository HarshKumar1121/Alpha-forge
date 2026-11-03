/**
 * The Workshop: write an idea, see what your own history already says about it (free), put it
 * on trial in the Hypothesis Court (spends the `validate` lane), and promote what survives into
 * a proposal. Promoting spends nothing — approving that proposal (right here, or from the
 * Agent's own proposal list) is the one step that spends, and it is always a manual click
 * (CLAUDE.md rule 5). Nothing on this screen submits an alpha.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { errorMessage } from '@/api/http'
import { fmt } from '@/lib/format'
import { agent, type Hypothesis } from '@/screens/forge/agent/api'
import { AgentPanel } from '@/screens/forge/agent/panel'
import { type FieldSetOut, fieldSets } from '@/screens/forge/data/api'
import { FieldPicker } from '@/screens/forge/data/field-picker'
import { pipelines } from '@/screens/forge/pipelines/api'
import { TemplatePicker } from '@/screens/forge/templates/template-picker'
import {
  Badge,
  Button,
  Chips,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Segmented,
  Skeleton,
  Textarea,
  type Tone,
} from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { type CourtCase, type Evidence, type TrialCreate, type TrialOut, workshop } from './api'

const VERDICT_TONE: Record<string, Tone> = {
  SURVIVES: 'profit',
  WOUNDED: 'warn',
  REJECTED: 'loss',
}

const STATUS_TONE: Record<TrialOut['status'], Tone> = {
  running: 'warn',
  done: 'neutral',
  failed: 'loss',
}

export function WorkshopScreen() {
  const queryClient = useQueryClient()

  const hypotheses = useQuery({
    queryKey: ['forge-workshop', 'hypotheses'],
    queryFn: () => agent.hypotheses(),
  })
  const trials = useQuery({
    queryKey: ['forge-workshop', 'trials'],
    queryFn: () => workshop.listTrials(),
    refetchInterval: (q) => (q.state.data?.some((t) => t.status === 'running') ? 2000 : false),
  })
  const fieldSetsQuery = useQuery({ queryKey: ['forge-fieldsets'], queryFn: fieldSets.list })
  const pipelinesQuery = useQuery({ queryKey: ['forge-pipelines'], queryFn: pipelines.list })

  const proposeHypothesis = useMutation({
    mutationFn: agent.proposeHypothesis,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forge-workshop', 'hypotheses'] })
    },
  })

  return (
    <Page>
      <PageHeader
        title="Workshop"
        description="Write an idea, challenge it against your own history, put it on trial in the Hypothesis Court, then promote what survives. Nothing here submits an alpha — that stays a manual step on the BRAIN website."
      />

      <IdeaForm
        pipelineNames={pipelinesQuery.data?.map((p) => p.name) ?? []}
        fieldSetList={fieldSetsQuery.data ?? []}
        pending={proposeHypothesis.isPending}
        error={proposeHypothesis.isError ? proposeHypothesis.error : null}
        onSubmit={(body) => proposeHypothesis.mutate(body)}
      />

      <Panel
        title="Hypotheses"
        description="What this tells you: every idea you've written, and whether the Court has judged it yet. Challenge one against your own history for free, or send it straight to trial."
        bodyClassName="flex flex-col divide-y divide-hairline p-4"
      >
        {hypotheses.isError && (
          <ErrorNotice error={hypotheses.error} title="Hypotheses could not load" />
        )}
        {hypotheses.isPending && <Skeleton className="h-24" label="Loading hypotheses" />}
        {hypotheses.data && hypotheses.data.length === 0 && (
          <Empty title="No hypotheses yet">Write one above to get started.</Empty>
        )}
        {hypotheses.data?.map((h) => (
          <HypothesisRow key={h.id} hypothesis={h} fieldSetList={fieldSetsQuery.data ?? []} />
        ))}
      </Panel>

      <Panel
        title="Court trials"
        description="What this tells you: each trial's per-field verdict — SURVIVES, WOUNDED or REJECTED — and what it cost from the validate lane. A trial still running refreshes itself every couple of seconds."
        bodyClassName="flex flex-col divide-y divide-hairline p-4"
      >
        {trials.isError && <ErrorNotice error={trials.error} title="Trials could not load" />}
        {trials.isPending && <Skeleton className="h-24" label="Loading trials" />}
        {trials.data && trials.data.length === 0 && (
          <Empty title="No trials yet">Run one from a hypothesis above.</Empty>
        )}
        {trials.data?.map((t) => (
          <TrialRow key={t.id} trial={t} />
        ))}
      </Panel>

      <AgentPanel context={{ screen: 'workshop' }} />
    </Page>
  )
}

function IdeaForm({
  pipelineNames,
  fieldSetList,
  pending,
  error,
  onSubmit,
}: {
  pipelineNames: string[]
  fieldSetList: FieldSetOut[]
  pending: boolean
  error: unknown
  onSubmit: (body: {
    title: string
    mechanism: string
    expectedSign: string
    horizon: string
    pipeline: string
    template: string
    templateId: string
    fields: string[]
  }) => void
}) {
  const [title, setTitle] = useState('')
  const [mechanism, setMechanism] = useState('')
  const [expectedSign, setExpectedSign] = useState<'+' | '-'>('+')
  const [horizon, setHorizon] = useState('')
  const [pipeline, setPipeline] = useState('')
  const [marketFieldSetId, setMarketFieldSetId] = useState<number | null>(null)
  const [fields, setFields] = useState<string[]>([])
  const [templateId, setTemplateId] = useState<string | null>(null)

  // The field/template pickers need a market (region/universe/delay) to search in — borrowed
  // from whichever field set sets the context, the same way `TrialSetup` below scopes a trial.
  const marketSet = fieldSetList.find((fs) => fs.id === marketFieldSetId) ?? fieldSetList[0] ?? null

  const canSubmit = title.trim() !== '' && mechanism.trim() !== ''

  return (
    <Panel
      title="Write an idea"
      description="What this tells you: nothing yet — this just records the hunch. Challenge it with evidence or send it to trial below before trusting it."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (!canSubmit) return
          onSubmit({
            title: title.trim(),
            mechanism: mechanism.trim(),
            expectedSign,
            horizon: horizon.trim(),
            pipeline: pipeline.trim(),
            template: '',
            templateId: templateId ?? '',
            fields,
          })
          setTitle('')
          setMechanism('')
          setHorizon('')
          setFields([])
          setTemplateId(null)
        }}
      >
        <Field label="Title" hint="A short name for the idea.">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} disabled={pending} />
        </Field>
        <Field
          label="Mechanism"
          hint="Why would this work, in your own words? The Court's critic argues against it."
        >
          <Textarea
            value={mechanism}
            onChange={(e) => setMechanism(e.target.value)}
            disabled={pending}
          />
        </Field>
        <div className="flex flex-wrap items-end gap-4">
          <Field label="Claimed sign" className="w-32">
            <Segmented<'+' | '-'>
              label="Claimed sign"
              items={[
                { value: '+', label: '+', disabled: pending },
                { value: '-', label: '−', disabled: pending },
              ]}
              value={expectedSign}
              onChange={setExpectedSign}
            />
          </Field>
          <Field label="Horizon" hint="e.g. short-term, 1 month" className="w-48">
            <Input
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              disabled={pending}
            />
          </Field>
          <Field label="Pipeline" hint="Which alpha type this targets." className="w-56">
            {pipelineNames.length > 0 ? (
              <Select
                label="Pipeline"
                items={pipelineNames.map((n) => ({ value: n, label: n }))}
                value={pipeline || null}
                onChange={setPipeline}
                disabled={pending}
              />
            ) : (
              <Input
                value={pipeline}
                onChange={(e) => setPipeline(e.target.value)}
                disabled={pending}
              />
            )}
          </Field>
        </div>
        {fieldSetList.length > 1 && (
          <Field label="Market" hint="Which field set's scope to search fields in.">
            <Select
              label="Market"
              className="w-72"
              value={marketSet ? String(marketSet.id) : null}
              onChange={(v) => {
                setMarketFieldSetId(Number(v))
                setFields([])
                setTemplateId(null)
              }}
              items={fieldSetList.map((fs) => ({ value: String(fs.id), label: fs.name }))}
              disabled={pending}
            />
          </Field>
        )}
        {marketSet ? (
          <>
            <FieldPicker
              scope={marketSet.scope}
              selected={fields}
              onChange={(ids) => setFields(ids)}
            />
            <TemplatePicker
              scope={marketSet.scope}
              fieldIds={fields}
              value={templateId}
              onChange={setTemplateId}
            />
          </>
        ) : (
          <Empty title="Save a field set first">
            The field and template pickers search a market's real fields — save one from the Data
            Explorer, then come back to write this idea.
          </Empty>
        )}
        {error != null && <ErrorNotice error={error} title="Could not save the idea" />}
        <Button
          type="submit"
          variant="primary"
          loading={pending}
          disabled={!canSubmit}
          className="self-end"
        >
          Save idea
        </Button>
      </form>
    </Panel>
  )
}

function HypothesisRow({
  hypothesis,
  fieldSetList,
}: {
  hypothesis: Hypothesis
  fieldSetList: FieldSetOut[]
}) {
  const [showEvidence, setShowEvidence] = useState(false)
  const [showTrialSetup, setShowTrialSetup] = useState(false)

  const evidence = useMutation({
    mutationFn: () =>
      workshop.evidence({ pipeline: hypothesis.pipeline, fields: hypothesis.fields, days: 90 }),
  })

  return (
    <div className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-body-strong text-ink">{hypothesis.title}</span>
        <div className="flex items-center gap-2">
          {hypothesis.verdict && (
            <Badge tone={VERDICT_TONE[hypothesis.verdict] ?? 'muted'}>{hypothesis.verdict}</Badge>
          )}
          <Badge tone="neutral">{hypothesis.status}</Badge>
        </div>
      </div>
      <p className="text-body-compact text-pretty text-ink-subtle">{hypothesis.mechanism}</p>
      <div className="flex flex-wrap gap-3 text-body-compact text-ink-subtle">
        <span>sign {hypothesis.expectedSign || '+'}</span>
        {hypothesis.horizon && <span>horizon {hypothesis.horizon}</span>}
        {hypothesis.pipeline && <span>pipeline {hypothesis.pipeline}</span>}
        {hypothesis.fields.length > 0 && <span>fields {hypothesis.fields.join(', ')}</span>}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={hypothesis.fields.length === 0}
          loading={evidence.isPending}
          onClick={() => {
            setShowEvidence(true)
            evidence.mutate()
          }}
        >
          Challenge with evidence
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setShowTrialSetup((v) => !v)}>
          {showTrialSetup ? 'Cancel trial' : 'Run Court trial'}
        </Button>
      </div>

      {showEvidence && (
        <EvidenceView
          evidence={evidence.data}
          pending={evidence.isPending}
          error={evidence.isError ? evidence.error : null}
        />
      )}

      {showTrialSetup && (
        <TrialSetup
          hypothesis={hypothesis}
          fieldSetList={fieldSetList}
          onDone={() => setShowTrialSetup(false)}
        />
      )}
    </div>
  )
}

function EvidenceView({
  evidence,
  pending,
  error,
}: {
  evidence: Evidence | undefined
  pending: boolean
  error: unknown
}) {
  if (pending) return <Skeleton className="h-16" label="Reading your history" />
  if (error != null) return <ErrorNotice error={error} title="Evidence could not load" />
  if (!evidence) return null
  if (evidence.findings.length === 0) {
    return (
      <Empty title="No prior history on these fields">
        Nothing in the last {fmt.int(evidence.days)} days used this pipeline and these fields — the
        Court will have nothing to go on besides the trial itself.
      </Empty>
    )
  }
  return (
    <div className="flex flex-col gap-2 rounded-md border border-hairline-subtle bg-surface-2 p-3">
      <p className="text-body-compact text-ink-subtle">
        Over the last <span className="num text-ink">{fmt.int(evidence.days)}</span> days,{' '}
        <span className="num text-ink">{fmt.int(evidence.sampleSize)}</span> prior alpha(s) on these
        fields:
      </p>
      {evidence.findings.map((f, i) => (
        <div key={i} className="flex flex-wrap items-center gap-3 text-body-compact">
          <Badge tone={f.count > 0 ? 'warn' : 'neutral'}>{f.check}</Badge>
          <span className="num text-ink-subtle">
            {fmt.int(f.count)} / {fmt.int(f.sampleSize)} failed before
          </span>
          <span className="text-ink-subtle">{f.note}</span>
        </div>
      ))}
    </div>
  )
}

function TrialSetup({
  hypothesis,
  fieldSetList,
  onDone,
}: {
  hypothesis: Hypothesis
  fieldSetList: FieldSetOut[]
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [fieldSetId, setFieldSetId] = useState<number | null>(null)
  const [fields, setFields] = useState<string[]>(hypothesis.fields.slice(0, 3))
  const [testBudget, setTestBudget] = useState(15)

  const fieldSet = fieldSetList.find((fs) => fs.id === fieldSetId) ?? null

  const create = useMutation({
    mutationFn: (body: TrialCreate) => workshop.createTrial(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forge-workshop', 'trials'] })
      onDone()
    },
  })

  const canRun = fieldSetId !== null && fields.length > 0 && fields.length <= 3

  return (
    <div className="flex flex-col gap-3 rounded-md border border-hairline-subtle bg-surface-2 p-3">
      <Field
        label="Field set"
        hint="The real scope and fields this trial runs against BRAIN with."
        className="w-72"
      >
        <Select
          label="Field set"
          items={fieldSetList.map((fs) => ({ value: String(fs.id), label: fs.name }))}
          value={fieldSetId !== null ? String(fieldSetId) : null}
          onChange={(v) => {
            setFieldSetId(Number(v))
            setFields([])
          }}
        />
      </Field>
      {fieldSet && (
        <Chips
          label="Fields (1 to 3)"
          items={fieldSet.fieldIds.map((f) => ({ value: f, label: f }))}
          value={fields}
          onChange={(next) => setFields(next.slice(0, 3))}
        />
      )}
      <Field
        label="Test budget"
        hint="Simulations spent from the validate lane for this trial."
        className="w-40"
      >
        <Input
          type="number"
          min={1}
          max={100}
          value={testBudget}
          onChange={(e) => setTestBudget(Number(e.target.value))}
        />
      </Field>
      {create.isError && <ErrorNotice error={create.error} title="The trial could not start" />}
      <div className="flex gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={!canRun}
          loading={create.isPending}
          onClick={() =>
            fieldSetId !== null &&
            create.mutate({
              hypothesisId: hypothesis.id,
              title: hypothesis.title,
              mechanism: hypothesis.mechanism,
              expectedSign: hypothesis.expectedSign,
              horizon: hypothesis.horizon,
              template: hypothesis.template,
              templateId: hypothesis.templateId,
              pipeline: hypothesis.pipeline,
              fieldSetId,
              fields,
              testBudget,
            })
          }
        >
          Run trial — spends {fmt.int(testBudget)} from the validate lane
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

function TrialRow({ trial }: { trial: TrialOut }) {
  const queryClient = useQueryClient()
  const [showPromote, setShowPromote] = useState(false)
  const [budget, setBudget] = useState(10)
  const [rationale, setRationale] = useState('')
  const [force, setForce] = useState(false)

  const promote = useMutation({
    mutationFn: () => workshop.promote(trial.id, { budget, rationale, force }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forge-workshop', 'trials'] })
      setShowPromote(false)
    },
  })
  const approve = useMutation({
    mutationFn: (proposalId: number) => agent.approve(proposalId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forge-workshop', 'trials'] })
    },
  })

  const caseData = trial.case as unknown as CourtCase | null
  const survivors = caseData
    ? Object.entries(caseData.verdicts).filter(([, v]) => v === 'SURVIVES' || v === 'WOUNDED')
    : []

  return (
    <div className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-body text-ink">
          Trial #{trial.id} · {trial.pipeline} on {trial.fields.join(', ')}
        </span>
        <div className="flex items-center gap-2">
          {trial.verdict && (
            <Badge tone={VERDICT_TONE[trial.verdict] ?? 'muted'}>{trial.verdict}</Badge>
          )}
          <Badge tone={STATUS_TONE[trial.status]}>{trial.status}</Badge>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric
          size="sm"
          label="Spent"
          value={fmt.int(trial.spent)}
          hint="Simulations used, from the validate lane"
        />
        <Metric
          size="sm"
          label="Started"
          value={fmt.ago(trial.createdAt)}
          hint="When the trial was asked for"
        />
        {trial.finishedAt && (
          <Metric
            size="sm"
            label="Finished"
            value={fmt.ago(trial.finishedAt)}
            hint="When the Court delivered its verdict"
          />
        )}
      </div>

      {caseData && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(caseData.verdicts).map(([field, v]) => (
            <Badge key={field} tone={VERDICT_TONE[v] ?? 'muted'}>
              {field}: {v}
            </Badge>
          ))}
        </div>
      )}

      {trial.status === 'failed' && (
        <Notice tone="error" title="The trial failed">
          {trial.error}
        </Notice>
      )}

      {trial.status === 'done' && trial.proposalId == null && survivors.length === 0 && (
        <Empty title="Nothing survived">
          Every field was REJECTED. Refine the idea or try different fields before trying again.
        </Empty>
      )}

      {trial.status === 'done' && trial.proposalId == null && survivors.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => setShowPromote((v) => !v)}>
              {showPromote ? 'Cancel' : `Promote ${fmt.int(survivors.length)} survivor(s)`}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              title="Build this out further in Pipelines instead of promoting straight to simulation."
              render={
                <Link
                  to="/forge/pipelines"
                  search={{
                    fs: trial.fieldSetId,
                    t:
                      ((trial.hypothesis as Record<string, unknown>)['template_id'] as
                        | string
                        | undefined) || undefined,
                  }}
                />
              }
            >
              Send to Pipelines instead
            </Button>
          </div>
          {showPromote && (
            <div className="flex flex-col gap-2 rounded-md border border-hairline-subtle bg-surface-2 p-3">
              <Field
                label="Budget"
                hint="Simulations the resulting pipeline run may spend, once approved."
                className="w-40"
              >
                <Input
                  type="number"
                  min={1}
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                />
              </Field>
              <Field label="Rationale" hint="Why this is worth the reserve lane's budget.">
                <Textarea value={rationale} onChange={(e) => setRationale(e.target.value)} />
              </Field>
              <label className="flex items-center gap-2 text-body-compact text-ink-subtle">
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(e) => setForce(e.target.checked)}
                />
                Also promote WOUNDED fields, not just SURVIVES
              </label>
              {promote.isError && <ErrorNotice error={promote.error} title="Could not promote" />}
              <Button
                variant="primary"
                size="sm"
                className="self-end"
                disabled={rationale.trim() === ''}
                loading={promote.isPending}
                onClick={() => promote.mutate()}
              >
                Write proposal
              </Button>
            </div>
          )}
        </>
      )}

      {trial.proposalId != null && (
        <Notice tone="info" title={`Proposal #${trial.proposalId} is waiting for approval`}>
          Approving spends from the reserve lane and starts a real pipeline run; nothing has been
          spent yet.
          {approve.isError && <p className="mt-1">{errorMessage(approve.error)}</p>}
          <Button
            size="sm"
            variant="primary"
            className="mt-2"
            loading={approve.isPending}
            onClick={() => trial.proposalId != null && approve.mutate(trial.proposalId)}
          >
            Approve — spend the reserve lane
          </Button>
        </Notice>
      )}
    </div>
  )
}
