import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import { t } from '../lib/i18n'
import {
  remakeBlocked,
  remakeCountTitle,
  remakeOptions,
  remakeStart,
  type SettingsPatch
} from '../lib/remake'
import type { RemakePreview } from '../lib/types'
import QueueItemSettings from './queue/QueueItemSettings'
import { seedOptions } from './queue/AddVideos'

/** What Tab can reach inside the dialog. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Make clips again (#190): one video's clips made again with other
 *  settings, without adding the video again.
 *
 *  It opens with the settings the video's clips were last made with, in the
 *  Queue page's own per-video panel, and says how many clips would be
 *  replaced and how many kept. Every setting the run will use that changes
 *  what it picks is on the panel: a video made as one whole clip shows that
 *  as a switch to keep or turn off (offerWhole). Make clips again queues the
 *  video and starts a stopped queue, as Add videos does; the usual progress
 *  bar follows it, and the Library reads its clips again when it's done, and
 *  says what the run did. Clips someone used
 *  are kept; the others are replaced only once the new clips are ready
 *  (docs/editor-suite/api/step2-remake.md). */
export default function RemakeDialog({
  videoId,
  title,
  onClose,
  onQueued
}: {
  videoId: string
  title: string
  onClose: () => void
  onQueued: () => void
}): JSX.Element {
  const [preview, setPreview] = useState<RemakePreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    let current = true
    api
      .remakePreview(videoId)
      .then((p) => current && setPreview(p))
      .catch((e) => current && setLoadError(errorText(e)))
    return () => {
      current = false
    }
  }, [videoId])

  // The keyboard starts in the dialog, Tab and Shift+Tab stay in it, and
  // Escape leaves it. Closed any way, the keyboard goes back to what opened
  // it (the Library's Make clips again… button), so a keyboard user keeps
  // their place.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    box.current?.focus()
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        close.current()
        return
      }
      if (e.key !== 'Tab' || !box.current) return
      const inside = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.getClientRects().length > 0
      )
      const active = document.activeElement
      if (inside.length === 0) {
        e.preventDefault()
        box.current.focus()
        return
      }
      const first = inside[0]
      const last = inside[inside.length - 1]
      const lost = !(active instanceof Node) || !box.current.contains(active)
      if (e.shiftKey && (lost || active === first || active === box.current)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (lost || active === last)) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('keydown', key)
      if (opener && opener.isConnected && !(opener as HTMLButtonElement).disabled) opener.focus()
    }
  }, [])

  // Read once, when the preview comes: the panel keeps its own state after.
  const start = useMemo(() => (preview ? remakeStart(preview, seedOptions) : null), [preview])

  const queue = async (patch: SettingsPatch): Promise<void> => {
    if (!start) return
    try {
      await api.remakeVideo(videoId, remakeOptions(start, patch))
    } catch (e) {
      throw new Error(errorText(e)) // the engine's own sentence, shown beside the buttons
    }
    // A stopped queue starts, as it does when a video is added: the person
    // asked for these clips now. A queue already running just takes it.
    const now = await api.queue().catch(() => null)
    if (now?.paused) await api.resumeQueue().catch(() => undefined)
  }

  const counts = preview?.counts
  // Waiting or being made in the queue since the Library last looked, say:
  // the reason, rather than settings the engine would refuse to queue.
  const why = preview ? remakeBlocked(preview, t) : null

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Make clips again')}
      onClick={onClose}
    >
      <div
        ref={box}
        tabIndex={-1}
        className="card w-full max-w-3xl space-y-3 max-h-[85vh] overflow-y-auto outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="font-semibold text-lg">{t('Make clips again')}</h3>
          <p className="text-xs text-muted mt-1 truncate" title={title}>
            {title}
          </p>
        </div>

        {counts && counts.total > 0 && (
          <p className="text-sm" title={remakeCountTitle(counts, t)}>
            <span className="text-accent font-medium tabular-nums">{counts.replaced}</span>{' '}
            {t('will be replaced')}, <span className="font-medium tabular-nums">{counts.kept}</span>{' '}
            {t('kept')}
          </p>
        )}

        {start && why === null ? (
          <QueueItemSettings
            job={{ id: preview?.from_job ?? 0, settings: start }}
            save={queue}
            onSaved={onQueued}
            saveLabel="Make clips again"
            savingLabel="Starting…"
            onCancel={onClose}
            noLongform
            offerWhole
          />
        ) : (
          <div className="flex items-center gap-3">
            <p
              className={`text-sm flex-1 ${why ? 'text-warn' : loadError ? 'text-error' : 'text-muted'}`}
            >
              {why ?? loadError ?? t('Loading…')}
            </p>
            <button className="btn-ghost" onClick={onClose}>
              {t('Cancel')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
