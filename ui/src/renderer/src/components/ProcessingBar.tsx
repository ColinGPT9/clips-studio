import { useEffect, useRef, useState } from 'react'
import { applyEvent, emptyProgress, etaSeconds, formatEta, progressStore } from '../lib/jobProgress'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'

// How long the event stream may go quiet before we stop believing it and ask
// the server directly. Long enough that a slow stage (a big download, a long
// analysis chunk) never triggers a pointless check.
const SILENCE_BEFORE_RECHECK_MS = 45_000
const RECHECK_EVERY_MS = 10_000

/** Live progress for the running job: stage label, percent bar, an estimated
 *  time remaining that ticks down, and a Cancel button. Hidden when idle. */
export default function ProcessingBar(): JSX.Element | null {
  const [progress, setProgress] = useState(progressStore.current)
  const [now, setNow] = useState(Date.now())
  const [cancelling, setCancelling] = useState(false)
  const lastEventAt = useRef(Date.now())

  useEvents((e) => {
    lastEventAt.current = Date.now()
    progressStore.current = applyEvent(progressStore.current, e)
    setProgress(progressStore.current)
    if (e.type === 'job' && ['done', 'failed', 'cancelled'].includes(e.status ?? ''))
      setCancelling(false)
  })

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  // The bar used to be cleared ONLY by a terminal event. If the socket was
  // down at the moment the job finished — a reconnect, a reload, the backend
  // restarting — that event never arrived and the bar claimed to be
  // processing forever, across page switches, until the app was restarted.
  // So when events go quiet, stop trusting them and ask what is really
  // running. The server is the authority; the event stream is just faster.
  useEffect(() => {
    if (!progress.active) return
    const id = setInterval(() => {
      if (Date.now() - lastEventAt.current < SILENCE_BEFORE_RECHECK_MS) return
      void api
        .jobs()
        .then((jobs) => {
          const live = jobs.some((j) => j.status === 'running' || j.status === 'queued')
          if (!live) {
            progressStore.current = { ...emptyProgress }
            setProgress(progressStore.current)
            setCancelling(false)
          } else {
            // Something IS running — the socket just missed events. Reset the
            // clock so we re-check on the next quiet stretch, not every tick.
            lastEventAt.current = Date.now()
          }
        })
        .catch(() => {
          /* backend unreachable — say nothing rather than wrongly clear */
        })
    }, RECHECK_EVERY_MS)
    return () => clearInterval(id)
  }, [progress.active])

  if (!progress.active) return null
  const eta = etaSeconds(progress, now)
  const pct = Math.round(progress.fraction * 100)

  const cancel = async (): Promise<void> => {
    if (!progress.videoId) return
    setCancelling(true)
    try {
      await api.cancelProcessing(progress.videoId)
    } catch {
      setCancelling(false)
    }
  }

  return (
    <div className="card space-y-2" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium truncate">
          {progress.title ? `${progress.title} — ` : ''}
          {progress.label || 'Working…'}
        </p>
        <div className="flex items-center gap-3 shrink-0">
          <p className="text-xs text-muted tabular-nums">
            {pct}% ·{' '}
            {eta !== null
              ? `Estimated time: ~${formatEta(eta)} left`
              : 'Estimated time: calculating…'}
          </p>
          {/* Reachable from wherever the bar is shown: while a long batch
              runs, the queue is the screen that answers "what's left?" */}
          <button
            className="btn-ghost !px-2.5 !py-1 text-xs"
            onClick={() => window.dispatchEvent(new CustomEvent('open-queue'))}
          >
            Queue
          </button>
          <button
            className="btn-ghost !px-2.5 !py-1 text-xs"
            onClick={cancel}
            disabled={cancelling || !progress.videoId}
          >
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        </div>
      </div>
      <div
        className="h-2 rounded-full bg-raised overflow-hidden"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Video processing progress"
      >
        <div
          className="h-full bg-accent rounded-full transition-[width] duration-700"
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>
    </div>
  )
}
