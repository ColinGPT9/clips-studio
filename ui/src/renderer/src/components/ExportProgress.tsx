import { useEffect, useState, type ReactNode } from 'react'
import { errorText } from '../lib/api'
import { t } from '../lib/i18n'
import { exportProgress, formatBytes, isActive, tally, type ExportBatch } from '../lib/exportJobs'
import type { ExportJob } from '../lib/types'

/** An export made in the background (Settings → Export set to anything but
 *  Original): how far along it is, with Cancel, while it runs; then what came
 *  of it. One export for the Export button, several for Export all, which
 *  passes `batch` for its summary.
 *
 *  What is happening is said in one live region that stays in place from
 *  start to finish: "Exporting…", then the result or why it failed. The
 *  percentage is left to the bar (aria-valuenow), so a screen reader isn't
 *  read every step of it. */
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

  let status: ReactNode
  let tone = ''
  let note: string | undefined
  const progress = exportProgress(jobs)
  const pct = Math.round(progress.fraction * 100)
  const stopping = cancelling || active.some((j) => j.cancelling)

  if (running) {
    const waiting = active.every((j) => j.status === 'queued')
    status =
      jobs.length > 1
        ? `${t('Exporting')} ${Math.min(jobs.length, jobs.length - active.length + 1)} ${t('of')} ${jobs.length}` +
          (progress.smaller ? ` · ${t('Making it smaller…')}` : '…')
        : waiting
          ? t('Waiting to export…')
          : progress.smaller
            ? t('Making it smaller…')
            : t('Exporting…')
  } else if (batch) {
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
    tone = s.failed > 0 ? 'text-error' : s.done > 0 ? 'text-accent' : 'text-muted'
    status = parts.join(' ')
  } else {
    const job = jobs[0]
    if (job.status === 'done' && job.result) {
      const r = job.result
      note = [
        r.path,
        r.retried ? t('It came out over the size limit, so it was made again, smaller.') : '',
        r.fell_back_to_cpu ? t('Made on the processor, because the graphics card could not make it.') : ''
      ]
        .filter(Boolean)
        .join('\n')
      tone = 'text-accent'
      status = (
        <>
          {t('Exported')} {r.file} · {formatBytes(r.bytes)} · {r.width}×{r.height}
          {r.over_target && (
            <span
              className="text-warn whitespace-nowrap"
              tabIndex={0}
              title={
                r.over_reason === 'too_small'
                  ? t('Your size limit is too small for this clip to look good at this size and frame rate, so Clips Kitty didn’t squeeze it down to your limit. A smaller Size or frame rate gets closer to your limit.')
                  : t('It came out over your size limit even after it was made again to fit. Try a slightly lower limit.')
              }
            >
              {' · ⚠ '}
              {t('over the size limit')}
            </span>
          )}
        </>
      )
    } else if (job.status === 'failed') {
      tone = 'text-error'
      status = `${t('Export failed:')} ${job.error || t('the engine gave no reason.')}`
    } else {
      tone = 'text-muted'
      status = t('Export cancelled.')
    }
  }

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
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <p className={`text-sm min-w-0 ${running ? 'truncate' : tone}`} aria-live="polite" aria-atomic="true" title={note}>
          {status}
        </p>
        {running && (
          <div className="flex items-center gap-3 shrink-0">
            <span className="text-xs text-muted tabular-nums" aria-hidden="true">
              {pct}%
            </span>
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
        )}
      </div>
      {running && (
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
      )}
      {running && error && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}
