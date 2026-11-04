/**
 * A free-hand expression editor: CodeMirror, with autocomplete built only from what is real —
 * the account's own operators (from `/api/template-lab/options`, the same list Template Studio's
 * lint checks against) and the fields currently in the Workbench tray. Nothing here invents an
 * operator or a field that is not actually available.
 */

import {
  acceptCompletion,
  autocompletion,
  type CompletionContext,
  type CompletionResult,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
  snippetCompletion,
} from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { bracketMatching, indentOnInput } from '@codemirror/language'
import { EditorState } from '@codemirror/state'
import {
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightSpecialChars,
  keymap,
  rectangularSelection,
} from '@codemirror/view'
import { useEffect, useRef } from 'react'
import type { components } from '@/api/generated'

type OperatorDoc = components['schemas']['OperatorDoc']

interface Live {
  operators: OperatorDoc[]
  fieldIds: string[]
  onChange: (text: string) => void
}

const WORD = /[$\w]+/

function complete(live: Live, context: CompletionContext): CompletionResult | null {
  const word = context.matchBefore(WORD)
  if (!word || (word.from === word.to && !context.explicit)) return null
  const typed = word.text.toLowerCase()
  const operators = live.operators
    .filter((o) => o.name.toLowerCase().startsWith(typed))
    .map((o) =>
      snippetCompletion(`${o.name}(#{})`, {
        label: o.name,
        type: 'function',
        detail: o.category,
        info: () => {
          const el = document.createElement('div')
          el.className = 'max-w-xs text-body-compact'
          el.textContent = o.description || o.definition
          return el
        },
      }),
    )
  const fields = live.fieldIds
    .filter((f) => f.toLowerCase().startsWith(typed))
    .map((f) => ({ label: f, type: 'variable', detail: 'tray field' }))
  return { from: word.from, options: [...operators, ...fields] }
}

export function ExpressionEditor({
  value,
  operators,
  fieldIds,
  onChange,
  label = 'Expression',
}: {
  value: string
  operators: OperatorDoc[]
  fieldIds: string[]
  onChange: (text: string) => void
  label?: string
}) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const live = useRef<Live>({ operators, fieldIds, onChange })
  live.current = { operators, fieldIds, onChange }
  const first = useRef(value)
  const name = useRef(label)

  useEffect(() => {
    if (!host.current) return
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: first.current,
        extensions: [
          highlightSpecialChars(),
          history(),
          drawSelection(),
          dropCursor(),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          rectangularSelection(),
          highlightActiveLine(),
          autocompletion({ override: [(ctx) => complete(live.current, ctx)] }),
          keymap.of([
            { key: 'Tab', run: acceptCompletion },
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...historyKeymap,
            ...completionKeymap,
          ]),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ 'aria-label': name.current, spellcheck: 'false' }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) live.current.onChange(update.state.doc.toString())
          }),
        ],
      }),
    })
    view.current = editor
    return () => {
      editor.destroy()
      view.current = null
    }
  }, [])

  useEffect(() => {
    const editor = view.current
    if (editor && editor.state.doc.toString() !== value)
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } })
  }, [value])

  return <div ref={host} className="overflow-hidden rounded-md border border-hairline-strong" />
}
