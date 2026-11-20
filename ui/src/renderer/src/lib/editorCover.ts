/** The editor is always drawn over the web page (editor suite K2, U3).
 *
 *  Clips Kitty Editor is a native child window laid over the Editor page's
 *  area, so anything of the app's own drawn into that area (a dialog, a
 *  popover, a card) would sit UNDER it, invisible and unusable. Each such
 *  thing holds the cover while it is open: `useEditorCover(open)`. While any
 *  hold is taken, the editor is hidden and the area shows the page's plain
 *  background; when the last one lets go, it comes back as it was.
 *
 *  Every thing of the app that can be drawn over the area while the Editor
 *  page is up, and its rule, is listed in docs/editor-suite/api/k2-editor.md
 *  ("The editor is always drawn over the web page");
 *  tests/test_ui_editor_page.py keeps the list honest. */
import { useEffect, useState } from 'react'

let holds = 0
const listeners = new Set<(n: number) => void>()

function emit(): void {
  for (const fn of listeners) fn(holds)
}

/** Hold the cover while `active` is true. */
export function useEditorCover(active: boolean): void {
  useEffect(() => {
    if (!active) return
    holds += 1
    emit()
    return () => {
      holds -= 1
      emit()
    }
  }, [active])
}

/** How many holds are taken now (0: the editor may be shown). */
export function useEditorCoverHolds(): number {
  const [n, setN] = useState(holds)
  useEffect(() => {
    listeners.add(setN)
    setN(holds)
    return () => {
      listeners.delete(setN)
    }
  }, [])
  return n
}
