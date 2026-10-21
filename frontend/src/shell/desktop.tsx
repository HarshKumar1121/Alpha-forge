/**
 * Inside the desktop app only: system notifications for what happens while the window is out of
 * sight — a task ending, BRAIN's daily limit, a lost session, an update to install. Each is raised
 * when its state changes, never for what was already so when the window opened.
 */

import { useQuery } from '@tanstack/react-query'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { CopyIcon, MinusIcon, SquareIcon, XIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { simulations } from '@/api/core'
import { cn } from '@/lib/cn'
import { useRefetchOn } from '@/lib/ws'
import { labTasks } from '@/screens/tasks/api'
import { useUpdateStatus } from './update'

/** Tauri puts its bridge into the page it shows; a browser tab has none. */
export const inDesktop = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
/** macOS keeps its own window buttons, drawn over the top bar's left end. */
export const onMac = typeof navigator !== 'undefined' && navigator.userAgent.includes('Mac')

const CONTROL =
  'flex h-12 w-11 items-center justify-center text-ink-subtle transition-colors hover:bg-surface-2 hover:text-ink [&_svg]:size-4'

/** Minimise, maximise and close, for Windows and Linux, where the window has no title bar. */
export function WindowControls() {
  const [maximized, setMaximized] = useState(false)
  useEffect(() => {
    const appWindow = getCurrentWindow()
    const read = () => void appWindow.isMaximized().then(setMaximized)
    read()
    const stop = appWindow.onResized(read)
    return () => void stop.then((unlisten) => unlisten())
  }, [])
  const appWindow = getCurrentWindow()
  return (
    <div className="flex">
      <button
        type="button"
        aria-label="Minimise"
        className={CONTROL}
        onClick={() => void appWindow.minimize()}
      >
        <MinusIcon strokeWidth={1.5} aria-hidden />
      </button>
      <button
        type="button"
        aria-label={maximized ? 'Restore' : 'Maximise'}
        className={CONTROL}
        onClick={() => void appWindow.toggleMaximize()}
      >
        {maximized ? (
          <CopyIcon strokeWidth={1.5} className="-scale-x-100" aria-hidden />
        ) : (
          <SquareIcon strokeWidth={1.5} aria-hidden />
        )}
      </button>
      {/* Closing keeps Alpha Harness in the tray, as the window's own close did. */}
      <button
        type="button"
        aria-label="Close to the tray"
        className={cn(CONTROL, 'hover:bg-pnl-negative hover:text-ink')}
        onClick={() => void appWindow.close()}
      >
        <XIcon strokeWidth={1.5} aria-hidden />
      </button>
    </div>
  )
}

const ENDED: Record<string, string> = { COMPLETE: 'finished', PAUSED: 'paused', FAILED: 'failed' }
const LIVE = new Set(['QUEUED', 'RUNNING'])

async function notify(title: string, body: string) {
  // What is on screen already says it.
  if (document.visibilityState === 'visible' && document.hasFocus()) return
  // Asked once, on mount: a prompt raised from the tray is held back or unwelcome.
  const { isPermissionGranted, sendNotification } = await import('@tauri-apps/plugin-notification')
  if (!(await isPermissionGranted())) return
  sendNotification({ title, body })
}

export function DesktopNotifications() {
  // The same queries the Tasks page and the header read, so this costs nothing extra on screen.
  const tasks = useQuery({ queryKey: ['lab-tasks'], queryFn: labTasks.list })
  useRefetchOn('studies', ['lab-tasks'], 2_000)
  const engine = useQuery({ queryKey: ['simulations', 'engine'], queryFn: simulations.engine })
  useRefetchOn('simulations', ['simulations', 'engine'], 2_000)
  const update = useUpdateStatus()

  // Asked now, while the window is in front: macOS may hold back a prompt raised from the tray.
  useEffect(() => {
    void import('@tauri-apps/plugin-notification').then(async (plugin) => {
      if (!(await plugin.isPermissionGranted())) await plugin.requestPermission()
    })
  }, [])

  const statuses = useRef<Map<number, string> | null>(null)
  useEffect(() => {
    const list = tasks.data?.tasks
    if (!list) return
    const before = statuses.current
    statuses.current = new Map(list.map((t) => [t.id, t.status]))
    if (!before) return
    // A task new since the last look may have started and ended between two of them.
    const ended = list.filter(
      (task) => ENDED[task.status] && (!before.has(task.id) || LIVE.has(before.get(task.id) ?? '')),
    )
    const name = (task: (typeof list)[number]) => task.name || task.templateName || task.labName
    const [only] = ended
    if (ended.length === 1 && only) {
      void notify(`${name(only)} ${ENDED[only.status]}`, only.message || only.labName)
    } else if (ended.length > 1) {
      // One notice for a batch, as when several tasks end in the same moment.
      const shown = ended.slice(0, 3).map((t) => `${name(t)} ${ENDED[t.status]}`)
      const more = ended.length > 3 ? `, and ${ended.length - 3} more` : ''
      void notify(`${ended.length} tasks ended`, `${shown.join(', ')}${more}.`)
    }
  }, [tasks.data])

  const flags = useRef<{ limit: boolean; session: boolean } | null>(null)
  useEffect(() => {
    if (!engine.data) return
    const now = { limit: engine.data.dailyLimitHit, session: engine.data.sessionLost }
    const before = flags.current
    flags.current = now
    if (!before) return
    if (now.limit && !before.limit)
      void notify(
        "BRAIN's daily simulation limit is reached",
        'Queued simulations wait for the reset at midnight US Eastern.',
      )
    if (now.session && !before.session)
      void notify(
        'BRAIN is not accepting your session',
        'Sending is paused until the session is renewed or you sign in again.',
      )
  }, [engine.data])

  const offered = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (!update.data) return
    const now = update.data.available ? update.data.latest : null
    const before = offered.current
    offered.current = now
    if (before !== undefined && now && now !== before)
      void notify(`Alpha Harness ${now} is available`, 'Open Alpha Harness to install it.')
  }, [update.data])

  return null
}
