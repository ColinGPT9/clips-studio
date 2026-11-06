import { useEffect, useState } from 'react'
import { errorText } from '../lib/api'
import { t } from '../lib/i18n'
import { formatBytes, isActive, overallFraction, tally, type ExportBatch } from '../lib/exportJobs'
import type { ExportJob } from '../lib/types'

/** An export made in the background (Settings → Export set to anything but
 *  Original): how far along it is, with Cancel, while it runs; then what came
 *  of it. One export for the Export button, several for Export all, which
 *  passes `batch` for its summary. */
export default function ExportProgress({
  jobs,
  onCancel,
  batch
}: {
  jobs: ExportJob[]
  onCancel: (jobs: ExportJob[]) => Promise<void>
  batch?: Omit<ExportBatch, 'keys'>
}): JSX.Element | null {
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState('')
  const active = jobs.filter(isActive)
  const running = active.length > 0

  useEffect(() => {
    if (!running) setCancelling(false)
  }, [running])

  if (jobs.length === 0) return null

  if (running) {
    const pct = Math.round(overallFraction(jobs) * 100)
    const stopping = cancelling || active.some((j) => j.cancelling)
    const waiting = active.every((j) => j.status === 'queued')
    const label =
      jobs.length > 1
        ? `${t('Exporting')} ${Math.min(jobs.length, jobs.length - active.length + 1)} ${t('of')} ${jobs.length}…`
        : waiting
          ? t('Waiting to export…')
          : t('Exporting…')
    const cancel = async (): Promise<void> => {
      setCancelling(true)
      setError('')
      try {
        await onCancel(active)
      } catch (e) {
        setError(errorText(e))
        setCancelling(false)
      }
    }
    return (
      <div className="space-y-1.5" aria-live="polite">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm truncate">{label}</p>
          <div className="flex items-center gap-3 shrink-0">
            <span className="text-xs text-muted tabular-nums">{pct}%</span>
            <button
              className="btn-ghost !px-2.5 !py-1 text-xs"
              onClick={() => void cancel()}
              disabled={stopping}
              title={
                jobs.length > 1
                  ? t('Stop the exports that are not finished. Finished files are kept; the unfinished one is deleted.')
                  : t('Stop this export. The unfinished file is deleted.')
              }
            >
              {stopping ? t('Cancelling…') : t('Cancel')}
            </button>
          </div>
        </div>
        <div
          className="h-2 rounded-full bg-raised overflow-hidden"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={t('Export progress')}
        >
          <div
            className="h-full bg-accent rounded-full transition-[width] duration-500"
            style={{ width: `${Math.max(2, pct)}%` }}
          />
        </div>
        {error && <p className="text-xs text-error">{error}</p>}
      </div>
    )
  }

  if (batch) {
    const s = tally(jobs)
    const parts: string[] = []
    if (s.done > 0)
      parts.push(
        `${t('Exported')} ${s.done} ${s.done === 1 ? t('clip') : t('clips')} (${formatBytes(s.bytes)}) ${t('to')} ${batch.folder}.`
      )
    if (batch.starred > 0) parts.push(`${t('Skipped')} ${batch.starred} ${t('already starred as exported.')}`)
    if (batch.missing > 0) parts.push(`${batch.missing} ${t('had no video file to export.')}`)
    if (s.over > 0) parts.push(`${s.over} ${t('came out over the size limit.')}`)
    if (s.cancelled > 0)
      parts.push(s.done + s.failed === 0 ? t('Export cancelled.') : `${s.cancelled} ${t('cancelled.')}`)
    if (s.failed > 0) parts.push(`${s.failed} ${t('failed:')} ${s.firstError}`)
    const tone = s.failed > 0 ? 'text-error' : s.done > 0 ? 'text-accent' : 'text-muted'
    return (
      <p className={`text-sm ${tone}`} aria-live="polite">
        {parts.join(' ')}
      </p>
    )
  }

  const job = jobs[0]
  if (job.status === 'done' && job.result) {
    const r = job.result
    const notes = [
      r.path,
      r.retried ? t('It came out over the size limit, so it was made once more, a little smaller.') : '',
      r.fell_back_to_cpu ? t('Made on the processor, because the graphics card could not make it.') : ''
    ]
    return (
      <p className="text-sm text-accent" aria-live="polite" title={notes.filter(Boolean).join('\n')}>
        {t('Exported')} {r.file} · {formatBytes(r.bytes)} · {r.width}×{r.height}
        {r.over_target && (
          <span
            className="text-warn"
            title={t('It is over your size limit even after a second, smaller try: the limit is too small for a clip this long.')}
          >
            {' · ⚠ '}
            {t('over the size limit')}
          </span>
        )}
      </p>
    )
  }
  if (job.status === 'failed') {
    return (
      <p className="text-sm text-error" aria-live="polite">
        {t('Export failed:')} {job.error || t('the engine gave no reason.')}
      </p>
    )
  }
  return (
    <p className="text-sm text-muted" aria-live="polite">
      {t('Export cancelled.')}
    </p>
  )
}
