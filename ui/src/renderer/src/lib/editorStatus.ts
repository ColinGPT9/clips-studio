/** Whether Clips Kitty Editor is there (editor suite K2, E1 and U2).
 *
 *  Read once when the app starts (GET /editor/status). While it is not found,
 *  or the window has no editor bridge (the browser shim, an older preload),
 *  nothing of the editor is shown anywhere: the app looks exactly as before. */
import { useEffect, useState } from 'react'
import { editorApi } from './api'

let found = false
let asked = false
const listeners = new Set<(on: boolean) => void>()

/** Ask the engine once; asked again only if the engine was not up yet. */
export function loadEditorStatus(): void {
  if (asked || !window.studio?.editor) return
  asked = true
  const ask = (tries: number): void => {
    editorApi
      .status()
      .then((s) => {
        found = s.found === true
        for (const fn of listeners) fn(found)
      })
      .catch(() => {
        // The engine is still starting: ask again a little later.
        if (tries > 0) setTimeout(() => ask(tries - 1), 3000)
      })
  }
  ask(20)
}

export function editorFound(): boolean {
  return found && !!window.studio?.editor
}

/** True once the engine said the editor is found. */
export function useEditorFound(): boolean {
  const [on, setOn] = useState(editorFound())
  useEffect(() => {
    const fn = (value: boolean): void => setOn(value && !!window.studio?.editor)
    listeners.add(fn)
    fn(found)
    return () => {
      listeners.delete(fn)
    }
  }, [])
  return on
}
