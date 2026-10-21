/** Persisted drafts kept in step across browser tabs. */

/**
 * Takes up what another tab saved as soon as it lands. Without it, the next change here writes
 * this tab's older copy over theirs, and the draft written in the other tab is gone.
 */
export function followOtherTabs(store: {
  persist: { getOptions: () => { name?: string }; rehydrate: () => Promise<void> | void }
}): void {
  window.addEventListener('storage', (event) => {
    if (event.key === store.persist.getOptions().name) void store.persist.rehydrate()
  })
}
