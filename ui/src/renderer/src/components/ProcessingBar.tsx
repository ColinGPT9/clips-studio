import { useEffect, useState } from 'react'
import { applyEvent, etaSeconds, formatEta, progressStore } from '../lib/jobProgress'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'

/** Live progress for the running job: stage label, percent bar, an estimated
 *  time remaining that ticks down, and a Cancel button. Hidden when idle. */
export default function ProcessingBar(): JSX.Element | null {
  const [progress, setProgress] = useState(progressStore.current)
  const [now, setNow] = useState(Date.now())
  const [cancelling, setCancelling] = useState(false)

  useEvents((e) => {
    progressStore.current = applyEvent(progressStore.current, e)
    setProgress(progressStore.current)
    if (e.type === 'job' && ['done', 'failed', 'cancelled'].includes(e.status ?? ''))
      setCancelling(false)
  })

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

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
            {pct}% · {eta !== null ? `~${formatEta(eta)} left` : 'estimating time…'}
          </p>
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
