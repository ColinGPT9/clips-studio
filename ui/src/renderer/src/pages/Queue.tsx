import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { QueueJob, QueueSnapshot, StudioEvent } from '../lib/types'
import { applyEvent, progressStore } from '../lib/jobProgress'
import { formatQueueEta, queueEta, remainingCount } from '../lib/queue'
import { useEvents } from '../lib/useEvents'
import AddVideos from '../components/queue/AddVideos'
import QueueItem from '../components/queue/QueueItem'
import { t } from '../lib/i18n'

/** The processing queue.
 *
 *  The point of this screen is to make a long batch survivable: queue ten
 *  videos, each with its own settings, press start, and leave. It is the only
 *  place that fetches or mutates queue state — the row components take a job
 *  and call back, so queue rules never end up spread across the UI.
 *
 *  The server is the authority. Events say "something changed, re-read it"
 *  rather than carrying deltas, and nothing here is applied optimistically: a
 *  reorder that only LOOKS applied because it raced the worker claiming the
 *  head of the queue is exactly the bug this screen exists to avoid. */
export default function Queue({
  onOpenInStudio
}: {
  onOpenInStudio?: (videoId: string) => void
}): JSX.Element {
  const [snapshot, setSnapshot] = useState<QueueSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  // Live progress for the running video, shared with the global bar so both
  // read the same fraction rather than each folding events their own way.
  const [live, setLive] = useState(progressStore.current)
  const [now, setNow] = useState(Date.now())
  const inFlight = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      setSnapshot(await api.queue())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      inFlight.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (e.type === 'progress' || e.type === 'job') {
      progressStore.current = applyEvent(progressStore.current, e)
      setLive(progressStore.current)
    }
    if (e.type === 'queue' || e.type === 'job') void refresh()
  })

  // The ETA counts down between events, and a long stage can be quiet for
  // minutes — without a clock the estimate would look frozen.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  // Belt-and-braces against a dropped WebSocket, same reasoning as the global
  // progress bar: the queue must not look stalled because a socket died.
  useEffect(() => {
    const id = setInterval(() => void refresh(), 20000)
    return () => clearInterval(id)
  }, [refresh])

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const paused = snapshot?.paused ?? false
  const remaining = remainingCount(snapshot)
  const eta = queueEta(snapshot, live, now)
  const confident = snapshot?.estimate.confident ?? false

  const section = (
    title: string,
    jobs: QueueJob[],
    opts?: { action?: JSX.Element; empty?: string; numbered?: boolean }
  ): JSX.Element | null => {
    if (jobs.length === 0 && !opts?.empty) return null
    return (
      <section className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
            {t(title)} {jobs.length > 0 && <span className="tabular-nums">({jobs.length})</span>}
          </h2>
          <div className="ml-auto">{opts?.action}</div>
        </div>
        {jobs.length === 0 ? (
          <p className="text-sm text-muted">{t(opts?.empty ?? '')}</p>
        ) : (
          <div className="space-y-2">
            {jobs.map((job, i) => (
              <QueueItem
                key={job.id}
                job={job}
                index={opts?.numbered ? i + 1 : 0}
                total={jobs.length}
                live={job.status === 'running' ? live : undefined}
                now={now}
                onChanged={refresh}
                onOpenInStudio={onOpenInStudio}
              />
            ))}
          </div>
        )}
      </section>
    )
  }

  return (
    <div className="p-6 space-y-5 max-w-4xl w-full">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-xl font-bold">{t('Processing queue')}</h1>
        <p className="text-sm text-muted">
          {t('Queue up a batch, then leave Clips Studio running — it works through them on its own.')}
        </p>
      </div>

      <div className="card flex items-center gap-4 flex-wrap">
        <div className="min-w-0">
          <p className="text-lg font-semibold tabular-nums">
            {remaining === 0
              ? t('Nothing left to process')
              : `${remaining} ${remaining === 1 ? t('video remaining') : t('videos remaining')}`}
          </p>
          {remaining > 0 && eta !== null && (
            <p className="text-sm text-muted">
              {confident ? t('About') : t('Roughly')} {formatQueueEta(eta)} {t('to go')}
              {!confident && ` · ${t('the estimate sharpens after a few videos')}`}
            </p>
          )}
          {paused && (
            <p className="text-sm text-warn mt-0.5">
              {snapshot && snapshot.processing.length > 0
                ? t('Paused — the current video finishes, then the queue stops.')
                : t('Paused — nothing new will start until you press Start queue.')}
            </p>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <button className="btn-ghost" onClick={() => setAdding(!adding)} aria-expanded={adding}>
            {adding ? t('Close') : t('+ Add videos')}
          </button>
          {paused ? (
            <button className="btn-accent" disabled={busy} onClick={() => act(api.resumeQueue)}>
              ▶ {t('Start queue')}
            </button>
          ) : (
            <button
              className="btn-ghost"
              disabled={busy || remaining === 0}
              onClick={() => act(api.pauseQueue)}
              title={t('Finish the current video, then stop. The queue is kept.')}
            >
              ⏸ {t('Pause queue')}
            </button>
          )}
        </div>
      </div>

      {adding && <AddVideos onAdded={refresh} />}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}

      {snapshot && (
        <>
          {section('Processing', snapshot.processing, {
            empty: paused ? 'Nothing is running.' : 'Nothing is running right now.'
          })}
          {section('Up next', snapshot.queued, {
            numbered: true,
            empty: 'Nothing waiting. Add videos above to queue a batch.',
            action:
              snapshot.queued.length > 0 ? (
                <button
                  className="btn-ghost !px-2 !py-1 text-xs"
                  disabled={busy}
                  onClick={() => act(() => api.clearQueue('queued'))}
                >
                  {t('Clear waiting')}
                </button>
              ) : undefined
          })}
          {section('Failed', snapshot.failed, {
            action: (
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                disabled={busy}
                onClick={() => act(() => api.clearQueue('failed'))}
              >
                {t('Clear failed')}
              </button>
            )
          })}
          {section('Completed', snapshot.completed, {
            action: (
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                disabled={busy}
                onClick={() => act(() => api.clearQueue('completed'))}
              >
                {t('Clear completed')}
              </button>
            )
          })}
        </>
      )}

      {!snapshot && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}
    </div>
  )
}
