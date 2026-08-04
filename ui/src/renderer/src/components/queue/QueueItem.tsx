import { useState } from 'react'
import { api } from '../../lib/api'
import type { QueueJob } from '../../lib/types'
import type { JobProgress } from '../../lib/jobProgress'
import { etaSeconds, formatEta } from '../../lib/jobProgress'
import { describeOptions, jobLabel, ranFor, sourceOf } from '../../lib/queue'
import QueueItemSettings from './QueueItemSettings'
import { t } from '../../lib/i18n'

const ICON: Record<string, string> = {
  running: '▶',
  queued: '○',
  done: '✓',
  failed: '⚠',
  cancelled: '⊘'
}

const TONE: Record<string, string> = {
  running: 'text-accent',
  queued: 'text-muted',
  done: 'text-success',
  failed: 'text-error',
  cancelled: 'text-muted'
}

/** One row of the processing queue.
 *
 *  Presentational: it renders a job and calls back. It never holds queue
 *  state, so the page stays the single place that knows what the queue is. */
export default function QueueItem({
  job,
  index,
  total,
  live,
  now,
  onChanged,
  onOpenInStudio
}: {
  job: QueueJob
  /** 1-based position among waiting videos; 0 when not applicable. */
  index: number
  total: number
  /** Live progress, only for the running row. */
  live?: JobProgress
  now: number
  onChanged: () => void
  onOpenInStudio?: (videoId: string) => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [log, setLog] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const chips = describeOptions(job.settings)
  const source = sourceOf(job.url)
  const running = job.status === 'running'
  const waiting = job.status === 'queued'
  const eta = running && live ? etaSeconds(live, now) : null
  const took = ranFor(job)

  return (
    <div className={`rounded-lg border px-3 py-2.5 ${running ? 'border-accent/40 bg-accent/5' : 'border-raised/60'}`}>
      <div className="flex items-start gap-3">
        <span className={`shrink-0 mt-0.5 ${TONE[job.status] ?? 'text-muted'}`} aria-hidden>
          {ICON[job.status] ?? '○'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            {waiting && index > 0 && (
              <span className="text-xs text-muted tabular-nums shrink-0">#{index}</span>
            )}
            <p className="font-medium truncate min-w-0" title={job.url || jobLabel(job)}>
              {jobLabel(job)}
            </p>
            {source && <span className="text-xs text-muted shrink-0">{source}</span>}
            {job.channel && <span className="text-xs text-muted shrink-0">· {job.channel}</span>}
          </div>

          {running && live && (
            <div className="mt-1.5">
              <div className="flex items-baseline gap-2 text-sm">
                <span className="text-accent">{live.label || t('Starting…')}</span>
                <span className="text-muted tabular-nums">
                  {Math.round(live.fraction * 100)}%
                </span>
                {eta !== null && (
                  <span className="text-muted tabular-nums">· {formatEta(eta)} {t('left')}</span>
                )}
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-raised overflow-hidden">
                <div
                  className="h-full bg-accent transition-[width] duration-500"
                  style={{ width: `${Math.round(live.fraction * 100)}%` }}
                />
              </div>
            </div>
          )}

          {waiting && (
            <p className="text-sm text-muted mt-0.5">
              {job.interrupted
                ? t('Restarted after an interruption — resumes from its last finished stage')
                : t('Waiting')}
            </p>
          )}

          {took && !running && (
            <p className="text-sm text-muted mt-0.5">
              {job.status === 'done' ? t('Finished in') : t('Ran for')} {took}
              {job.attempts > 1 && ` · ${job.attempts} ${t('attempts')}`}
            </p>
          )}

          {job.error && (
            <p className="text-sm text-error mt-1 break-words">{job.error}</p>
          )}

          {chips.length > 0 && (
            <div className="flex gap-1.5 flex-wrap mt-1.5">
              {chips.map((c) => (
                <span
                  key={c}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-raised text-muted"
                >
                  {c}
                </span>
              ))}
            </div>
          )}

          {log !== null && (
            <pre className="mt-2 max-h-64 overflow-auto text-[11px] leading-relaxed bg-base/60 rounded p-2 whitespace-pre-wrap break-words">
              {log || t('No log was recorded for this run.')}
            </pre>
          )}

          {open && waiting && <QueueItemSettings job={job} onSaved={onChanged} />}
          {error && <p className="text-sm text-error mt-1">{error}</p>}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {waiting && (
            <>
              <button
                className="btn-ghost !px-2 !py-1"
                title={t('Run this one next')}
                aria-label={t('Move to the front of the queue')}
                disabled={busy || index <= 1}
                onClick={() => run(() => api.moveJob(job.id, { to: 'top' }))}
              >
                ⇈
              </button>
              <button
                className="btn-ghost !px-2 !py-1"
                title={t('Move up')}
                aria-label={t('Move up')}
                disabled={busy || index <= 1}
                onClick={() => run(() => api.moveJob(job.id, { delta: -1 }))}
              >
                ▲
              </button>
              <button
                className="btn-ghost !px-2 !py-1"
                title={t('Move down')}
                aria-label={t('Move down')}
                disabled={busy || index >= total}
                onClick={() => run(() => api.moveJob(job.id, { delta: 1 }))}
              >
                ▼
              </button>
              {job.type === 'process' && (
                <button
                  className="btn-ghost !px-2 !py-1"
                  aria-expanded={open}
                  title={t('Settings for this video')}
                  onClick={() => setOpen(!open)}
                >
                  ⚙
                </button>
              )}
            </>
          )}

          {running && (
            <button
              className="btn-ghost !px-2 !py-1"
              disabled={busy || !job.video_id}
              title={t('Stop this video (finishes the clip it is on)')}
              onClick={() => run(() => api.cancelProcessing(job.video_id))}
            >
              {busy ? t('Cancelling…') : t('Cancel')}
            </button>
          )}

          {(job.status === 'failed' || job.status === 'cancelled') && (
            <button
              className="btn-ghost !px-2 !py-1"
              disabled={busy}
              title={t('Put this video back in the queue with the same settings')}
              onClick={() => run(() => api.retryJob(job.id))}
            >
              {t('Retry')}
            </button>
          )}

          {job.status === 'done' && job.video_id && onOpenInStudio && (
            <button
              className="btn-ghost !px-2 !py-1"
              title={t('Open the clips from this video')}
              onClick={() => onOpenInStudio(job.video_id)}
            >
              {t('Clips')}
            </button>
          )}

          {!running && job.log_path && (
            <button
              className="btn-ghost !px-2 !py-1"
              title={t('Show what happened during this run')}
              onClick={async () => {
                if (log !== null) {
                  setLog(null)
                  return
                }
                try {
                  const res = await api.jobLog(job.id)
                  setLog(res.log)
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e))
                }
              }}
            >
              {t('Log')}
            </button>
          )}

          {!running && (
            <button
              className="btn-ghost !px-2 !py-1"
              disabled={busy}
              title={t('Remove from the queue')}
              aria-label={t('Remove from the queue')}
              onClick={() => run(() => api.deleteJob(job.id))}
            >
              ✕
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
