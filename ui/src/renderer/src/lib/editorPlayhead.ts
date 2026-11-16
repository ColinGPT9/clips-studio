/** The playhead, outside React state: it moves every frame while playing, and
 *  only what shows it (the time readout, the timeline's line) should draw
 *  again, not the whole editor. */
import { useSyncExternalStore } from 'react'

export interface PlayheadStore {
  get: () => number
  set: (tUs: number) => void
  subscribe: (fn: () => void) => () => void
  playing: () => boolean
  setPlaying: (on: boolean) => void
}

export function createPlayhead(): PlayheadStore {
  let t = 0
  let playing = false
  const listeners = new Set<() => void>()
  const emit = (): void => listeners.forEach((fn) => fn())
  return {
    get: () => t,
    set: (tUs) => {
      const next = Math.max(0, Math.round(tUs))
      if (next === t) return
      t = next
      emit()
    },
    subscribe: (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    playing: () => playing,
    setPlaying: (on) => {
      if (on === playing) return
      playing = on
      emit()
    }
  }
}

export function usePlayheadTime(store: PlayheadStore): number {
  return useSyncExternalStore(store.subscribe, store.get)
}

export function usePlaying(store: PlayheadStore): boolean {
  return useSyncExternalStore(store.subscribe, store.playing)
}
