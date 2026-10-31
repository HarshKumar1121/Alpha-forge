/**
 * Templates of your own: write one, see what it would render, keep every version.
 *
 * The built-ins live in ``config/forge/templates`` and stay read-only — they are part of the
 * repository, and an edit that existed only in one database would vanish on the next clone.
 * Cloning one copies its YAML under a new id, which is yours.
 *
 * The lint is the backend's own loader: the same code that refuses a bad file refuses a bad
 * edit, so "it saved" and "it will load tomorrow" are the same statement.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { CopyIcon, PlusIcon, Trash2Icon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { fmt } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { forgeTemplates } from '@/screens/forge/templates/api'
import { TemplateEditor } from '@/screens/research-labs/template/editor'
import {
  Badge,
  Button,
  Disclosure,
  Empty,
  ErrorNotice,
  Field,
  Input,
  LINK,
  Notice,
  Page,
  PageHeader,
  Panel,
  Skeleton,
} from '@/ui/kit'
import { Confirm, Dialog, Select } from '@/ui/overlay'
import { SplitPane } from '@/ui/panels'
import { STARTER_YAML, studio } from '../studio/api'

/** The editor needs these, and a YAML document has no use for any of them. */
const NO_EXPRESSION_HELP = {
  reference: [],
  presets: {},
  variables: {},
  infos: new Map(),
}

export function MyTemplatesScreen() {
  const { t: selected } = useSearch({ from: '/forge/my-templates' })
  const navigate = useNavigate()
  const client = useQueryClient()
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [cloning, setCloning] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const mine = useQuery({ queryKey: ['forge-my-templates'], queryFn: studio.mine })
  const current = mine.data?.find((t) => t.templateId === selected) ?? null

  // Opening a template, or switching to another, replaces the editor's text.
  useEffect(() => {
    setText(current ? current.yamlText : '')
    setNote('')
  }, [current])

  const open = (templateId: string | undefined) =>
    void navigate({ to: '/forge/my-templates', search: { t: templateId } })

  const typed = useDebounced(text, 400)
  const lint = useQuery({
    queryKey: ['forge-studio', 'lint', typed],
    queryFn: () => studio.lint(typed),
    enabled: typed.trim().length > 0,
  })

  const save = useMutation({
    mutationFn: async () => {
      if (current) return studio.update(current.templateId, text, note)
      return studio.create(text, note || 'Created')
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ['forge-my-templates'] })
      void client.invalidateQueries({ queryKey: ['forge-templates'] })
      void client.invalidateQueries({ queryKey: ['forge-studio', 'versions'] })
      toast.success(`Saved ${saved.templateId} v${saved.version}`)
      setNote('')
      if (!current) open(saved.templateId)
    },
    onError: (e) => toast.error('Could not save it', { description: errorMessage(e) }),
  })

  const remove = useMutation({
    mutationFn: () => studio.remove(current?.templateId ?? ''),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['forge-my-templates'] })
      void client.invalidateQueries({ queryKey: ['forge-templates'] })
      toast.success('Template deleted')
      setDeleting(false)
      open(undefined)
    },
    onError: (e) => toast.error('Could not delete it', { description: errorMessage(e) }),
  })

  const problems = lint.data?.ok === false ? lint.data.problems : []
  const dirty = current ? text !== current.yamlText : text.trim().length > 0

  return (
    <Page>
      <PageHeader
        title="My templates"
        description="Your own ideas, in the same typed-slot format as the built-ins. Saved here, they appear in the library and the Studio alongside them."
        actions={
          <>
            <Button
              onClick={() => {
                open(undefined)
                setText(STARTER_YAML)
              }}
            >
              <PlusIcon aria-hidden />
              New
            </Button>
            <Button variant="ghost" onClick={() => setCloning(true)}>
              Clone a built-in
            </Button>
          </>
        }
      />

      <SplitPane id="forge-my-templates" first={{ default: 300, min: 220, max: 480 }}>
        <Panel title="Saved" bodyClassName="flex flex-col divide-y divide-hairline p-0">
          {mine.isPending && <Skeleton className="h-40" label="Loading" />}
          {mine.data?.length === 0 && (
            <Empty title="Nothing of your own yet">
              Start from <span className="num">New</span>, or clone a built-in from the{' '}
              <a className={LINK} href="/forge/templates">
                library
              </a>
              .
            </Empty>
          )}
          {(mine.data ?? []).map((t) => (
            <button
              key={t.templateId}
              type="button"
              onClick={() => open(t.templateId)}
              className={`flex flex-col gap-0.5 px-4 py-3 text-left transition-colors hover:bg-surface-2 ${
                t.templateId === selected ? 'bg-surface-2' : ''
              }`}
            >
              <span className="num truncate text-body text-ink">{t.templateId}</span>
              <span className="truncate text-body-compact text-ink-subtle">
                {t.name} · v{fmt.int(t.version)}
              </span>
            </button>
          ))}
        </Panel>

        <div className="flex min-w-0 flex-col gap-4">
          {!current && text.trim().length === 0 ? (
            <Panel title="Editor">
              <Empty title="Pick a template, or start a new one">
                A template is an economic idea with typed slots. The engine fills the slots with
                real fields from a field set; you write the shape of the idea.
              </Empty>
            </Panel>
          ) : (
            <Panel
              title={current ? `Editing ${current.templateId}` : 'New template'}
              description="What this tells you: whether this YAML would load. The check is the backend's own loader, so a template that passes here is one that works tomorrow."
              actions={
                <>
                  {lint.data?.ok && <Badge tone="profit">Valid</Badge>}
                  {problems.length > 0 && <Badge tone="loss">{problems.length} problems</Badge>}
                  {current && (
                    <Button variant="ghost" size="sm" onClick={() => setDeleting(true)}>
                      <Trash2Icon aria-hidden />
                      Delete
                    </Button>
                  )}
                </>
              }
              bodyClassName="flex flex-col gap-3 p-4"
            >
              <TemplateEditor
                plain
                label="Template YAML"
                value={text}
                onChange={setText}
                problems={problems}
                {...NO_EXPRESSION_HELP}
              />

              {problems.length > 0 && (
                <Notice tone="error" title="This will not load">
                  <ul className="ml-4 list-disc">
                    {problems.map((p) => (
                      <li key={p} className="break-words">
                        {p}
                      </li>
                    ))}
                  </ul>
                </Notice>
              )}

              {lint.data?.ok && lint.data.example && (
                <Field
                  label="Rendered on placeholder fields"
                  hint="What the template becomes once the engine fills and cleans its slots."
                >
                  <code className="num block rounded-md bg-canvas p-2 text-body-compact break-all text-ink">
                    {lint.data.example}
                  </code>
                </Field>
              )}

              {lint.data?.ok && (
                <div className="flex flex-wrap gap-1">
                  {Object.entries(lint.data.fits).map(([type, ok]) => (
                    <Badge key={type} tone={ok ? 'profit' : 'muted'}>
                      {type.replaceAll('_', ' ')} {ok ? 'fits' : 'no'}
                    </Badge>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-end gap-2">
                <Field
                  className="min-w-48 flex-1"
                  label="What changed"
                  hint="Kept with this version, so a result can be traced to the idea that produced it."
                >
                  <Input value={note} onChange={(e) => setNote(e.target.value)} />
                </Field>
                <Button
                  variant="primary"
                  loading={save.isPending}
                  disabled={!dirty || problems.length > 0 || lint.isPending}
                  onClick={() => save.mutate()}
                >
                  {current ? 'Save new version' : 'Save template'}
                </Button>
              </div>
            </Panel>
          )}

          {current && <Versions templateId={current.templateId} onRestore={setText} />}
        </div>
      </SplitPane>

      <CloneDialog open={cloning} onOpenChange={setCloning} onDone={open} />
      <Confirm
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${current?.templateId ?? ''}?`}
        confirmLabel="Delete"
        danger
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        Its versions go with it. Nothing on BRAIN changes.
      </Confirm>
    </Page>
  )
}

function Versions({
  templateId,
  onRestore,
}: {
  templateId: string
  onRestore: (text: string) => void
}) {
  const versions = useQuery({
    queryKey: ['forge-studio', 'versions', templateId],
    queryFn: () => studio.versions(templateId),
  })
  const exported = useMutation({
    mutationFn: () => studio.exported(templateId),
    onSuccess: async (file) => {
      try {
        await navigator.clipboard.writeText(file.yamlText)
        toast.success('YAML copied', {
          description: 'Paste it into config/forge/templates to make it a built-in.',
        })
      } catch {
        toast.error('Could not write to the clipboard.')
      }
    },
    onError: (e) => toast.error('Could not export it', { description: errorMessage(e) }),
  })

  return (
    <Panel
      title="Versions"
      description="What this tells you: every saved edit, newest first. The version that produced an alpha stays readable, so a result can always be traced back."
      actions={
        <Button variant="ghost" size="sm" onClick={() => exported.mutate()}>
          <CopyIcon aria-hidden />
          Export YAML
        </Button>
      }
      bodyClassName="flex flex-col gap-2 p-4"
    >
      {versions.isError && <ErrorNotice error={versions.error} title="Could not load versions" />}
      {(versions.data ?? []).map((v) => (
        <Disclosure key={v.version} summary={`v${v.version} — ${v.note || 'no note'}`}>
          <div className="flex flex-col gap-2">
            <pre className="num overflow-auto rounded-md bg-canvas p-2 text-body-compact text-ink">
              {v.yamlText}
            </pre>
            <Button size="sm" variant="ghost" onClick={() => onRestore(v.yamlText)}>
              Load this version into the editor
            </Button>
          </div>
        </Disclosure>
      ))}
    </Panel>
  )
}

function CloneDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: (templateId: string) => void
}) {
  const client = useQueryClient()
  const [from, setFrom] = useState<string | null>(null)
  const [newId, setNewId] = useState('')
  const library = useQuery({
    queryKey: ['forge-templates', 'list'],
    queryFn: forgeTemplates.list,
    staleTime: 5 * 60 * 1000,
  })
  const builtins = useMemo(
    () => (library.data?.templates ?? []).filter((t) => t.source !== 'user'),
    [library.data],
  )

  const clone = useMutation({
    mutationFn: () => studio.clone(from ?? '', newId),
    onSuccess: (made) => {
      void client.invalidateQueries({ queryKey: ['forge-my-templates'] })
      void client.invalidateQueries({ queryKey: ['forge-templates'] })
      toast.success(`Cloned as ${made.templateId}`)
      onOpenChange(false)
      onDone(made.templateId)
    },
    onError: (e) => toast.error('Could not clone it', { description: errorMessage(e) }),
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Clone a built-in"
      description="Built-ins are part of the repository and stay read-only. A clone is yours to edit."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={clone.isPending}
            disabled={!from || !/^[a-z][a-z0-9_]*$/.test(newId)}
            onClick={() => clone.mutate()}
          >
            Clone
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Template to copy">
          <Select
            label="Template"
            items={builtins.map((t) => ({ value: t.id, label: t.name }))}
            value={from}
            onChange={setFrom}
          />
        </Field>
        <Field
          label="New id"
          hint="Lower case, digits and underscores — this is how the Studio and the pipelines name it."
        >
          <Input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder="my_idea" />
        </Field>
      </div>
    </Dialog>
  )
}
