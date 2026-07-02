import { useEffect, useState } from 'react'
import { applyEvent, emptyProgress, etaSeconds, formatEta, progressStore } from '../lib/jobProgress'
import { useEvents } from '../lib/useEvents'

/** Live progress for the running job: stage label, percent bar, and an
 *  estimated time remaining that ticks down every second. Hidden when idle. */
export default function ProcessingBar(): JSX.Element | null {
  const [progress, setProgress] = useState(progressStore.current)
  const [now, setNow] = useState(Date.now())

  useEvents((e) => {
    progressStore.current = applyEvent(progressStore.current, e)
    setProgress(progressStore.current)
  })

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!progress.active) return null
  const eta = etaSeconds(progress, now)
  const pct = Math.round(progress.fraction * 100)

  return (
    <div className="card space-y-2" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium truncate">
          {progress.title ? `${progress.title} — ` : ''}
          {progress.label || 'Working…'}
        </p>
        <p className="text-xs text-muted tabular-nums shrink-0">
          {pct}% · {eta !== null ? `~${formatEta(eta)} left` : 'estimating time…'}
        </p>
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
