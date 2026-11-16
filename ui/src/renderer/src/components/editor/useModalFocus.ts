/** Keeps the keyboard inside a dialog while it is open (the new editor's
 *  Export dialog and the shape box of "Edit whole video" and "Import a video
 *  to edit"):
 *
 *  - everything else on the page is made inert, so neither Tab, a click nor
 *    a screen reader reaches the screen behind, and its shortcuts don't run;
 *    what was inert before stays so when the dialog closes;
 *  - Tab and Shift+Tab go round the dialog's own controls;
 *  - Esc closes it wherever the focus is (handled on window, first);
 *  - when it closes, the focus goes back to where it was (the button that
 *    opened it). */
import { useEffect, useRef, useState, type RefObject } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])'

function focusables(box: HTMLElement): HTMLElement[] {
  return Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.closest('[inert]') && (el.offsetParent !== null || el === document.activeElement)
  )
}

type Inertable = HTMLElement & { inert: boolean }

/** `root`: the dialog's outermost element (its backdrop); `box`: the part
 *  that holds its controls (the same element will do). `onEscape` runs on
 *  Esc; pass null while Esc must not close it (while it is working). */
export function useModalFocus(
  root: RefObject<HTMLElement>,
  box: RefObject<HTMLElement>,
  onEscape: (() => void) | null
): void {
  const escape = useRef(onEscape)
  escape.current = onEscape
  // Read on the first render, before a control inside takes the focus
  // (autoFocus runs before effects do): the button that opened the dialog.
  const [opener] = useState(() => document.activeElement as HTMLElement | null)

  useEffect(() => {
    const overlay = root.current
    if (!overlay) return
    const before = opener
    // Everything outside the dialog, at every level up to <body>, made inert.
    const changed: Inertable[] = []
    for (let node: HTMLElement | null = overlay; node && node !== document.body; node = node.parentElement) {
      const parent: HTMLElement | null = node.parentElement
      if (!parent) break
      const siblings = Array.from(parent.children) as Inertable[]
      for (const sibling of siblings) {
        if (sibling === node || sibling.inert || sibling.tagName === 'SCRIPT') continue
        sibling.inert = true
        changed.push(sibling)
      }
    }
    if (!overlay.contains(document.activeElement)) {
      const first = box.current ? focusables(box.current)[0] : undefined
      first?.focus()
    }

    const onKey = (e: KeyboardEvent): void => {
      const area = box.current
      if (!area) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        escape.current?.()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusables(area)
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const at = document.activeElement as HTMLElement | null
      const inside = at !== null && area.contains(at)
      if (e.shiftKey && (!inside || at === first)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (!inside || at === last)) {
        e.preventDefault()
        first.focus()
      }
    }
    // Focus that lands outside anyway (the window regains it, say) comes back.
    const onFocus = (e: FocusEvent): void => {
      const area = box.current
      if (!area || area.contains(e.target as Node)) return
      focusables(area)[0]?.focus()
    }
    window.addEventListener('keydown', onKey, true)
    document.addEventListener('focusin', onFocus, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      document.removeEventListener('focusin', onFocus, true)
      for (const el of changed) el.inert = false
      if (before && document.contains(before) && !before.closest('[inert]')) before.focus()
    }
    // Set up once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
