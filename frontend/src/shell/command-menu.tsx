/**
 * ⌘K: jump to any screen, sub-tab or lab. cmdk owns filtering, ranking and keyboard
 * navigation; a Base UI dialog owns focus trap and dismissal. Styled with tokens only.
 */

import { Dialog as BDialog } from '@base-ui/react/dialog'
import { useRouter } from '@tanstack/react-router'
import { Command } from 'cmdk'
import { SearchIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { create } from 'zustand'
import { BACKDROP } from '@/ui/overlay'
import { NAV } from './nav'
import { useSettings } from './settings'

export const useCommandMenu = create<{
  open: boolean
  setOpen: (open: boolean) => void
}>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}))

/** Somewhere to go, or, for what floats over the page rather than replacing it, something to open. */
interface Destination {
  label: string
  href?: string
  run?: () => void
  /** Other words for it, so a search for what it does finds it too. */
  also?: string[]
}

/** What a destination is for, in the words someone looking for it would type. */
const ALSO: Record<string, string[]> = {
  '/dashboard': ["Dispatch Today's Simulations"],
  '/matrix': ['View Live Simulation Matrix'],
  '/pool/submittable': ['Browse Submittable Alphas'],
  '/sync': ['Sync Data Fields with BRAIN'],
  '/ai/assistant': ['Ask AI Assistant'],
  '/data': ['Explore Data Catalog'],
}

/** Shaped like a BRAIN Alpha id: letters and digits, with a digit or a capital past the first. */
const ALPHA_ID = /^(?=[A-Za-z0-9]{7,8}$).(?=.*[0-9A-Z])/

export function CommandMenu() {
  const { open, setOpen } = useCommandMenu()
  const router = useRouter()

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen(!useCommandMenu.getState().open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  const [query, setQuery] = useState('')
  // Each opening starts empty, as cmdk's own input would.
  useEffect(() => {
    if (!open) setQuery('')
  }, [open])
  const typed = query.trim()
  const groups = useMemo<[string, Destination[]][]>(
    () => [
      ...(ALPHA_ID.test(typed)
        ? [
            [
              'Alpha',
              [
                { label: `Open Alpha ${typed}`, href: `/alpha/${typed}` },
                {
                  label: `Settings Sampler on ${typed}`,
                  href: `/tools/settings-sampler?alpha=${typed}`,
                },
                {
                  label: `Fields Sampler on ${typed}`,
                  href: `/tools/fields-sampler?alpha=${typed}`,
                },
                {
                  label: `Correlation Breaker on ${typed}`,
                  href: `/tools/correlation-breaker?alpha=${typed}`,
                },
              ],
            ] as [string, Destination[]],
          ]
        : []),
      ['Actions', [{ label: 'Open Settings', run: () => useSettings.getState().setOpen(true) }]],
      [
        'Screens & Labs',
        NAV.flatMap((item) => [
          { label: item.label, href: item.to, also: ALSO[item.to] ?? [] },
          ...('tabs' in item ? item.tabs : []).map((t) => {
            const href = `${item.to}/${t.tab}`
            return { label: `${item.label} · ${t.label}`, href, also: ALSO[href] ?? [] }
          }),
        ]),
      ],
    ],
    [typed],
  )

  const go = (destination: Destination) => {
    setOpen(false)
    if (destination.run) destination.run()
    else if (destination.href) void router.navigate({ href: destination.href })
  }

  return (
    <BDialog.Root open={open} onOpenChange={(next) => setOpen(next)}>
      <BDialog.Portal>
        <BDialog.Backdrop className={BACKDROP} />
        <BDialog.Popup className="fixed top-[15vh] left-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 overflow-hidden rounded-lg border border-hairline-strong bg-surface-3 shadow-float outline-none">
          <BDialog.Title className="sr-only">Go to</BDialog.Title>
          <Command
            label="Go to a screen or lab"
            loop
            className="flex flex-col [&_[cmdk-group-heading]]:eyebrow [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1"
          >
            <div className="flex items-center gap-2 border-b border-hairline px-3 transition-colors focus-within:border-ink-subtle">
              <SearchIcon className="size-4 shrink-0 text-ink-subtle" aria-hidden />
              <Command.Input
                autoFocus
                value={query}
                onValueChange={setQuery}
                placeholder="Go to a screen or lab…"
                className="h-11 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-ink-subtle"
              />
            </div>
            <Command.List className="max-h-80 overflow-y-auto p-1">
              <Command.Empty className="px-3 py-6 text-center text-body text-ink-subtle">
                No destination matches.
              </Command.Empty>
              {groups.map(
                ([heading, destinations]) =>
                  destinations.length > 0 && (
                    <Command.Group key={heading} heading={heading}>
                      {destinations.map((destination) => (
                        <Command.Item
                          key={destination.href ?? destination.label}
                          // Unique across groups: cmdk selects by value, and a screen offered as
                          // both a quick action and a destination lit up twice.
                          value={destination.label}
                          keywords={[
                            ...(destination.href ? [destination.href] : []),
                            ...(destination.also ?? []),
                          ]}
                          onSelect={() => go(destination)}
                          className="flex h-8 cursor-default items-center rounded-sm px-2 text-body text-ink-muted select-none data-[selected=true]:bg-surface-4 data-[selected=true]:text-ink"
                        >
                          <span className="truncate">{destination.label}</span>
                        </Command.Item>
                      ))}
                    </Command.Group>
                  ),
              )}
            </Command.List>
          </Command>
        </BDialog.Popup>
      </BDialog.Portal>
    </BDialog.Root>
  )
}
