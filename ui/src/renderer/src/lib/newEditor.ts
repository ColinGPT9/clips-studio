/** The "New editor (beta)" switch, and the new editor's other remembered
 *  choices.
 *
 *  Kept in localStorage beside the app's other UI preferences (appearance,
 *  the gaming preview's full screen): it changes what this person's window
 *  shows, nothing the engine does. Off by default; with it off, nothing of the
 *  new editor is shown anywhere. */
import { useEffect, useState } from 'react'

const SWITCH_KEY = 'new-editor-beta'
const PLATFORM_KEY = 'new-editor-platform'
const CHANGED = 'new-editor-changed'
/** Asks the app to open a project in the new editor (App.tsx listens). */
export const OPEN_PROJECT = 'open-editor-project'

export function newEditorOn(): boolean {
  try {
    return localStorage.getItem(SWITCH_KEY) === '1'
  } catch {
    return false
  }
}

export function setNewEditorOn(on: boolean): void {
  try {
    localStorage.setItem(SWITCH_KEY, on ? '1' : '0')
  } catch {
    /* storage refused: the switch stays as it was */
  }
  window.dispatchEvent(new Event(CHANGED))
}

/** Whether the switch is on, following it as Settings changes it. */
export function useNewEditor(): boolean {
  const [on, setOn] = useState(newEditorOn)
  useEffect(() => {
    const read = (): void => setOn(newEditorOn())
    window.addEventListener(CHANGED, read)
    window.addEventListener('storage', read)
    return () => {
      window.removeEventListener(CHANGED, read)
      window.removeEventListener('storage', read)
    }
  }, [])
  return on
}

/** Open a project in the new editor, from anywhere. `notice`: a plain line
 *  the editor shows when it opens (already translated). */
export function openProject(projectId: string, notice?: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_PROJECT, { detail: { projectId, notice: notice ?? '' } }))
}

/** The phone app drawn over the viewer: tiktok, reels, shorts or none. */
export const PLATFORM_CHOICES = ['tiktok', 'reels', 'shorts', 'none'] as const
export type PlatformChoice = (typeof PLATFORM_CHOICES)[number]

export function loadPlatform(): PlatformChoice {
  try {
    const v = localStorage.getItem(PLATFORM_KEY)
    return (PLATFORM_CHOICES as readonly string[]).includes(v ?? '') ? (v as PlatformChoice) : 'none'
  } catch {
    return 'none'
  }
}

export function savePlatform(p: PlatformChoice): void {
  try {
    localStorage.setItem(PLATFORM_KEY, p)
    if (p !== 'none') localStorage.setItem(LAST_APP_KEY, p)
  } catch {
    /* not remembered: still shown now */
  }
}

const LAST_APP_KEY = 'new-editor-last-app'

/** The app last chosen in the picker (TikTok before any): drawn faintly
 *  while a caption or text is dragged with the picker on "No overlay"
 *  (step 9). */
export function lastPlatformApp(): Exclude<PlatformChoice, 'none'> {
  try {
    const v = localStorage.getItem(LAST_APP_KEY)
    return v === 'reels' || v === 'shorts' || v === 'tiktok' ? v : 'tiktok'
  } catch {
    return 'tiktok'
  }
}
